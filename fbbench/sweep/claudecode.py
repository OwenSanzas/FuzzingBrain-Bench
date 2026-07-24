"""Claude-Code-CLI arm: drive headless `claude -p` over the bench MCP server.

  python -m fbbench.sweep.claudecode one   <bug_id> [--model sonnet] [--max-turns N]
  python -m fbbench.sweep.claudecode sweep [--bugs all|<csv>] [--model sonnet]

The sibling of the Codex arm (fbbench/sweep/codex.py). It reuses the SAME bench
MCP server (the public canonical challenge image: `docker run -i --rm <image>
mcp-server`), the SAME neutral discovery view, the SAME netns-isolated exec(),
and grades through the SAME remote oracle — so the only difference between the
two product-CLI arms is the model/driver.

Cheat hardening (audited empirically — see the module docstring notes below):
Claude Code runs HEADLESS on the host, but EVERY host-side cheat surface is shut
off, not just forbidden by the prompt:
  - cwd is the bind-mounted ISOLATED workspace (a temp dir), NOT the repo — so
    the repo's `output/` (prior winning PoCs) and `bugs/` (the staged answer) are
    not reachable by relative path.
  - ALL built-in tools (Bash/Read/Write/Web*/Task/Skill/SlashCommand/…) are
    disallowed; the ONLY allowed tools are the six `mcp__bench__*` tools. Verified
    that with these flags an agent explicitly instructed to shell out / read the
    host answer file produces ZERO non-bench tool calls and cannot reach it.
  - `--strict-mcp-config` → only the bench MCP server (no user MCP servers leak).
  - `--setting-sources project` with cwd = the empty workspace → the user's
    global allow-list / `skipDangerousModePermissionPrompt` do NOT apply.
  - the child env is scrubbed to PATH+HOME only (HOME kept for `claude` auth),
    mirroring Codex's `inherit = "none"`.
The one residual cheat surface — the in-container `mcp__bench__exec` reading the
oracle answer key — is SHARED with the Codex arm (a bench-level issue), not a
Claude-specific regression.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from fbbench.grading import capability_set, find_bug, list_bugs
from fbbench.prompts import CODEX_TASK_PROMPT
from fbbench.runner.mcp_client import _full_scan_alias
# Reuse the Codex arm's host-side helpers verbatim so the two arms grade and
# select PoCs identically (same remote oracle, same blob heuristic).
from fbbench.paths import resolve_output
from fbbench.sweep.codex import (
    FLAGS, GRADE_URL, IMAGE_PREFIX, DEFAULT_OUTPUT,
    _best_caps, _candidate_blobs, _codex_nudge, _remote_grade,
)

MAX_TURNS_DEFAULT = 100
MODEL_DEFAULT = "sonnet"
MAX_RESUMES = 30  # parity with the Codex arm's resume cap

# The only tools the agent may call: the six bench MCP tools. Everything else is
# a host-side cheat/contamination surface and is hard-denied below.
_BENCH_TOOLS = ",".join(
    f"mcp__bench__{t}" for t in
    ("setup", "list_directory", "read_file", "write_file", "exec", "grade"))
# Exhaustive built-in denylist. `--allowedTools` is NOT exclusive (tools absent
# from it can still run if they don't require a prompt — Skill/SlashCommand slip
# through), so we ALSO name every built-in here. Audited: with this list an agent
# told to shell out makes zero non-bench calls.
_DENY_TOOLS = ",".join((
    "Bash", "BashOutput", "KillShell", "Read", "Write", "Edit", "MultiEdit",
    "NotebookEdit", "Glob", "Grep", "Task", "Agent", "WebFetch", "WebSearch",
    "ToolSearch", "TodoWrite", "Skill", "SlashCommand", "ExitPlanMode",
))


def claude_task_prompt() -> str:
    """The shared CLI task prompt, re-pointed from Codex's `harness` MCP server to
    Claude Code's `bench` server (mcp__harness__* -> mcp__bench__*). Everything
    else — including the fuzz-harness references (./harness etc.) and the generic
    "your own built-in tools are not available" line — is identical for both arms.
    """
    return (CODEX_TASK_PROMPT
            .replace("MCP `harness`", "MCP `bench`")
            .replace("mcp__harness__", "mcp__bench__"))


def _budget_text(max_turns: int) -> str:
    """Same turn-budget HARD RULES the Codex arm appends (one tool call ≈ one
    turn). Claude, like Codex, gets no per-turn budget note injected mid-episode,
    so without this it over-reads source and never grades."""
    first_by = max(5, max_turns // 10)
    every = max(3, max_turns // 15)
    return (
        f"\n\nTURN BUDGET — HARD RULES (one tool call ≈ one turn, ~{max_turns} total):\n"
        f"1. Within your FIRST {first_by} turns you MUST write a candidate input and "
        f"call grade() on it — even a crude guess. Do not read more than a handful of "
        f"files before that first grade().\n"
        f"2. After that, call grade() at least once every ~{every} turns. Never read "
        f"more than ~{every} files in a row without grading something.\n"
        f"3. Every grade() banks partial credit (reach/crash/…) independently, so a "
        f"rough PoC that only 'reaches' is worth far more than perfect source analysis "
        f"that never grades. Reading the whole source without grading scores ZERO.\n"
        f"Treat grade() as your primary tool, not a final step.")


def model_label(model: str) -> str:
    """Result-dir label for this arm + model (e.g. claude-code-sonnet)."""
    return f"claude-code-{model}"


def stage_claude_env(real_bug_dir: str, model: str) -> tuple[str, str, str, str]:
    """Stage an isolated workspace + a bench MCP config for the canonical image.

    Returns (image, root, work, mcp_cfg):
      - image:   docker.io/...-<alias> canonical challenge image
      - root:    cell temp dir (caller cleans it up)
      - work:    bind-mounted workspace (-> container /workspace) AND the claude
                 cwd — named by the NEUTRAL alias so the path leaks nothing.
      - mcp_cfg: path to bench.mcp.json wiring `docker run … mcp-server`.
    """
    alias = _full_scan_alias(real_bug_dir)
    image = f"{IMAGE_PREFIX}{alias}"
    root = tempfile.mkdtemp(prefix=f"cc-{alias}-")
    work = os.path.join(root, "workspace")
    os.makedirs(work, exist_ok=True)
    os.chmod(work, 0o777)  # the container (root) writes candidate inputs here
    mcp_cfg = os.path.join(root, "bench.mcp.json")
    with open(mcp_cfg, "w") as f:
        json.dump({"mcpServers": {"bench": {"command": "docker", "args": [
            "run", "-i", "--rm", "--security-opt", "seccomp=unconfined",
            "-v", f"{work}:/workspace", image, "mcp-server"]}}}, f)
    return image, root, work, mcp_cfg


def claude_cmd(prompt: str, mcp_cfg: str, model: str, max_turns: int,
               resume_session: str | None = None) -> list[str]:
    """The hardened `claude -p` argv (see module docstring for the threat model)."""
    cmd = ["claude", "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--model", model,
           "--mcp-config", mcp_cfg, "--strict-mcp-config",
           "--allowedTools", _BENCH_TOOLS,
           "--disallowedTools", _DENY_TOOLS,
           "--permission-mode", "default",
           "--setting-sources", "project",
           "--max-turns", str(max_turns)]
    if resume_session:
        cmd += ["--resume", resume_session]
    return cmd


def _anthropic_key() -> str | None:
    """The pay-as-you-go API key for --auth api, from ./.env then the environment."""
    from fbbench.env import read_dotenv
    return read_dotenv().get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")


def _clean_env(auth: str = "sub", api_key: str | None = None,
               home: str | None = None) -> dict:
    """Env for the `claude` PARENT process (host-side). PATH+HOME only, mirroring
    Codex's `inherit = none`. The bench MCP `docker run` forwards no `-e`, so
    whatever is here NEVER reaches the container — the model key stays with the
    agent, exactly like the API arm (env key) and the Codex arm (auth.json).

    Two auth entries:
      - 'sub' (default, backward compatible): OAuth subscription. Real HOME holds
        ~/.claude creds and NO API key is set, so `claude` uses the claude.ai (Max)
        login. Subject to that plan's session limit.
      - 'api': API key. ANTHROPIC_API_KEY is set (it takes precedence over OAuth)
        and HOME points at an isolated dir with NO ~/.claude creds, so there is no
        OAuth/key conflict — pay-as-you-go, no session-limit throttle.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": home or os.environ.get("HOME", "")}
    if auth == "api":
        if not api_key:
            raise SystemExit("claudecode --auth api: no ANTHROPIC_API_KEY in "
                             "./.env or environment")
        env["ANTHROPIC_API_KEY"] = api_key
    return env


def _kill_pg(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_claude_once(argv: list[str], lf, deadline: float,
                     env: dict | None = None) -> dict:
    """Run ONE `claude -p` process, streaming stream-json lines to `lf` and
    parsing them live. A watchdog hard-kills on the wall-clock backstop.

    `env` selects the auth entry (PATH+HOME[+ANTHROPIC_API_KEY]); None inherits
    the parent env. Returns {turns, grade_calls, tokens, usd, session_id, ended}
    for THIS invocation, where a turn == one assistant MESSAGE (one model API
    call). NB: stream-json splits a multi-block message into several `assistant`
    events that SHARE a message.id, so we dedupe by id — matching `--max-turns`.
    """
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, start_new_session=True, env=env)
    st = {"turns": 0, "grade_calls": 0, "tokens": 0, "usd": 0.0,
          "session_id": None, "ended": "exited"}
    msg_ids: set = set()
    grade_ids: set = set()
    stop = threading.Event()

    def _watch():
        while not stop.wait(3):
            if time.time() > deadline:
                st["ended"] = "deadline"
                _kill_pg(proc)
                return
    wd = threading.Thread(target=_watch, daemon=True)
    wd.start()

    for line in proc.stdout:  # blocks until EOF (proc exit / killed)
        lf.write(line)
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            st["session_id"] = ev.get("session_id") or st["session_id"]
        elif t == "assistant":
            msg = ev.get("message", {})
            mid = msg.get("id")
            if mid is not None:
                msg_ids.add(mid)
            st["turns"] = len(msg_ids)
            for b in msg.get("content", []):
                if (b.get("type") == "tool_use"
                        and str(b.get("name", "")).endswith("__grade")):
                    grade_ids.add(b.get("id"))
            st["grade_calls"] = len(grade_ids)
        elif t == "result":
            st["session_id"] = ev.get("session_id") or st["session_id"]
            u = ev.get("usage") or {}
            st["tokens"] += int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
            st["usd"] += float(ev.get("total_cost_usd") or 0.0)
    try:
        proc.wait(timeout=10)
    except Exception:
        _kill_pg(proc)
    stop.set()
    return st


def run_claude(work: str, mcp_cfg: str, model: str, timeout_s: int,
               max_turns: int = MAX_TURNS_DEFAULT, *,
               auth: str = "sub", api_key: str | None = None) -> dict:
    """Drive `claude -p` to a fixed TURN budget, EB-style (like the Codex arm).

    A turn = one model API call (one `assistant` event). Claude satisfices before
    the budget, so after each process exits we RESUME the session (`--resume`)
    with the same EB nudge (wrap-up / stuck-grade / continue) until the budget is
    hit. Each resume's per-invocation `--max-turns` is the REMAINING budget so a
    single process can never overshoot. Wall-clock `timeout_s` is an anti-hang
    backstop only.

    `auth` picks the auth entry ('sub' = OAuth Max, 'api' = ANTHROPIC_API_KEY).
    For 'api' we stage an isolated HOME (no ~/.claude creds) so the key is used
    cleanly with no OAuth conflict; both entries keep the key out of the container.
    """
    if auth == "api":
        home = os.path.join(os.path.dirname(work), "claude_home")
        os.makedirs(home, exist_ok=True)
    else:
        home = None
    env = _clean_env(auth, api_key, home)

    log_path = os.path.join(work, "claude.log")
    t0 = time.time()
    deadline = t0 + timeout_s
    turns = grade_calls = tokens = 0
    usd = 0.0
    session_id = None
    last_grade_turn = 0
    terminated = "resumes_exhausted"

    with open(log_path, "w") as lf:
        prompt = claude_task_prompt() + _budget_text(max_turns)
        resume = None
        for attempt in range(MAX_RESUMES + 1):
            remaining = max_turns - turns
            if remaining <= 0:
                terminated = "turn_budget"
                break
            argv = claude_cmd(prompt, mcp_cfg, model, remaining, resume_session=resume)
            st = _run_claude_once(argv, lf, deadline, env)
            turns += st["turns"]
            grade_calls += st["grade_calls"]
            tokens += st["tokens"]
            usd += st["usd"]
            if st["grade_calls"]:
                last_grade_turn = turns
            session_id = st["session_id"] or session_id

            if turns >= max_turns:
                terminated = "turn_budget"
                break
            if st["ended"] == "deadline" or time.time() > deadline:
                terminated = "timeout"
                break
            if attempt >= MAX_RESUMES or not session_id:
                terminated = "resumes_exhausted"
                break
            # Claude stopped on its own with budget left → nudge and resume.
            nudge = _codex_nudge(turns, max_turns, last_grade_turn)
            lf.write(json.dumps({"fbbench_nudge": nudge, "at_turn": turns}) + "\n")
            prompt = nudge
            resume = session_id

    return {"terminated": terminated, "duration_s": time.time() - t0,
            "log_path": log_path, "turns": turns, "grade_calls": grade_calls,
            "tokens": tokens, "total_usd": round(usd, 4)}


def _stream_to_transcript(log_path: str, out_path: Path, *, model: str,
                          bug_id: str, kb: list[str]) -> None:
    """Convert a claude stream-json log into report.py's transcript.jsonl format.

    Maps assistant text + tool_use → assistant events, tool_result (in `user`
    events) → tool_result events, and our resume nudges → budget_note events, so
    report.py renders Claude episodes exactly like the API / Codex arms.
    """
    events: list[dict] = [{
        "event": "start", "model": model, "bug_id": bug_id,
        "capability_set": sorted(kb), "system_prompt": claude_task_prompt(),
        "initial_user_message": "",
    }]
    call_name: dict[str, str] = {}
    call_input: dict[str, object] = {}
    turn = 0
    # stream-json splits one assistant message across several events sharing a
    # message.id; coalesce them so each model turn is ONE transcript event.
    seen_msg: dict[str, int] = {}   # message.id -> index in `events`
    seen_block: set = set()         # (mid, block-key) to avoid double-adding
    for raw in open(log_path, errors="ignore"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        if "fbbench_nudge" in ev:
            events.append({"event": "budget_note", "turn": turn,
                           "note": ev["fbbench_nudge"]})
            continue
        t = ev.get("type")
        if t == "assistant":
            msg = ev.get("message", {})
            mid = msg.get("id") or f"_anon{len(events)}"
            if mid not in seen_msg:
                turn += 1
                events.append({
                    "event": "assistant", "turn": turn, "text": "",
                    "stop_reason": "end_turn",
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "tool_calls": [],
                })
                seen_msg[mid] = len(events) - 1
            ae = events[seen_msg[mid]]
            for b in msg.get("content", []):
                if b.get("type") == "text":
                    key = (mid, "t", b.get("text", "")[:40])
                    if key in seen_block:
                        continue
                    seen_block.add(key)
                    ae["text"] = (ae["text"] + "\n\n" + b.get("text", "")).strip()
                elif b.get("type") == "tool_use":
                    cid = b.get("id") or ""
                    if (mid, cid) in seen_block:
                        continue
                    seen_block.add((mid, cid))
                    name = str(b.get("name", "")).split("__")[-1]
                    call_name[cid] = name
                    call_input[cid] = b.get("input")
                    ae["tool_calls"].append(
                        {"id": cid, "name": name, "input": b.get("input")})
                    ae["stop_reason"] = "tool_use"
        elif t == "user":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") != "tool_result":
                    continue
                cid = b.get("tool_use_id") or ""
                out = b.get("content")
                result = out
                if isinstance(out, list):
                    result = "\n".join(
                        x.get("text", "") for x in out if isinstance(x, dict))
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except Exception:
                        pass
                events.append({
                    "event": "tool_result", "id": cid,
                    "tool": call_name.get(cid, "?"), "result": result,
                    "is_error": bool(b.get("is_error")),
                    "input": call_input.get(cid),
                })
    with open(out_path, "w") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _persist(cell_dir: Path, *, bug: str, model: str, real: str,
             r: dict, blobs: list[str], alias: str) -> dict:
    """Re-grade blobs through the remote oracle, write score.json + report."""
    cell_dir.mkdir(parents=True, exist_ok=True)
    caps, best_blob, ts, solved = _best_caps(alias, blobs)
    if best_blob:
        shutil.copy(best_blob, cell_dir / "best_blob")
    if Path(r["log_path"]).is_file():
        shutil.copy(r["log_path"], cell_dir / "claude.log")
    kb = capability_set(real)
    score = {
        "bug_id": bug, "model": model_label(model), "seed": 0,
        "capabilities": caps, "tier_score": ts, "k_b": kb,
        # Authoritative target_bug_found from _best_caps — matches the API arm.
        "solved": solved,
        "terminated_reason": r["terminated"], "turns_used": r["turns"],
        "max_turns": r.get("max_turns"), "duration_s": round(r["duration_s"], 1),
        "grade_calls": r["grade_calls"], "blobs_written": len(blobs),
        "tokens_used": r["tokens"] or None, "total_usd": r["total_usd"],
    }
    (cell_dir / "score.json").write_text(json.dumps(score, indent=2))
    try:
        _stream_to_transcript(r["log_path"], cell_dir / "transcript.jsonl",
                              model=model_label(model), bug_id=bug, kb=kb)
        from fbbench.runner.report import write_report
        write_report(cell_dir)
    except Exception as e:  # noqa: BLE001
        print(f"report skipped: {e}")
    return score


def cmd_one(args) -> int:
    real = find_bug(args.bug_id)
    if not real:
        sys.exit(f"bug not found: {args.bug_id}")
    alias = _full_scan_alias(str(real))
    image, _root, work, mcp_cfg = stage_claude_env(str(real), args.model)
    print(f"IMAGE={image}\nWORK={work}\nLOG={os.path.join(work, 'claude.log')}", flush=True)
    api_key = _anthropic_key() if args.auth == "api" else None
    r = run_claude(work, mcp_cfg, args.model, args.timeout, args.max_turns,
                   auth=args.auth, api_key=api_key)
    r["max_turns"] = args.max_turns
    print(f"\nclaude {r['terminated']} after {r['duration_s']:.0f}s  "
          f"turns={r['turns']}/{args.max_turns}  grades={r['grade_calls']}  "
          f"${r['total_usd']}", flush=True)

    blobs = _candidate_blobs(work)
    print(f"\n=== {len(blobs)} candidate blob(s) in workspace ===", flush=True)
    for b in blobs:
        print(f"  {os.path.basename(b):30s} ({os.path.getsize(b)}b)")

    cell_dir = resolve_output(args.output) / args.bug_id / model_label(args.model) / "one"
    score = _persist(cell_dir, bug=args.bug_id, model=args.model, real=str(real),
                     r=r, blobs=blobs, alias=alias)
    fired = [f for f in FLAGS if score["capabilities"][f] == "fired"]
    print(f"\nBEST fired {fired}  (tier {score['tier_score']}/5, "
          f"solved={score['solved']})")
    print(f"results saved to: {cell_dir}")
    print(f"workspace kept at: {work}")
    return 0


def run_sweep_cell(bug: str, model: str, timeout_s: int,
                   max_turns: int = MAX_TURNS_DEFAULT, *,
                   auth: str = "sub", api_key: str | None = None,
                   out_root=DEFAULT_OUTPUT) -> dict | None:
    cell_dir = out_root / bug / model_label(model) / "seed-0"
    if (cell_dir / "score.json").is_file():
        return None  # already done
    real = find_bug(bug)
    if not real:
        print(f"  [skip] bug not found: {bug}")
        return None
    alias = _full_scan_alias(str(real))
    _image, root, work, mcp_cfg = stage_claude_env(str(real), model)
    try:
        r = run_claude(work, mcp_cfg, model, timeout_s, max_turns,
                       auth=auth, api_key=api_key)
        r["max_turns"] = max_turns
        blobs = _candidate_blobs(work)
        score = _persist(cell_dir, bug=bug, model=model, real=str(real),
                         r=r, blobs=blobs, alias=alias)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return score


def cmd_sweep(args) -> int:
    out_root = resolve_output(args.output)
    label = model_label(args.model)
    api_key = _anthropic_key() if args.auth == "api" else None
    if args.auth == "api" and not api_key:
        sys.exit("  --auth api: no ANTHROPIC_API_KEY in ./.env or environment")
    bugs = ([n for n, _ in list_bugs()] if args.bugs == "all"
            else [b.strip() for b in args.bugs.split(",") if b.strip()])
    done = sum(1 for b in bugs if (out_root / b / label / "seed-0" / "score.json").is_file())
    print(f"  claude-code sweep ({label}, auth={args.auth}): {len(bugs)} bugs "
          f"({done} done, {len(bugs)-done} to run)")
    t0 = time.time()
    solved_total = 0
    for i, bug in enumerate(bugs, 1):
        cell = out_root / bug / label / "seed-0" / "score.json"
        if cell.is_file():
            s = json.loads(cell.read_text())
        else:
            print(f"  [{i}/{len(bugs)}] run  {bug} ...", flush=True)
            s = run_sweep_cell(bug, args.model, args.timeout, args.max_turns,
                               auth=args.auth, api_key=api_key, out_root=out_root)
            if not s:
                continue
        mark = "✓" if s["solved"] else "✗"
        print(f"      {mark} {s['tier_score']}/5  {s['terminated_reason']}  "
              f"turns={s.get('turns_used')}/{s.get('max_turns', '?')}  "
              f"{s['duration_s']}s  grades={s['grade_calls']}  "
              f"blobs={s['blobs_written']}  ${s.get('total_usd')}")
        solved_total += int(s["solved"])
    print(f"\n  done in {time.time()-t0:.0f}s  solved {solved_total}/{len(bugs)}")
    try:
        from fbbench.report.summary import write_summary
        print(f"  summary -> {write_summary(out_root)}")
    except Exception as e:  # noqa: BLE001
        print(f"  summary skipped: {e}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fbbench.sweep.claudecode",
                                 description="Claude-Code-CLI arm for FuzzingBrain Bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_one = sub.add_parser("one", help="run a single bug (keeps workspace)")
    sp_one.add_argument("bug_id")
    sp_one.add_argument("--model", default=MODEL_DEFAULT,
                        help="claude model alias (sonnet/opus/haiku) or full id")
    sp_one.add_argument("--max-turns", type=int, default=MAX_TURNS_DEFAULT,
                        help="turn budget (one assistant message = one turn)")
    sp_one.add_argument("--timeout", type=int, default=1800,
                        help="wall-clock backstop seconds (anti-hang, not the cap)")
    sp_one.add_argument("--auth", choices=("sub", "api"), default="sub",
                        help="auth entry: 'sub' = claude.ai OAuth (Max, session-limited), "
                             "'api' = ANTHROPIC_API_KEY from ./.env (pay-as-you-go, no throttle)")
    sp_one.add_argument("--output", "-o", default=None,
                        help="results root (default: ./output); bare name nests under it, "
                             "path used as-is")
    sp_one.set_defaults(fn=cmd_one)

    sp_sweep = sub.add_parser("sweep", help="batch all bugs, persist score.json (resumable)")
    sp_sweep.add_argument("--bugs", default="all", help="'all' or comma list")
    sp_sweep.add_argument("--model", default=MODEL_DEFAULT,
                          help="claude model alias (sonnet/opus/haiku) or full id")
    sp_sweep.add_argument("--max-turns", type=int, default=MAX_TURNS_DEFAULT,
                          help="turn budget per bug (one assistant message = one turn)")
    sp_sweep.add_argument("--timeout", type=int, default=1800,
                          help="per-bug wall-clock backstop seconds (anti-hang)")
    sp_sweep.add_argument("--auth", choices=("sub", "api"), default="sub",
                          help="auth entry: 'sub' = claude.ai OAuth (Max, session-limited), "
                               "'api' = ANTHROPIC_API_KEY from ./.env (pay-as-you-go, no throttle)")
    sp_sweep.add_argument("--output", "-o", default=None,
                          help="results root (default: ./output); bare name nests under it, "
                               "path used as-is. Re-running the same --output resumes it")
    sp_sweep.set_defaults(fn=cmd_sweep)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
