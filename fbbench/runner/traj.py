"""Distil a full transcript into a compact, human-readable trajectory chain.

`transcript.jsonl` is the full-fidelity record (every prompt, tool call, and raw
result). For quickly seeing *what the agent did* — which tools, in what order,
with what outcome, and where it crashed — that is too verbose. `build_traj`
reduces it to one node per tool call: turn, tool, a one-line argument summary, a
one-line result summary, and a `crash` flag for run_poc_on_harness() calls that faulted.

The runner writes this as `traj.jsonl` (one node per line, script-friendly) and
`traj.md` (a readable table) next to the episode; `fb-bench traj <run-dir>`
pretty-prints it. Nothing here re-derives the grade verdict — it only summarises
the raw harness output the agent already saw.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# The submission tool is `run_poc_on_harness` (the sole name). Match it so the
# trajectory's grade-call / fault counts aren't silently zero.
GRADE_TOOLS = frozenset({"run_poc_on_harness"})

# Markers in a run_poc_on_harness()'s raw harness stderr that mean "this input faulted".
_CRASH_RE = re.compile(
    r"AddressSanitizer|UndefinedBehaviorSanitizer|LeakSanitizer|MemorySanitizer|"
    r"runtime error:|libFuzzer: (?:out-of-memory|timeout|deadly signal)|"
    r"Exception in thread|SUMMARY: \w+Sanitizer|: Assertion .+ failed|"
    r"stack-overflow|AssertionError|ERROR: libFuzzer")


def _short(s: str, n: int = 90) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _arg_summary(tool: str, inp: dict) -> str:
    inp = inp or {}
    if tool in ("read_file", "list_directory") or tool in GRADE_TOOLS:
        a = inp.get("path", "")
        if tool == "read_file" and (inp.get("offset") or inp.get("limit")):
            a += f" [{inp.get('offset', 0)}:+{inp.get('limit', '')}]"
        return _short(a, 70)
    if tool == "write_file":
        return _short(f"{inp.get('path', '')} ({len(inp.get('content') or '')}B)", 70)
    if tool == "exec":
        return _short(inp.get("cmd", ""), 70)
    return ""


def _grade_out(result: dict) -> tuple[str, bool]:
    ho = result.get("harness_output") or {}
    if not isinstance(ho, dict):
        return _short(str(result), 80), False
    exit_code, sig = ho.get("exit_code"), ho.get("signal") or ""
    stdout = ho.get("stdout") or ""
    stderr = ho.get("stderr") or ""
    m = _CRASH_RE.search(stderr)
    # Mirror the in-image grader's crashFired guard (gradelocal.go): a bare
    # terminating signal with NO output is the kernel-6.17 ASan startup flake,
    # not an input-triggered crash — so it must NOT be marked as a fault here,
    # or the trajectory's 💥 would contradict a not_fired score.
    has_output = bool(stderr.strip()) or bool(stdout.strip())
    crash = bool(m) or (bool(sig) and has_output)
    parts = [f"exit={exit_code}"]
    if sig:
        parts.append(f"sig={sig}")
    if m:
        # the line carrying the fault marker, trimmed
        line = next((l for l in stderr.splitlines() if m.group(0) in l), m.group(0))
        parts.append(_short(line, 70))
    return "  ".join(parts), crash


def _out_summary(tool: str, result, is_error: bool) -> tuple[str, bool]:
    if is_error:
        data = result.get("data") if isinstance(result, dict) else result
        return "ERROR " + _short(data or "", 70), False
    if not isinstance(result, dict):
        return _short(result, 80), False
    if tool in GRADE_TOOLS:
        return _grade_out(result)
    if tool == "setup":
        return f"bug={result.get('bug_id', '?')}", False
    if tool == "list_directory":
        return f"{len(result.get('entries', []))} entries", False
    if tool == "read_file":
        return f"{len(result.get('content', ''))} chars", False
    if tool == "write_file":
        return f"{result.get('bytes_written', 0)}B written", False
    if tool == "exec":
        return _short(f"exit={result.get('exit_code')}  {result.get('stdout', '')}", 80), False
    return _short(json.dumps(result), 80), False


def build_traj(transcript_path: str | Path) -> list[dict]:
    """One node per tool call: {n, turn, tool, arg, ok, out, crash}."""
    nodes: list[dict] = []
    for line in Path(transcript_path).read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event") != "tool_result":
            continue
        tool = e.get("tool", "?")
        out, crash = _out_summary(tool, e.get("result"), e.get("is_error", False))
        nodes.append({
            "n": len(nodes) + 1,
            "turn": e.get("turn"),
            "tool": tool,
            "arg": _arg_summary(tool, e.get("input")),
            "ok": not e.get("is_error", False),
            "out": out,
            "crash": crash,
        })
    return nodes


def render_md(nodes: list[dict], header: str = "") -> str:
    out = [f"# Trajectory — {header}".rstrip(" —"), ""]
    grades = [n for n in nodes if n["tool"] in GRADE_TOOLS]
    hits = [n for n in grades if n["crash"]]
    out.append(f"{len(nodes)} tool calls · {len(grades)} run_poc_on_harness() calls · "
               f"{len(hits)} faulted"
               + (f" (first at call #{hits[0]['n']}, turn {hits[0]['turn']})" if hits else ""))
    out.append("")
    out.append("| # | turn | tool | argument | result |  |")
    out.append("|--:|--:|--|--|--|:-:|")
    for n in nodes:
        mark = "💥" if n["crash"] else ("✗" if not n["ok"] else "")
        out.append(f"| {n['n']} | {n['turn']} | `{n['tool']}` | {n['arg']} | {n['out']} | {mark} |")
    return "\n".join(out) + "\n"


def render_text(nodes: list[dict]) -> str:
    lines = []
    for n in nodes:
        mark = " 💥" if n["crash"] else ("  ✗" if not n["ok"] else "   ")
        lines.append(f"{n['n']:>3} t{n['turn']:<3}{mark} {n['tool']:<14} "
                     f"{n['arg']:<40}  {n['out']}")
    return "\n".join(lines)


def write_traj(transcript_path: str | Path, out_dir: str | Path,
               header: str = "") -> list[dict]:
    """Build the chain from a transcript and write traj.jsonl + traj.md."""
    nodes = build_traj(transcript_path)
    out_dir = Path(out_dir)
    with open(out_dir / "traj.jsonl", "w") as f:
        for n in nodes:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    (out_dir / "traj.md").write_text(render_md(nodes, header), encoding="utf-8")
    return nodes
