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
    remote grading backend keeps its own copy, so the two still have to be moved
    together; this only removes the image from that list.

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


def run_env_args(run: dict[str, str] | None) -> list[str]:
    """`docker run` -e flags carrying this episode's identity into the container.

    The in-image mcp-server reads these and forwards them to the oracle as
    FB-Run-* headers, which is how one run's twenty submissions are told apart
    from twenty runs' one each. Passed as container environment, never through
    the tool surface: the agent has no way to read or forge them, and no part of
    the challenge changes because they are set.

    Shared so the api arm and the vendor arms cannot drift on the variable names.
    """
    args: list[str] = []
    for key, value in (run or {}).items():
        if value:
            args += ["-e", f"BENCH_RUN_{key.upper()}={value}"]
    return args


class MCPClient:
    def __init__(self, bug_dir: str, workspace: str, *, image: str,
                 run: dict[str, str] | None = None):
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
               # Always fetch the latest published image. Without this, a stale
               # locally-cached <image>:latest is reused silently — and an old
               # image bakes an old mcp-server that still POSTs the retired
               # /grade?bug= endpoint (now 404). --pull=always keeps the baked
               # grade client in sync with the backend.
               "--pull=always",
               "--cidfile", self._cidfile,
               "--security-opt", "seccomp=unconfined",
               "-e", "BENCH_GRADE_REVEAL=1"]
        # Send grades somewhere other than the endpoint the image was baked
        # with, when the operator names one. mcp-server reads BENCH_GRADE_URL at
        # run time, so this redirects a published image without rebaking it --
        # which is what makes a local grading backend possible at all.
        #
        # Conditional on purpose. Unset means the argument is not passed and the
        # image keeps its own value, so an external user, and any run that does
        # not opt in, is bit-for-bit unaffected. Passing a default here instead
        # would repeat a failure this repo has already had: a local address
        # shipped as the default made every published image unable to grade, and
        # nothing raised -- the requests simply went nowhere.
        #
        # A localhost/127.0.0.1 URL is the one value that cannot be passed
        # through as written: inside the container it means the CONTAINER, so a
        # backend running on the host is unreachable and the grade silently goes
        # nowhere -- the same failure as above, arriving by a different route.
        # Rewriting it to the host-gateway alias, and publishing that alias, is
        # what makes "point it at my laptop" work. Other hosts pass through
        # untouched.
        grade_url = os.environ.get("BENCH_GRADE_URL")
        if grade_url:
            for local in ("127.0.0.1", "localhost"):
                if local in grade_url:
                    grade_url = grade_url.replace(local, "host.docker.internal")
                    cmd += ["--add-host", "host.docker.internal:host-gateway"]
                    break
            cmd += ["-e", f"BENCH_GRADE_URL={grade_url}"]
        cmd += sig_rules_args()
        cmd += run_env_args(run)
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

    def call(self, name: str, arguments: dict, meta: dict | None = None) -> Any:
        """Invoke a tool. `meta` rides alongside the call rather than inside it.

        `arguments` is what the model produced and is bound by the tool's schema.
        `_meta` is added here, after the model is done, so the agent can neither
        read it nor set it — which is the whole point: it carries facts about the
        episode (which turn this is) that the oracle wants and the agent must not
        be able to forge.
        """
        arguments = self._clamp_exec_timeout(name, arguments)
        params: dict = {"name": name, "arguments": arguments}
        if meta:
            params["_meta"] = meta
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
