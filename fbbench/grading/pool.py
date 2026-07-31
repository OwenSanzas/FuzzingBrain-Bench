"""Ask the oracle what a sweep found.

Scores live oracle-side, not here, and that is deliberate: computing "how many
DISTINCT crashes did this run find" requires knowing which crashes are the same,
and a runner that knows that can leak it back into the episode it is driving.
See the side-channel note in the grader's crash-pool design.

This module is for the REPORT, which runs after every episode has ended. Reading
a score here cannot reach an agent.

Everything is best-effort: a report over an old output tree, or one taken while
the oracle is down, should still render the parts it can rather than fail.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fbbench.grading.grader import DEFAULT_GRADE_URL


def batch_uid(exp_dir: Path) -> str | None:
    """The sweep id recorded when this output tree was created.

    Written by run_matrix; absent for trees produced before batches existed, and
    for single cells run outside a matrix.
    """
    p = Path(exp_dir) / "batch.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text()).get("batch_uid") or None
    except (OSError, json.JSONDecodeError):
        return None


def batch_score(uid: str, timeout: int = 30) -> dict | None:
    """Per-model and per-challenge unique-crash counts for one sweep, or None.

    None means "could not ask" — no oracle, no network, unknown batch. Callers
    render without the column rather than treating it as a zero, because a zero
    is a claim about the run and this is a claim about the lookup.
    """
    url = os.environ.get("BENCH_GRADE_URL", DEFAULT_GRADE_URL).rstrip("/")
    req = urllib.request.Request(
        f"{url}/v1/batches/{uid}/score",
        headers={"ngrok-skip-browser-warning": "true"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def per_challenge_crashes(score: dict | None) -> dict[tuple[str, str], dict]:
    """Index a batch score by (model, bug) so a report can look a cell up."""
    if not score:
        return {}
    return {(r["model"], r["bug_alias"]): r for r in score.get("per_challenge", [])}
