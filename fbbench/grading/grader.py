"""Grade a single blob in the challenge image — no agent, no LLM, no network.

This is the vendor-neutral grading entry point: feed it bytes from any source
(AFL++, libFuzzer, honggfuzz, hand-crafted) and it runs them through the same
sanitizer-instrumented harness an episode is graded against, inside the same
container, and reports what the harness did.

It drives the image's own mcp-server over stdio — the one canonical runtime, so
a blob graded here and a blob graded mid-episode take byte-identical paths. The
image holds no answer key, so what comes back is what the harness printed plus
the crash's signature; there is no `solved` verdict to give.
"""
from __future__ import annotations

import time
from pathlib import Path

FLAGS = ["reach", "crash", "differential", "class", "site"]


def grade_blob(bug_dir: Path, blob: Path, image: str | None = None,
               timeout: int = 600) -> tuple[dict, float]:
    """Run `blob` through `bug_dir`'s challenge harness, in its image.

    Returns (verdict, elapsed_s). The verdict is what the in-image grader
    reports: `crashed`, the crash `signature` and its `class` when it faulted,
    and the raw `harness_output` behind both.

    The blob is copied into the container's workspace, which is the only place
    run_poc_on_harness accepts a candidate from. `docker cp`, not base64 through
    exec(): the encoded text would become an argv entry, and four of the corpus's
    own reference PoCs are large enough to blow past the argv cap (fwupd-01 is
    2.1 MB). That failure is silent — the write does not happen and grading then
    reports the candidate missing — so size must not decide what can be graded.
    """
    from fbbench.images import DEFAULT_IMAGE_PREFIX, DEFAULT_IMAGE_TAG
    from fbbench.runner.mcp_client import MCPClient, _full_scan_alias

    alias = _full_scan_alias(str(bug_dir))
    image = image or f"{DEFAULT_IMAGE_PREFIX}{alias}:{DEFAULT_IMAGE_TAG}"
    data = Path(blob).read_bytes()

    t0 = time.time()
    mcp = MCPClient(str(bug_dir), "/workspace", image=image)
    try:
        mcp.initialize()
        if not mcp.copy_in(Path(blob), "/workspace/candidate.bin"):
            raise RuntimeError("could not stage the candidate in the container")
        # docker cp lands the file as root; exec() runs as the agent uid, and the
        # grader reads it as root, so only the read bit has to be there.
        mcp.call("exec", {"cmd": "chmod 0644 /workspace/candidate.bin",
                          "timeout_s": 30})
        out = mcp.call("run_poc_on_harness", {"path": "/workspace/candidate.bin"})
    finally:
        mcp.close()

    ho = (out or {}).get("harness_output") or {}
    return {
        "crashed": bool(out.get("crashed")),
        "novelty": out.get("crash_novelty"),
        "signature": out.get("crash_signature"),
        "signature_text": out.get("crash_signature_text"),
        "klass": out.get("crash_class"),
        "exit_code": ho.get("exit_code"),
        "signal": ho.get("signal"),
        "stdout": ho.get("stdout") or "",
        "stderr": ho.get("stderr") or "",
        "duration_ms": out.get("duration_ms"),
    }, time.time() - t0


def graded_flags(caps: dict, declared: list[str] | None = None) -> list[str]:
    """The rungs an archived run was graded on, in ladder order.

    Nothing produces a capability map any more — the rungs past `crash` need an
    answer key, and no image carries one. This stays because runs recorded while
    they were still graded that way are still on disk and still render, and
    because the research eval protocol reads the same declared `capability_set`.

    Three of the corpus's challenges were graded on fewer than five rungs — a
    resource-exhaustion DoS has no machine-checkable crash site, so `reach` and
    `site` are not part of its capability_set. Scoring those out of five makes a
    complete solve read as partial (systemd-02 solves everything it has and shows
    2/5), which is why no denominator should be the constant 5.

    An "n/a" anywhere in caps is authoritative: those are exactly the rungs that
    were declined for this bug. Without one we cannot tell a real five-rung
    verdict from a cell that never graded anything — an untouched capability map
    is five `not_fired`, not an empty dict — so the declared capability_set
    answers instead. Any rung that actually fired is unioned in: a stale
    declaration must not produce a denominator smaller than the score above it.
    """
    if any(v == "n/a" for v in caps.values()):
        return [f for f in FLAGS if caps.get(f) not in (None, "n/a")]
    if declared:
        fired = {f for f in FLAGS if caps.get(f) == "fired"}
        return [f for f in FLAGS if f in declared or f in fired]
    return list(FLAGS)
