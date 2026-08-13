"""Line-delimited JSON-RPC 2.0 client for the FuzzingBrain Bench MCP server.

The server is the mcp-server baked into the public challenge image; we `docker
run` it and talk over its stdin/stdout. A narrow shim — just enough to drive the
6-tool contract.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from typing import Any

# Upper bound (seconds) on an exec tool call's timeout_s. A single blocking
# exec pins the whole episode (the client waits on the server's read), so a
# model that asks for a multi-hour timeout on a runaway command would stall a
# worker indefinitely. Clamped client-side so it applies even to the server
# baked into the (unrebuilt) challenge image.
EXEC_TIMEOUT_CAP_S = 300

# This checkout's crash-signature rules, and where they are mounted so the
# in-image grader uses them. See `sig_rules_args`.
SIG_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "grading", "signature.py")
SIG_RULES_IN_CONTAINER = "/opt/fbbench/signature.current.py"


def sig_rules_args() -> list[str]:
    """`docker run` flags making the in-image grader score with THIS checkout's
    crash-signature rules instead of the copy baked into the image.

    A self-contained image grades locally, and names each crash with the
    signature script `build_challenge` vendored into it when the image was
    built. That copy is frozen at build time, so a rules fix reaches a published
    image only by rebuilding and republishing all of them — and until it does,
    the image counts distinct crashes by one set of rules while everything
    downstream reads them by another. Mounting the current file closes that gap
    for every runner-driven episode, which is what a sweep is.

    Read-only, and deliberately so: the agent has exec in this container, and a
    writable scoring rule is one `printf` away from every crash being novel.

    Two things this does NOT cover, both by nature. An image driven directly by
    an external user gets its baked copy — nothing here runs for them. And the
    baked copy is what an external user gets, so the two still have to be moved
    together; this only removes runner-driven episodes from that list.

    Set BENCH_SIG_SCRIPT on the host to override (including to the baked path,
    to measure exactly what an external user would get).
    """
    if os.environ.get("BENCH_SIG_SCRIPT"):
        return ["-e", f"BENCH_SIG_SCRIPT={os.environ['BENCH_SIG_SCRIPT']}"]
    if not os.path.isfile(SIG_RULES):
        # Better to grade with the baked rules than to mount nothing at the path
        # we then point the server at: a missing script makes every crash
        # `<unsigned>`, which silently collapses them all into one.
        return []
    return ["-v", f"{SIG_RULES}:{SIG_RULES_IN_CONTAINER}:ro",
            "-e", f"BENCH_SIG_SCRIPT={SIG_RULES_IN_CONTAINER}"]


# The neutral <project>-NN alias for a bug dir (the image tag is named by it).
def _full_scan_alias(real_bug_dir: str) -> str:
    """A neutral `<project>-NN` handle for full-scan, replacing the descriptive
    bug_id (e.g. `libpng-zlib-inflate-uaf` -> `libpng-03`) so the identifier no
    longer names the bug. NN is the bug's stable 1-based position among its
    project's bundles (sorted). The project name is not a leak — the harness
    source reveals it anyway."""
    real = os.path.abspath(real_bug_dir)
    proj_dir = os.path.dirname(real)
    project = os.path.basename(proj_dir)
    me = os.path.basename(real)
    siblings = sorted(n for n in os.listdir(proj_dir)
                      if os.path.isfile(os.path.join(proj_dir, n, "bench.yaml")))
    idx = (siblings.index(me) + 1) if me in siblings else 1
    return f"{project}-{idx:02d}"


class MCPClient:
    def __init__(self, bug_dir: str, workspace: str, *, image: str):
        # Drive the PUBLIC challenge image's own mcp-server over stdio. The
        # challenge surface + BENCH_* are baked into the image, so what we measure
        # is byte-identical to what any external user runs. The container is
        # ephemeral (--rm) and self-contained.
        #
        # seccomp=unconfined lets the in-container mcp-server create the user+network
        # namespace exec() isolation needs (default Docker seccomp blocks
        # unshare(CLONE_NEWUSER)). exec'd children still get `-n`, so the agent's
        # shell cannot fetch the upstream issue or a reference PoC. The container
        # is ephemeral and answer-free, so this leaks nothing. BENCH_GRADE_REVEAL=1
        # marks the TRUSTED runner: the in-image grader returns the crash
        # signature so the runner can score, and the seal strips it before the
        # model sees the grade result. --cidfile lets us `docker cp` grade
        # candidates out of the live container.
        env = os.environ.copy()
        self._image = image
        self._cid_dir = tempfile.mkdtemp(prefix="fbcid-")
        self._cidfile = os.path.join(self._cid_dir, "cid")
        cmd = ["docker", "run", "-i", "--rm",
               # Always fetch the latest published image. Without this a stale
               # locally-cached <image>:latest is reused silently, and a stale
               # image bakes a stale harness and a stale grader — so a run would
               # be scored by rules nobody could see from this checkout.
               "--pull=always",
               "--cidfile", self._cidfile,
               "--security-opt", "seccomp=unconfined",
               "-e", "BENCH_GRADE_REVEAL=1"]
        cmd += sig_rules_args()
        cmd += [image, "mcp-server"]
        bug_dir, workspace = "/src", "/workspace"
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._id = 0
        self._lock = threading.Lock()
        self.bug_dir = bug_dir
        self.workspace = workspace
        # Drain stderr to a buffer so the pipe never fills.
        self._stderr_buf: list[bytes] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_buf.append(line)

    def initialize(self) -> dict:
        return self._call("initialize", {})

    def list_tools(self) -> list[dict]:
        return self._call("tools/list", {})["tools"]

    def call(self, name: str, arguments: dict) -> Any:
        """Invoke a tool with the arguments the model produced."""
        arguments = self._clamp_exec_timeout(name, arguments)
        params: dict = {"name": name, "arguments": arguments}
        resp = self._call("tools/call", params)
        return resp.get("structuredContent", resp)

    def copy_out(self, path: str, dest) -> bool:
        """`docker cp` a file the agent produced (a grade candidate) out of the
        live container to the host — the workspace lives inside the ephemeral
        container, so a host path check would always fail. True iff it landed."""
        if not self._cidfile:
            return False
        try:
            with open(self._cidfile) as f:
                cid = f.read().strip()
        except OSError:
            return False
        if not cid:
            return False
        try:
            r = subprocess.run(["docker", "cp", f"{cid}:{path}", str(dest)],
                               capture_output=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    def copy_in(self, src, path: str) -> bool:
        """`docker cp` a file from the host INTO the live container's workspace.

        The obvious way to stage a candidate is to base64 it into an exec()
        command, and that works right up until the blob is big: the encoded text
        becomes an argv entry, and argv is capped. Four of the corpus's own
        reference PoCs are over 400 KB (fwupd-01 is 2.1 MB), and every one of
        them failed with "argument list too long" — the write silently did not
        happen and grading then reported the candidate missing. Size must not
        decide whether an input can be graded, so the bytes travel as a file.
        """
        if not self._cidfile:
            return False
        try:
            with open(self._cidfile) as f:
                cid = f.read().strip()
        except OSError:
            return False
        if not cid:
            return False
        try:
            r = subprocess.run(["docker", "cp", str(src), f"{cid}:{path}"],
                               capture_output=True, timeout=120)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _clamp_exec_timeout(name: str, arguments: dict) -> dict:
        # Weak models routinely set an absurd exec timeout_s (e.g. 10000s on a
        # runaway `grep -R ..`), which blocks the episode for hours since the
        # client waits on the server's blocking read. Clamp it here so the fix
        # applies even to the server baked into the (unrebuilt) challenge image.
        # Copy so the transcript keeps the model's real request.
        if name != "exec":
            return arguments
        ts = arguments.get("timeout_s")
        if isinstance(ts, (int, float)) and ts > EXEC_TIMEOUT_CAP_S:
            arguments = {**arguments, "timeout_s": EXEC_TIMEOUT_CAP_S}
        return arguments

    def _call(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            assert self._proc.stdin is not None
            self._proc.stdin.write((json.dumps(req) + "\n").encode())
            self._proc.stdin.flush()
            assert self._proc.stdout is not None
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed stdout; stderr=" + b"".join(self._stderr_buf[-20:]).decode("utf-8", "replace"))
            resp = json.loads(line)
        if "error" in resp:
            err = resp["error"]
            raise MCPToolError(err.get("message", "tool error"), err.get("data"))
        return resp["result"]

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        if self._cid_dir:
            shutil.rmtree(self._cid_dir, ignore_errors=True)


class MCPToolError(Exception):
    def __init__(self, message: str, data: Any = None):
        super().__init__(message)
        self.data = data
