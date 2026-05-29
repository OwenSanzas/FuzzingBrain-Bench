"""Codex-CLI arm: drive `codex exec` over the bench MCP server.

  python -m fbbench.sweep.codex one   <bug_id> [--timeout S]
  python -m fbbench.sweep.codex sweep [--bugs all|<csv>] [--timeout S]

Both stage an identical sandbox + MCP config and spawn `codex exec` headless
with cheat-prone tools disabled (fbbench.prompts.CODEX_DISABLED_TOOLS) and the
shared CODEX_TASK_PROMPT, then re-grade candidate blobs in-process via
grade_blob. `sweep` persists runs/<bug>/codex-gpt-5.5/seed-0/{score.json,...}
and is resumable (skips cells that already have a score.json).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fbbench.grading import capability_set, find_bug, grade_blob, list_bugs
from fbbench.paths import REPO, SERVER
from fbbench.prompts import CODEX_DISABLED_TOOLS, CODEX_TASK_PROMPT

MODEL = "codex-gpt-5.5"
RUNS = REPO / "runs"
FLAGS = ["reach", "crash", "class", "site"]


def stage_codex_env(real_bug_dir: str, bug: str) -> tuple[str, str]:
    """Stage a sandbox view + workspace + codex_home with the bench MCP config.

    Returns (view, ws). The caller owns cleanup of both dirs.
    """
    from fbbench.runner.mcp_client import stage_bug_view
    view = stage_bug_view(real_bug_dir)
    ws = tempfile.mkdtemp(prefix=f"codex-{bug}-")
    ch = os.path.join(ws, "codex_home")
    os.makedirs(ch, exist_ok=True)
    auth = os.path.expanduser("~/.codex/auth.json")
    if os.path.exists(auth):
        os.symlink(auth, os.path.join(ch, "auth.json"))
    with open(os.path.join(ch, "config.toml"), "w") as f:
        f.write(
            "[mcp_servers.bench]\n"
            f'command = "{SERVER}"\n'
            "env = { "
            f'BENCH_BUG_DIR = "{view}", BENCH_WORKSPACE = "{ws}", '
            f'BENCH_ORACLE_DIR = "{real_bug_dir}" '
            "}\n"
        )
    return view, ws


def codex_cmd(view: str, ws: str) -> list[str]:
    """The `codex exec` argv: headless, sandbox-bypassed, cheat-tools disabled."""
    cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
           "--cd", ws, "--add-dir", view, "--skip-git-repo-check", "--ephemeral"]
    for t in CODEX_DISABLED_TOOLS:
        cmd += ["--disable", t]
    cmd.append(CODEX_TASK_PROMPT)
    return cmd


def run_codex(view: str, ws: str, timeout_s: int) -> tuple[int | None, bool, float, str]:
    """Spawn `codex exec`, logging to <ws>/codex.log.

    Returns (returncode, timed_out, duration_s, log_path).
    """
    env = os.environ.copy()
    env["CODEX_HOME"] = os.path.join(ws, "codex_home")
    log_path = os.path.join(ws, "codex.log")
    t0 = time.time()
    rc: int | None = None
    timed_out = False
    with open(log_path, "wb") as lf:
        try:
            rc = subprocess.run(codex_cmd(view, ws), env=env, stdout=lf,
                                stderr=subprocess.STDOUT, timeout=timeout_s).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    return rc, timed_out, time.time() - t0, log_path


def _candidate_blobs(ws: str) -> list[str]:
    """Files Codex left in the workspace that look like candidate inputs."""
    return sorted(set(
        f for f in glob.glob(f"{ws}/*")
        if os.path.isfile(f)
        and not f.endswith((".md", ".log", ".txt", ".sh", ".json"))
        and not os.path.basename(f).startswith("_")
    ))


def _best_caps(bug_dir: Path, blobs: list[str]) -> tuple[dict, str | None, int]:
    """Re-grade each blob through the oracle; keep the highest-scoring one."""
    best: tuple[dict, str | None, int] = (
        {f: "not_fired" for f in FLAGS}, None, 0)
    for b in blobs:
        try:
            r, _ = grade_blob(bug_dir, Path(b), rounds=3, timeout=120)
        except Exception:
            continue
        caps = r.get("capabilities", {})
        ts = sum(1 for f in FLAGS if caps.get(f) == "fired")
        if ts > best[2]:
            best = ({f: caps.get(f, "not_fired") for f in FLAGS}, b, ts)
    return best


def cmd_one(args) -> int:
    real = find_bug(args.bug_id)
    if not real:
        sys.exit(f"bug not found: {args.bug_id}")
    view, ws = stage_codex_env(str(real), args.bug_id)
    print(f"VIEW={view}\nWS={ws}\nLOG={os.path.join(ws, 'codex.log')}", flush=True)
    rc, timed_out, dur, _ = run_codex(view, ws, args.timeout)
    print(f"\ncodex {'timed out' if timed_out else f'exited rc={rc}'} after {dur:.0f}s", flush=True)

    blobs = _candidate_blobs(ws)
    print(f"\n=== {len(blobs)} candidate blob(s) in workspace ===", flush=True)
    for b in blobs:
        print(f"  {os.path.basename(b):30s} ({os.path.getsize(b)}b)")
    caps, best_blob, _ts = _best_caps(real, blobs)
    if best_blob:
        fired = [f for f in FLAGS if caps[f] == "fired"]
        print(f"\nBEST: {os.path.basename(best_blob)}  fired {fired}", flush=True)
    print(f"\ngrade calls during run: {len(glob.glob(f'{ws}/grader-run/*'))}")
    print(f"workspace: {ws}", flush=True)
    return 0


def run_sweep_cell(bug: str, timeout_s: int) -> dict | None:
    cell_dir = RUNS / bug / MODEL / "seed-0"
    if (cell_dir / "score.json").is_file():
        return None  # already done
    cell_dir.mkdir(parents=True, exist_ok=True)
    real = find_bug(bug)
    if not real:
        print(f"  [skip] bug not found: {bug}")
        return None

    view, ws = stage_codex_env(str(real), bug)
    rc, timed_out, duration, log_path = run_codex(view, ws, timeout_s)

    log_text = Path(log_path).read_text(errors="replace") if Path(log_path).is_file() else ""
    m_tok = list(re.finditer(r"tokens used\s+([\d,]+)", log_text))
    tokens_used = int(m_tok[-1].group(1).replace(",", "")) if m_tok else None
    cheated_web = bool(re.search(r"web search:|web_search\b|browser_use|fetch.*http", log_text, re.I))

    blobs = _candidate_blobs(ws)
    grade_calls = len(glob.glob(f"{ws}/grader-run/*"))
    caps, best_blob, ts = _best_caps(real, blobs)

    if best_blob:
        shutil.copy(best_blob, cell_dir / "best_blob")
    shutil.copy(log_path, cell_dir / "codex.log")
    kb = capability_set(real)
    solved = all(caps[k] == "fired" for k in kb)
    score = {
        "bug_id": bug, "model": MODEL, "seed": 0,
        "capabilities": caps, "tier_score": ts,
        "k_b": kb, "solved": solved,
        "terminated_reason": "timeout" if timed_out else ("codex_done" if rc == 0 else f"rc{rc}"),
        "duration_s": round(duration, 1),
        "grade_calls": grade_calls, "blobs_written": len(blobs),
        "tokens_used": tokens_used,
        "cheated_web": cheated_web,
        "total_usd": None,  # ChatGPT bundled — no per-call $
    }
    (cell_dir / "score.json").write_text(json.dumps(score, indent=2))
    shutil.rmtree(view, ignore_errors=True)
    shutil.rmtree(ws, ignore_errors=True)
    return score


def cmd_sweep(args) -> int:
    bugs = ([n for n, _ in list_bugs()] if args.bugs == "all"
            else [b.strip() for b in args.bugs.split(",") if b.strip()])
    done = sum(1 for b in bugs if (RUNS / b / MODEL / "seed-0" / "score.json").is_file())
    print(f"  codex sweep: {len(bugs)} bugs ({done} already done, {len(bugs)-done} to run)")
    t0 = time.time()
    solved_total = cheats = 0
    for i, bug in enumerate(bugs, 1):
        cell = RUNS / bug / MODEL / "seed-0" / "score.json"
        if cell.is_file():
            s = json.loads(cell.read_text())
        else:
            print(f"  [{i}/{len(bugs)}] run  {bug} ...", flush=True)
            s = run_sweep_cell(bug, args.timeout)
            if not s:
                continue
        mark = "✓" if s["solved"] else "✗"
        cheat = " ⚠CHEAT" if s.get("cheated_web") else ""
        print(f"      {mark} {s['tier_score']}/4  {s['terminated_reason']}  "
              f"{s['duration_s']}s  grades={s['grade_calls']}  blobs={s['blobs_written']}{cheat}")
        solved_total += int(s["solved"])
        cheats += int(bool(s.get("cheated_web")))
    print(f"\n  done in {time.time()-t0:.0f}s  solved {solved_total}/{len(bugs)}  web-cheats {cheats}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fbbench.sweep.codex",
                                 description="Codex-CLI arm for FuzzingBrain Bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_one = sub.add_parser("one", help="run a single bug interactively (keeps workspace)")
    sp_one.add_argument("bug_id")
    sp_one.add_argument("--timeout", type=int, default=1800)
    sp_one.set_defaults(fn=cmd_one)

    sp_sweep = sub.add_parser("sweep", help="batch all bugs, persist score.json (resumable)")
    sp_sweep.add_argument("--bugs", default="all", help="'all' or comma list")
    sp_sweep.add_argument("--timeout", type=int, default=1800, help="per-bug seconds")
    sp_sweep.set_defaults(fn=cmd_sweep)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
