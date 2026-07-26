"""Render a self-contained HTML report for one finished episode.

After a run, ``report.html`` lands next to ``score.json`` / ``traj.md`` and can
be opened straight in a browser — no server. It reads the run dir's
``score.json``, ``cost.json``, and ``transcript.jsonl`` and lays out, in the
GitHub-dark style of the AGF reports:

* a header with the bug / model / sanitizer / language tags,
* hero stats (tier score, turns, tool calls, cost),
* the capability ladder (reach -> crash -> differential -> class -> site),
* cards for token / cost breakdown, per-tool call counts, and run metadata,
* the full trajectory table (every tool call, its argument and result, with
  the run_poc_on_harness() calls and the faulting call highlighted).

Everything the report shows is answer-free: it is the record of what the agent
did, which never includes the oracle's PoC / expected fault / location.
"""
from __future__ import annotations

import json
from pathlib import Path

from fbbench.runner.traj import GRADE_TOOLS, build_traj, _grade_out

LADDER = ["reach", "crash", "differential", "class", "site"]

_MAX_BLOCK = 6000  # cap each rendered tool arg/output block (raw transcript has full)
_LADDER_LABEL = {"reach": "reach", "crash": "crash", "differential": "differential",
                 "class": "class", "site": "site"}


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _tool_stats(nodes: list[dict]) -> list[tuple[str, int, int]]:
    """(tool, calls, errors) per tool, most-used first."""
    agg: dict[str, list[int]] = {}
    for n in nodes:
        row = agg.setdefault(n["tool"], [0, 0])
        row[0] += 1
        if not n["ok"]:
            row[1] += 1
    return sorted(((t, c, e) for t, (c, e) in agg.items()), key=lambda r: -r[1])


def _ladder_html(caps: dict, kb: list[str]) -> str:
    # Applicability is the ORACLE's call: a tier it does not grade for THIS bug
    # comes back "n/a". Render straight from caps so the ladder can never disagree
    # with the tier count — both read the same authoritative dict. kb is NOT
    # consulted: a local guess (DEFAULT_KB) drifted from the oracle and greyed
    # tiers the oracle had actually fired (5/5 header, differential shown as ·).
    cells = []
    for k in LADDER:
        state = caps.get(k)
        if state == "fired":
            cls, glyph = "fired", "●"
        elif state == "not_fired":
            cls, glyph = "miss", "○"
        else:  # "n/a" or absent → not applicable to this bug
            cls, glyph = "na", "·"
        cells.append(
            f'<div class="rung {cls}"><div class="g">{glyph}</div>'
            f'<div class="k">{_LADDER_LABEL[k]}</div></div>'
        )
    return '<div class="ladder">' + '<div class="arrow">→</div>'.join(cells) + "</div>"


def _stat(n: str, label: str, cls: str = "") -> str:
    return f'<div class="stat"><div class="n {cls}">{n}</div><div class="l">{_esc(label)}</div></div>'


def _yn(v) -> str:
    return "yes" if v else "no"


def _config_rows(score: dict, kb: list[str], max_turns_fallback) -> list[tuple[str, str]]:
    """Every run knob that shaped this episode, as (label, value) pairs.

    Reads the `config` block written by the runner, and also falls back to the
    richer top-level score.json fields (mode / diff_level / distractors / …) so
    the same report renders for both the public and the research score shapes.
    """
    cfg = score.get("config") or {}
    rows: list[tuple[str, str]] = []

    mode = cfg.get("mode") or score.get("mode") or "blind"
    rows.append(("mode", mode))

    diff_level = cfg.get("diff_level", score.get("diff_level"))
    if diff_level is not None:
        rows.append(("diff level", str(diff_level)))
    distractors = cfg.get("distractors", score.get("distractors"))
    if distractors is not None:
        n = len(distractors) if isinstance(distractors, (list, tuple)) else distractors
        rows.append(("distractor files", str(n)))
    for fld, label in (("changed_files", "changed files"), ("crash_files", "crash-region files")):
        v = cfg.get(fld, score.get(fld))
        if v:
            rows.append((label, ", ".join(v) if isinstance(v, (list, tuple)) else str(v)))

    mt = cfg.get("max_turns", score.get("max_turns", max_turns_fallback))
    if mt is not None:
        rows.append(("turn budget", str(mt)))

    if kb:
        rows.append(("required ladder (K_b)", " → ".join(kb)))

    rows.append(("stop-on-solve", _yn(cfg.get("stop_on_solve", score.get("stop_on_solve", True)))))
    rows.append(("preserve PoCs", _yn(cfg.get("preserve_pocs", score.get("preserve_pocs", True)))))
    rows.append(("grading", cfg.get("grading", "remote-oracle")))
    img = cfg.get("image")
    if img:
        rows.append(("challenge image", img))
    return rows


def _config_html(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td>{_esc(k)}</td><td class="r">{_esc(v)}</td></tr>' for k, v in rows
    )
    return ('<table class="mini cfg"><tbody>' + body + "</tbody></table>")


def _block(s: str) -> str:
    """Escape + cap a multi-line string for a <pre> block."""
    s = "" if s is None else str(s)
    clipped = len(s) > _MAX_BLOCK
    if clipped:
        s = s[:_MAX_BLOCK] + f"\n… [+{len(s) - _MAX_BLOCK:,} more chars — see transcript.jsonl]"
    return _esc(s)


def _result_text(tool: str, result, is_error: bool) -> tuple[str, bool]:
    """Full-fidelity (capped) text for a tool result + whether it faulted."""
    if is_error:
        data = result.get("data") if isinstance(result, dict) else result
        return "ERROR: " + (str(data) if data else ""), False
    if not isinstance(result, dict):
        return str(result), False
    if tool in GRADE_TOOLS:
        crash = _grade_out(result)[1]
        ho = result.get("harness_output") or {}
        parts = []
        if isinstance(ho, dict):
            if ho.get("exit_code") is not None:
                parts.append(f"exit_code: {ho.get('exit_code')}")
            if ho.get("signal"):
                parts.append(f"signal: {ho.get('signal')}")
            if ho.get("stdout"):
                parts.append("--- stdout ---\n" + str(ho.get("stdout")))
            if ho.get("stderr"):
                parts.append("--- stderr ---\n" + str(ho.get("stderr")))
        verdict = {k: v for k, v in result.items() if k != "harness_output"}
        if verdict:
            parts.insert(0, json.dumps(verdict, indent=2))
        return ("\n".join(parts) or json.dumps(result, indent=2)), crash
    if tool == "exec":
        out = [f"exit_code: {result.get('exit_code')}"]
        if result.get("stdout"):
            out.append("--- stdout ---\n" + str(result.get("stdout")))
        if result.get("stderr"):
            out.append("--- stderr ---\n" + str(result.get("stderr")))
        return "\n".join(out), False
    if tool == "read_file":
        return str(result.get("content", "")), False
    return json.dumps(result, indent=2), False


def build_conversation(transcript_path: Path) -> tuple[list[dict], str, str]:
    """Reconstruct the dialogue: per-turn assistant text + tool calls + results.

    Returns (turns, system_prompt, initial_user_message). Each turn is
    {turn, text, tokens, stop, calls:[{tool, input, result_text, crash, err}]}.
    Pairs an assistant's tool_calls with the matching tool_result by id.
    """
    results_by_id: dict[str, dict] = {}
    assistants: list[dict] = []
    notes_by_turn: dict[int, list[str]] = {}
    system_prompt = initial_user = ""
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("event")
        if ev == "start":
            system_prompt = e.get("system_prompt", "") or ""
            initial_user = e.get("initial_user_message", "") or ""
        elif ev == "tool_result":
            tid = e.get("id")
            if tid is not None:
                results_by_id[tid] = e
        elif ev == "assistant":
            assistants.append(e)
        elif ev == "budget_note":
            notes_by_turn.setdefault(e.get("turn"), []).append(e.get("note", ""))

    turns: list[dict] = []
    for a in assistants:
        calls = []
        for tc in (a.get("tool_calls") or []):
            tid = tc.get("id")
            r = results_by_id.get(tid, {})
            tool = tc.get("name") or r.get("tool") or "?"
            rtext, crash = _result_text(tool, r.get("result"), r.get("is_error", False))
            calls.append({
                "tool": tool,
                "input": tc.get("input") if tc.get("input") is not None else r.get("input"),
                "result_text": rtext,
                "crash": crash,
                "err": bool(r.get("is_error", False)),
            })
        turns.append({
            "turn": a.get("turn"),
            "text": a.get("text") or "",
            "stop": a.get("stop_reason") or "",
            "in_tok": a.get("input_tokens", 0),
            "out_tok": a.get("output_tokens", 0),
            "notes": notes_by_turn.get(a.get("turn"), []),
            "calls": calls,
        })
    return turns, system_prompt, initial_user


def _conversation_html(turns: list[dict], system_prompt: str, initial_user: str) -> str:
    head = ""
    if system_prompt:
        head += (
            '<details class="sys"><summary>system prompt — the task framing the agent '
            'received (answer-free)</summary>'
            f'<pre>{_block(system_prompt)}</pre></details>'
        )
    if initial_user:
        head += (
            '<details class="sys" open><summary>initial user message</summary>'
            f'<pre>{_block(initial_user)}</pre></details>'
        )

    blocks = []
    for t in turns:
        inner = []
        if t["text"].strip():
            inner.append(f'<div class="think">{_block(t["text"])}</div>')
        for note in t["notes"]:
            inner.append(f'<div class="bnote">{_esc(note)}</div>')
        for c in t["calls"]:
            badge = ('<span class="b crash">crash</span>' if c["crash"]
                     else ('<span class="b err">error</span>' if c["err"] else ""))
            arg_json = json.dumps(c["input"], indent=2, ensure_ascii=False) if c["input"] is not None else ""
            arg_det = (f'<details><summary>arguments</summary><pre>{_block(arg_json)}</pre></details>'
                       if arg_json.strip() not in ("", "{}", "null") else "")
            open_attr = " open" if (c["crash"] or c["err"]) else ""
            res_det = (f'<details{open_attr}><summary>result {badge}</summary>'
                       f'<pre>{_block(c["result_text"])}</pre></details>')
            ccls = "crash" if c["crash"] else ("err" if c["err"] else "")
            inner.append(
                f'<div class="tcall {ccls}"><div class="tc-h"><code>{_esc(c["tool"])}</code>{badge}</div>'
                f'{arg_det}{res_det}</div>'
            )
        meta = (f'turn {t["turn"]}'
                + (f' · {t["in_tok"]:,} in / {t["out_tok"]:,} out tok' if (t["in_tok"] or t["out_tok"]) else "")
                + (f' · stop: {_esc(t["stop"])}' if t["stop"] else ""))
        blocks.append(
            f'<div class="cturn"><div class="cturn-h">{meta}</div>{"".join(inner)}</div>'
        )
    body = "".join(blocks) or '<div class="muted">no conversation recorded</div>'
    return head + '<div class="conv">' + body + "</div>"


def build_report_html(run_dir: Path) -> str:
    score = _load(run_dir / "score.json")
    cost = _load(run_dir / "cost.json")
    tpath = run_dir / "transcript.jsonl"
    nodes = build_traj(tpath) if tpath.is_file() else []

    bug = score.get("bug_id", run_dir.parent.parent.name if run_dir.name.startswith("seed")
                     else run_dir.name)
    model = score.get("model", "—")
    caps = score.get("capabilities", {})
    caps_bestof = score.get("capabilities_bestof", {})
    tier = score.get("tier_score", 0)
    reason = score.get("terminated_reason", "—")
    turns = score.get("turns_used", 0)
    dur = score.get("duration_s", 0.0)
    usd = score.get("total_usd") or cost.get("total_usd") or 0.0
    err = score.get("error", "")

    # capability_set + sanitizer / language come from the transcript start event.
    kb: list[str] = []
    sanitizer = language = ""
    if tpath.is_file():
        for line in tpath.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "start":
                kb = sorted(e.get("capability_set", []) or [])
                break

    # Authoritative applicable K_b comes from the oracle's caps: any tier it does
    # not grade for this bug is "n/a". Prefer that over the runner's logged
    # capability_set (a local DEFAULT_KB guess that has drifted). Fall back to the
    # logged kb only when caps carry no verdict yet (e.g. an aborted run).
    applicable_kb = [k for k in LADDER if caps.get(k) not in (None, "n/a")]
    if applicable_kb:
        kb = applicable_kb

    grades = [n for n in nodes if n["tool"] in GRADE_TOOLS]
    faults = [n for n in grades if n["crash"]]
    tool_rows = _tool_stats(nodes)

    in_tok = cost.get("input_tokens", 0)
    out_tok = cost.get("output_tokens", 0)
    cache_r = cost.get("cache_read_tokens", 0)

    cfg = score.get("config") or {}
    mode = cfg.get("mode") or score.get("mode") or "blind"
    tags = []
    if language:
        tags.append(language)
    if sanitizer:
        tags.append(sanitizer)
    tags.append(mode)
    tag_html = "".join(f'<span class="tag">{_esc(t)}</span>' for t in tags)

    # Authoritative solve: a SINGLE candidate reproduced the full target defect
    # (score.solved / terminated "solved"). Fall back to the best-candidate caps
    # for older runs that predate the field. NOT a sticky union across inputs.
    if "solved" in score:
        solved = bool(score.get("solved"))
    else:
        solved = bool(caps) and all(caps.get(k) == "fired" for k in (kb or LADDER))
    verdict_cls = "g" if solved else ("r" if reason == "error" else "a")

    # ---- trajectory rows ----
    traj_rows = []
    for n in nodes:
        mark = "💥" if n["crash"] else ("✗" if not n["ok"] else "")
        rcls = "crash" if n["crash"] else ("err" if not n["ok"] else "")
        traj_rows.append(
            f'<tr class="{rcls}"><td class="r">{n["n"]}</td><td class="r">{n["turn"]}</td>'
            f'<td><code>{_esc(n["tool"])}</code></td>'
            f'<td class="arg">{_esc(n["arg"])}</td>'
            f'<td class="out">{_esc(n["out"])}</td>'
            f'<td class="mk">{mark}</td></tr>'
        )

    tool_rows_html = "".join(
        f'<tr><td><code>{_esc(t)}</code></td><td class="r">{c}</td>'
        f'<td class="r">{e or ""}</td></tr>'
        for t, c, e in tool_rows
    )

    err_card = (
        f'<div class="note">Episode ended in <b>error</b>: <code>{_esc(err)}</code></div>'
        if err else ""
    )

    conv_html = ""
    if tpath.is_file():
        conv_turns, system_prompt_full, initial_user = build_conversation(tpath)
        conv_html = _conversation_html(conv_turns, system_prompt_full, initial_user)

    config_html = _config_html(_config_rows(score, kb, None))

    # Best-of ladder shown alongside the unanimity one (only if the run recorded
    # it). Unanimity drives the tier score; best-of is the "fired on any round"
    # view. Both are human/report facing — neither reaches the model.
    tier_bestof = score.get("tier_score_bestof")
    ladder_bestof_html = ""
    if caps_bestof:
        ladder_bestof_html = (
            '<div class="sub" style="margin-top:14px">best-of '
            f'&mdash; fired on any round ({tier_bestof}/5)</div>'
            + _ladder_html(caps_bestof, kb)
        )

    return _TEMPLATE.format(
        bug=_esc(bug), model=_esc(model), tags=tag_html,
        tier=tier, verdict_cls=verdict_cls,
        turns=turns, ncalls=len(nodes), usd=f"{usd:.4f}",
        config=config_html,
        ladder=_ladder_html(caps, kb),
        ladder_bestof=ladder_bestof_html,
        reason=_esc(reason), dur=f"{dur:.1f}",
        refus=score.get("refusal_retries", 0), malf=score.get("malformed_retries", 0),
        in_tok=f"{in_tok:,}", out_tok=f"{out_tok:,}", cache_r=f"{cache_r:,}",
        in_usd=f'{cost.get("input_usd", 0.0):.4f}',
        out_usd=f'{cost.get("output_usd", 0.0):.4f}',
        ngrades=len(grades), nfaults=len(faults),
        tool_rows=tool_rows_html or '<tr><td colspan="3" class="muted">no tool calls</td></tr>',
        traj_rows="".join(traj_rows) or '<tr><td colspan="6" class="muted">no trajectory</td></tr>',
        conversation=conv_html,
        err_card=err_card,
    )


def write_report(run_dir: str | Path) -> Path:
    run_dir = Path(run_dir)
    html = build_report_html(run_dir)
    out = run_dir / "report.html"
    out.write_text(html)
    return out


_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{bug} · {model} — FB·Bench run report</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--card2:#1c2230;--line:#2a3038;--txt:#e6edf3;
--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149;--purple:#bc8cff;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);line-height:1.55;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}}
.wrap{{max-width:1080px;margin:0 auto;padding:44px 24px 80px;}}
header h1{{font-size:1.9rem;margin:0 0 6px;letter-spacing:-.02em;}}
header h1 .m{{color:var(--accent);}}
.sub{{color:var(--muted);font-size:1rem;}}
.tag{{display:inline-block;font-size:.72rem;background:#1f6feb22;color:var(--accent);
border:1px solid #1f6feb55;border-radius:20px;padding:3px 11px;margin-right:6px;}}
h2{{font-size:1.25rem;margin:48px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line);}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 8px;}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 16px;text-align:center;}}
.stat .n{{font-size:2rem;font-weight:700;letter-spacing:-.02em;}}
.stat .n.g{{color:var(--green)}}.stat .n.a{{color:var(--accent)}}.stat .n.r{{color:var(--red)}}.stat .n.p{{color:var(--purple)}}
.stat .l{{color:var(--muted);font-size:.82rem;margin-top:2px;}}
.ladder{{display:flex;align-items:center;gap:6px;margin:18px 0 4px;flex-wrap:wrap;}}
.ladder .arrow{{color:var(--muted);font-size:1.1rem;}}
.rung{{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 18px;text-align:center;min-width:108px;}}
.rung .g{{font-size:1.5rem;line-height:1;}}
.rung .k{{font-size:.8rem;color:var(--muted);margin-top:5px;}}
.rung.fired{{border-color:#2386364d;background:#23863618;}}.rung.fired .g{{color:var(--green);}}
.rung.miss .g{{color:#46506080;}}
.rung.na{{opacity:.4;}}.rung.na .g{{color:var(--muted);}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;}}
.card h3{{margin:0 0 10px;font-size:.95rem;}}
table{{width:100%;border-collapse:collapse;font-size:.88rem;}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top;}}
th{{color:var(--muted);font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;}}
td.r{{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap;}}
td.mk{{text-align:center;}}td.muted,.muted{{color:var(--muted);}}
code{{background:#0d1117;border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:.9em;color:#ff7b72;}}
.mini td{{padding:5px 8px;}}.mini td:first-child{{font-weight:600;}}
table.cfg{{max-width:560px;}}table.cfg td:first-child{{color:var(--muted);font-weight:500;}}
table.cfg td.r{{color:var(--txt);font-weight:600;text-align:right;word-break:break-all;}}
.traj td.arg{{color:var(--muted);font-family:ui-monospace,monospace;font-size:.82rem;word-break:break-all;}}
.traj td.out{{font-family:ui-monospace,monospace;font-size:.82rem;color:#9da7b3;word-break:break-all;}}
.traj tr.crash td{{background:#f8514915;}}.traj tr.crash td.mk{{color:var(--red);}}
.traj tr.err td.out{{color:var(--amber);}}
.note{{background:#f8514915;border:1px solid #f8514944;border-radius:10px;padding:11px 15px;color:#ffa198;font-size:.88rem;margin:16px 0;}}
/* conversation */
details.sys{{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:4px 14px;}}
details.sys>summary{{cursor:pointer;color:var(--muted);font-size:.84rem;padding:8px 0;}}
.conv{{display:flex;flex-direction:column;gap:16px;margin-top:18px;}}
.cturn{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;}}
.cturn-h{{color:var(--muted);font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;
margin-bottom:8px;font-variant-numeric:tabular-nums;}}
.think{{white-space:pre-wrap;word-break:break-word;color:var(--txt);font-size:.92rem;
background:var(--card2);border-left:3px solid var(--purple);border-radius:8px;padding:11px 14px;margin:6px 0 12px;}}
.bnote{{color:var(--amber);font-size:.82rem;background:#d2992212;border:1px solid #d2992233;
border-radius:8px;padding:7px 12px;margin:6px 0;}}
.tcall{{border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:8px 0;background:#11161f;}}
.tcall.crash{{border-color:#f8514955;background:#f8514910;}}
.tcall.err{{border-color:#d2992255;}}
.tc-h{{font-size:.9rem;margin-bottom:4px;display:flex;align-items:center;gap:8px;}}
.tc-h code{{background:#0d1117;color:var(--accent);border-color:#1f6feb44;}}
.b{{font-size:.68rem;border-radius:20px;padding:2px 9px;font-weight:600;}}
.b.crash{{background:#f8514922;color:var(--red);border:1px solid #f8514955;}}
.b.err{{background:#d2992222;color:var(--amber);border:1px solid #d2992255;}}
.tcall details{{margin:5px 0;}}.tcall summary{{cursor:pointer;color:var(--muted);font-size:.78rem;padding:3px 0;}}
.tcall pre,details.sys pre{{white-space:pre-wrap;word-break:break-word;background:#0d1117;border:1px solid var(--line);
border-radius:8px;padding:10px 12px;font-size:.78rem;line-height:1.5;max-height:480px;overflow:auto;
color:#c9d1d9;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:4px 0 2px;}}
.foot{{color:var(--muted);font-size:.8rem;margin-top:48px;border-top:1px solid var(--line);padding-top:16px;}}
@media(max-width:760px){{.stats,.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class="wrap">

<header>
<div style="margin-bottom:10px">{tags}</div>
<h1>{bug} <span class="m">· {model}</span></h1>
<div class="sub">FuzzingBrain&nbsp;Bench — single-episode run report</div>
</header>

{err_card}

<div class="stats">
  <div class="stat"><div class="n {verdict_cls}">{tier}/5</div><div class="l">tier score</div></div>
  <div class="stat"><div class="n">{turns}</div><div class="l">turns used</div></div>
  <div class="stat"><div class="n a">{ncalls}</div><div class="l">tool calls</div></div>
  <div class="stat"><div class="n p">${usd}</div><div class="l">total cost</div></div>
</div>

<h2>Run configuration</h2>
<div class="sub" style="margin-bottom:10px">Every knob that shaped this episode — the run is
reproducible from these parameters alone.</div>
{config}

<h2>Capability ladder</h2>
<div class="sub">unanimity &mdash; a rung counts only if it fired on every round (drives the tier score)</div>
{ladder}
{ladder_bestof}

<h2>Breakdown</h2>
<div class="grid">
  <div class="card"><h3>Tokens &amp; cost</h3>
    <table class="mini">
      <tr><td>input</td><td class="r">{in_tok} tok</td><td class="r">${in_usd}</td></tr>
      <tr><td>output</td><td class="r">{out_tok} tok</td><td class="r">${out_usd}</td></tr>
      <tr><td>cache read</td><td class="r">{cache_r} tok</td><td class="r"></td></tr>
      <tr><td><b>total</b></td><td class="r"></td><td class="r">${usd}</td></tr>
    </table>
  </div>
  <div class="card"><h3>Tool calls</h3>
    <table><thead><tr><th>tool</th><th class="r">calls</th><th class="r">err</th></tr></thead>
    <tbody>{tool_rows}</tbody></table>
  </div>
  <div class="card"><h3>Run</h3>
    <table class="mini">
      <tr><td>terminated</td><td class="r">{reason}</td></tr>
      <tr><td>duration</td><td class="r">{dur}s</td></tr>
      <tr><td>run_poc_on_harness() calls</td><td class="r">{ngrades}</td></tr>
      <tr><td>faulting grades</td><td class="r">{nfaults}</td></tr>
      <tr><td>refusal retries</td><td class="r">{refus}</td></tr>
      <tr><td>malformed retries</td><td class="r">{malf}</td></tr>
    </table>
  </div>
</div>

<h2>Trajectory</h2>
<table class="traj"><thead><tr><th class="r">#</th><th class="r">turn</th><th>tool</th>
<th>argument</th><th>result</th><th></th></tr></thead>
<tbody>{traj_rows}</tbody></table>

<h2>Conversation</h2>
<div class="sub" style="margin-bottom:6px">The full dialogue — the agent's reasoning each turn,
every tool call with its arguments, and the system's response. Long blocks are collapsed;
faulting / errored calls are expanded.</div>
{conversation}

<div class="foot">Generated by FuzzingBrain&nbsp;Bench · the report records the agent's own
actions only — no oracle PoC, expected fault, or crash location is ever included.</div>

</div></body></html>
"""
