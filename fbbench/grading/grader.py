"""Grade a single blob against the remote oracle — no agent, no LLM, no Docker.

This is the vendor-neutral grading entry point: feed it bytes from any source
(AFL++, libFuzzer, hand-crafted) and it POSTs them to the remote grading oracle,
which runs the official sanitizer-instrumented harness and returns the verdict.
Just an HTTP request — the answer key never leaves the oracle.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

FLAGS = ["reach", "crash", "differential", "class", "site"]

# Single source of truth for the remote grading oracle. Developers switch the
# endpoint HERE; env BENCH_GRADE_URL still overrides at runtime for staging.
DEFAULT_GRADE_URL = "https://nonretinal-arletha-arduous.ngrok-free.dev"


def grade_blob(bug_dir: Path, blob: Path, rounds: int = 1,
               timeout: int = 300) -> tuple[dict, float]:
    """POST `blob` to the remote oracle for `bug_dir`'s challenge.

    Returns (verdict, elapsed_s) where verdict is the oracle's JSON:
    {capabilities, capabilities_bestof, target_bug_found, agreed, evidence, ...}.
    The bug id is the challenge alias (the bug dir's own name) unless BENCH_BUG_ID
    overrides it; the oracle holds the answer key and rounds config server-side.
    """
    bug_id = os.environ.get("BENCH_BUG_ID") or Path(bug_dir).name
    grade_url = os.environ.get("BENCH_GRADE_URL", DEFAULT_GRADE_URL).rstrip("/")
    data = Path(blob).read_bytes()
    req = urllib.request.Request(
        f"{grade_url}/v1/challenges/{bug_id}/grade", data=data, method="POST",
        headers={"Content-Type": "application/octet-stream",
                 # Skip ngrok's browser interstitial so the JSON comes back clean.
                 "ngrok-skip-browser-warning": "true"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"grade oracle status {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"grade oracle unreachable ({grade_url}): {e.reason}") from None
    return out, time.time() - t0
