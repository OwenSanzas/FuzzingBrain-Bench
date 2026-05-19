#!/usr/bin/env python3
"""Grade every shipped bug's poc.bin through the MCP server.

Pass criterion: every flag in the bug's `capability_set` fires `fired`,
with `agreed: true` across the 3 randomized rounds.

Exit code 0 if all PASS, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "bin" / "mcp-server"


def find_bugs() -> list[tuple[str, Path]]:
    bugs = []
    for proj in sorted((REPO / "bugs").iterdir()):
        if not proj.is_dir():
            continue
        for sub in sorted(proj.iterdir()):
            if (sub / "binaries").is_dir() and (sub / "poc" / "poc.bin").is_file():
                bugs.append((sub.name, sub))
    return bugs


def grade(bug_id: str, bug_dir: Path) -> tuple[str, str, float]:
    ws = Path(tempfile.mkdtemp(prefix=f"fbb-{bug_id}-"))
    try:
        shutil.copy(bug_dir / "poc" / "poc.bin", ws / "poc.bin")
        K_b = yaml.safe_load((bug_dir / "bench.yaml").read_text())["capability_set"]
        req = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "grade", "arguments": {
                "path": str(ws / "poc.bin"),
                "options": {"round_count": 3},
            }},
        }
        env = os.environ.copy()
        env["BENCH_BUG_DIR"] = str(bug_dir.resolve())
        env["BENCH_WORKSPACE"] = str(ws)
        t0 = time.time()
        p = subprocess.run(
            [str(SERVER)], input=(json.dumps(req) + "\n").encode(),
            capture_output=True, env=env, timeout=240,
        )
        elapsed = time.time() - t0
        out = json.loads(p.stdout.decode().strip().splitlines()[-1])
        r = out["result"]["structuredContent"]
        caps = r.get("capabilities", {})
        fired = ",".join(c for c in ["reach", "crash", "class", "site"] if caps.get(c) == "fired")
        ok = all(caps.get(c) == "fired" for c in K_b) and r.get("agreed", False)
        return ("PASS" if ok else "FAIL", fired, elapsed)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main() -> int:
    if not SERVER.exists():
        print(f"error: {SERVER} not built. run `make mcp-server`", file=sys.stderr)
        return 2

    bugs = find_bugs()
    if not bugs:
        print("error: no shippable bugs found", file=sys.stderr)
        return 2

    print(f"Grading {len(bugs)} bugs through {SERVER.name}")
    print("=" * 72)

    n_pass = 0
    for bug_id, bd in bugs:
        try:
            status, fired, elapsed = grade(bug_id, bd)
        except Exception as e:
            status, fired, elapsed = f"ERR ({e})", "", 0.0
        if status == "PASS":
            n_pass += 1
        print(f"  [{status:4s}] {bug_id:38s} fired=[{fired}] ({elapsed:.0f}s)")

    print("=" * 72)
    print(f"\n  {n_pass}/{len(bugs)} PASS")
    return 0 if n_pass == len(bugs) else 1


if __name__ == "__main__":
    sys.exit(main())
