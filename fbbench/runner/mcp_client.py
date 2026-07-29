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
        # challenge surface + BENCH_* (incl. the remote BENCH_GRADE_URL) are baked
        # into the image, so what we measure is byte-identical to what any external
        # user runs. The container is ephemeral (--rm) and self-contained.
        #
        # seccomp=unconfined lets the in-container mcp-server create the user+network
        # namespace exec() isolation needs (default Docker seccomp blocks
        # unshare(CLONE_NEWUSER)). exec'd children still get `-n` (no network — they
        # cannot brute-force the remote oracle); the server's own run_poc_on_harness() call keeps
        # the container's network. The container is ephemeral and answer-free, so
        # this leaks nothing. BENCH_GRADE_REVEAL=1 marks the TRUSTED runner: the
        # in-image mcp-server returns the verdict so the runner can score, then
        # strips it before the model sees the grade result. --cidfile lets us
        # `docker cp` grade candidates out of the live container.
        env = os.environ.copy()
        self._image = image
        self._cid_dir = tempfile.mkdtemp(prefix="fbcid-")
        self._cidfile = os.path.join(self._cid_dir, "cid")
        cmd = ["docker", "run", "-i", "--rm",
               "--cidfile", self._cidfile,
               "--security-opt", "seccomp=unconfined",
               "-e", "BENCH_GRADE_REVEAL=1"]
        # Point the in-container grader at a local grade server when the host sets
        # BENCH_GRADE_URL. A localhost/127.0.0.1 URL means "the host", which the
        # container reaches via the host-gateway alias, so rewrite it and publish
        # the alias; other hosts pass through untouched. Without this the baked-in
        # remote BENCH_GRADE_URL (the ngrok oracle) is used.
        grade_url = os.environ.get("BENCH_GRADE_URL")
        if grade_url:
            for local in ("127.0.0.1", "localhost"):
                if local in grade_url:
                    grade_url = grade_url.replace(local, "host.docker.internal")
                    cmd += ["--add-host", "host.docker.internal:host-gateway"]
                    break
            cmd += ["-e", f"BENCH_GRADE_URL={grade_url}"]
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
        arguments = self._clamp_exec_timeout(name, arguments)
        resp = self._call("tools/call", {"name": name, "arguments": arguments})
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
