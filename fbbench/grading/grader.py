"""Grade a single blob in the challenge image — no agent, no LLM, no network.

This is the vendor-neutral grading entry point: feed it bytes from any source
(AFL++, libFuzzer, honggfuzz, hand-crafted) and it runs them through the same
sanitizer-instrumented harness an episode is graded against, inside the same
container, and reports what the harness did.

It drives the image's own mcp-server over stdio — the one canonical runtime, so
a blob graded here and a blob graded mid-episode take byte-identical paths. The
image holds no answer key, so what comes back is what the harness printed plus
the crash's signature.
"""
from __future__ import annotations

import time
from pathlib import Path


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
    from fbbench.images import challenge_image
    from fbbench.runner.mcp_client import MCPClient, _full_scan_alias

    alias = _full_scan_alias(str(bug_dir))
    image = image or challenge_image(alias)
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
