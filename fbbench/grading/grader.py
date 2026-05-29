"""Drive the MCP server's grade() oracle on a single blob — no agent, no LLM.

This is the vendor-neutral grading entry point: feed it bytes from any source
(AFL++, libFuzzer, hand-crafted, an LLM episode) and it runs the official
sanitizer-instrumented harness through the same three-round oracle the agent
sees. Consolidates the duplicate subprocess plumbing that fb-bench grade and
the regression smoke test each used to carry.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fbbench.paths import SERVER

FLAGS = ["reach", "crash", "class", "site"]


def grade_blob(bug_dir: Path, blob: Path, rounds: int = 3,
               server_bin: Path = SERVER, timeout: int = 300) -> tuple[dict, float]:
    """Grade `blob` against `bug_dir`'s oracle.

    Returns (structuredContent, elapsed_s) where structuredContent is the
    grade() result: {capabilities, agreed, rounds, evidence, ...}.
    """
    ws = Path(tempfile.mkdtemp(prefix=f"fb-{Path(bug_dir).name}-"))
    try:
        shutil.copy(blob, ws / "input.bin")
        req = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "grade", "arguments": {
                "path": str(ws / "input.bin"),
                "options": {"round_count": rounds},
            }},
        }
        env = os.environ.copy()
        env["BENCH_BUG_DIR"] = str(Path(bug_dir).resolve())
        env["BENCH_WORKSPACE"] = str(ws)
        t0 = time.time()
        p = subprocess.run([str(server_bin)],
                           input=(json.dumps(req) + "\n").encode(),
                           capture_output=True, env=env, timeout=timeout)
        elapsed = time.time() - t0
        out = json.loads(p.stdout.decode().strip().splitlines()[-1])
        return out["result"]["structuredContent"], elapsed
    finally:
        shutil.rmtree(ws, ignore_errors=True)
