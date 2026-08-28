"""The generic external-agent arm: run ANY agent the bench does not contain.

    fb-bench run <bugs> --agent path/to/agent.yaml

There is no per-agent code here. An agent is described by a small manifest and
lives in its own repository; this arm gives it the one contract every agent
plugs into and grades what it produced -- the same in-image grader every other
arm uses, so a crash counted here means what it means everywhere.

The contract, the whole waist between bench and agent:

    1. a directory of the challenge source     (this arm stages it from the
                                                 sealed image; the answer is not
                                                 in it, and that is asserted)
    2. a `submit <file>` command in that dir    (this arm provides it; it runs
                                                 the candidate against the sealed
                                                 harness and returns the verdict)

The agent is a command run in that directory. It never touches Docker, never
learns the image name, never learns which version it is looking at -- the source
it reads and the harness its candidate runs on both come from the one sealed
image, so they cannot disagree.

Manifest (YAML or JSON):

    name: fbagent
    command: >
      omp -p "{opening}" --tools read,glob,grep,bash
      --system-prompt @fuzzing-brain.md --no-lsp --no-skills --no-session
      --auto-approve --max-time {timeout}
    network: blocked            # or: allowed
    shell_env: OMP_SHELL_PATH   # the env var the agent reads its shell from;
                                # the bench points it at the sandbox wrapper so
                                # the agent's shell inherits the masked Docker
                                # socket and (blocked) the empty net namespace

Template fields in `command`: {workspace} {timeout} {opening} {submit}.
`@path` inside the command is read relative to the manifest and inlined, so a
long system prompt lives in its own file next to the manifest.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fbbench.grading import find_bug, grade_blob
from fbbench.images import challenge_image
from fbbench.runner.mcp_client import _full_scan_alias

DEFAULT_OPENING = (
    "Read the harness to learn the input format, follow it into the source to "
    "find a fault it can reach, then build a candidate input and run it with "
    "./submit. Keep going until one crashes."
)


# --------------------------------------------------------------- the manifest

class Manifest:
    """What the bench needs to know to run one external agent."""

    def __init__(self, data: dict, base: Path):
        self.base = base
        self.name = str(data.get("name") or base.stem)
        self.command = str(data["command"]).strip()
        self.network = str(data.get("network", "blocked")).lower()
        self.shell_env = data.get("shell_env")
        if "command" not in data:
            raise ValueError(f"{base}: manifest has no `command`")

    @staticmethod
    def _search_dirs() -> list[Path]:
        """Where a bare agent name is looked up, most specific first.

        Keeps the bench free of agent code: an agent registers by dropping its
        manifest (or a symlink to it) into one of these, or by pointing
        $FBBENCH_AGENTS at the directory it already lives in.
        """
        dirs: list[Path] = []
        for d in os.environ.get("FBBENCH_AGENTS", "").split(os.pathsep):
            if d.strip():
                dirs.append(Path(d).expanduser())
        dirs.append(Path.home() / ".config" / "fbbench" / "agents")
        dirs.append(Path(__file__).resolve().parents[2] / "agents")
        return dirs

    @classmethod
    def resolve(cls, value: str) -> Path:
        """A path is used as-is; a bare name is found on the search path."""
        p = Path(value).expanduser()
        if p.is_file():
            return p.resolve()
        if "/" not in value and not value.endswith((".yaml", ".yml", ".json")):
            for d in cls._search_dirs():
                for cand in (f"{value}.agent.yaml", f"{value}.agent.yml",
                             f"{value}.yaml", f"{value}.json", f"{value}/agent.yaml"):
                    hit = d / cand
                    if hit.is_file():
                        return hit.resolve()
        raise FileNotFoundError(
            f"no agent manifest for {value!r}. Give a path, or register a name: "
            "put <name>.agent.yaml in ~/.config/fbbench/agents/ (or a dir named "
            "by $FBBENCH_AGENTS).")

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        p = cls.resolve(str(path))
        raw = p.read_text()
        try:
            import yaml
            data = yaml.safe_load(raw)
        except Exception:
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{p}: manifest must be a mapping")
        return cls(data, p.parent)

    @property
    def allow_network(self) -> bool:
        return self.network in ("allowed", "allow", "on", "true", "1")

    def render(self, **fields) -> str:
        """The command line, with @file references inlined and {fields} filled."""
        cmd = self.command
        # @path -> the file's contents, quoted; resolved next to the manifest.
        out, i = [], 0
        for token in shlex.split(cmd):
            if token.startswith("@"):
                ref = (self.base / token[1:]).resolve()
                out.append(ref.read_text().strip() if ref.is_file() else token)
            else:
                out.append(token)
        rendered = []
        for token in out:
            for k, v in fields.items():
                token = token.replace("{" + k + "}", str(v))
            rendered.append(token)
        return rendered  # a list argv, already split


# ------------------------------------------------------------- the sandbox

def _write_sandbox_shell(root: Path, allow_network: bool) -> Path:
    """A shell that masks the Docker socket and, unless network is allowed,
    runs in an empty net namespace. The agent's tools inherit both -- enforced
    by the kernel, not by the prompt -- so an agent that tries to read the
    sealed answer out of the image, or fetch a published PoC, simply cannot."""
    sh = root / "sandbox-sh"
    net = "1" if not allow_network else "0"
    sh.write_text(
        "#!/bin/bash\n"
        "set -u\n"
        "inner='\n"
        "  mount --bind /dev/null /var/run/docker.sock 2>/dev/null || true\n"
        f"  if [ \"{net}\" = \"1\" ]; then ip link set lo up 2>/dev/null || true; fi\n"
        "  exec /bin/bash \"$@\"\n"
        "'\n"
        f"if [ \"{net}\" = \"1\" ]; then\n"
        "  exec unshare --mount --net --user --map-root-user /bin/bash -c \"$inner\" -- \"$@\"\n"
        "fi\n"
        "exec unshare --mount --user --map-root-user /bin/bash -c \"$inner\" -- \"$@\"\n"
    )
    sh.chmod(0o755)
    return sh


# ------------------------------------------------------------- stage + submit

def stage(image: str, workspace: Path) -> None:
    """Copy the public challenge out of the sealed image, and refuse to proceed
    if the answer came with it."""
    workspace.mkdir(parents=True, exist_ok=True)
    cid = subprocess.run(["docker", "create", image], capture_output=True,
                         text=True, timeout=120)
    if cid.returncode != 0:
        raise RuntimeError(f"docker create {image}: {cid.stderr.strip()[:200]}")
    container = cid.stdout.strip()
    try:
        cp = subprocess.run(["docker", "cp", f"{container}:/challenge/.",
                             str(workspace)], capture_output=True, text=True, timeout=300)
        if cp.returncode != 0:
            raise RuntimeError(f"docker cp: {cp.stderr.strip()[:200]}")
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=120)
    strays = []
    for pat in ("**/oracle.yaml", "**/expected.yaml", "**/binaries/vuln/**"):
        strays += [str(p) for p in workspace.glob(pat)]
    if strays:
        raise RuntimeError(f"answer files present in staged workspace: {strays[:3]}")


class Judge:
    """The `submit` the agent calls: grades a candidate on the sealed harness,
    returns the verdict live, and remembers every candidate for the score.

    The grader is the bench's own `grade_blob` -- the same one that produces the
    final number -- so the feedback the agent iterates against and the score it
    is given can never diverge.
    """

    def __init__(self, workspace: Path, bug_dir: Path):
        self.ws = workspace
        self.bug_dir = bug_dir
        self.req = workspace / ".fbbench" / "req"
        self.res = workspace / ".fbbench" / "res"
        self.blobs = workspace / ".fbbench" / "blobs"
        self.log: list[dict] = []
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self) -> None:
        for d in (self.req, self.res, self.blobs):
            d.mkdir(parents=True, exist_ok=True)
        s = self.ws / "submit"
        s.write_text(
            "#!/bin/bash\n"
            "# Run a candidate input against the challenge harness: ./submit <file>\n"
            "set -u\n"
            'if [ $# -ne 1 ] || [ ! -f "$1" ]; then echo \"usage: ./submit <file>\" >&2; exit 2; fi\n'
            'h="$(cd "$(dirname "$0")" && pwd)"; id="$(date +%s%N)-$$"\n'
            'cp -- "$1" "$h/.fbbench/req/$id"\n'
            'for _ in $(seq 1 900); do [ -f "$h/.fbbench/res/$id" ] && { cat "$h/.fbbench/res/$id"; exit 0; }; sleep 0.2; done\n'
            'echo "submit: no verdict in time" >&2; exit 1\n')
        s.chmod(0o755)
        (self.ws / "try_poc").write_text('#!/bin/bash\nexec "$(dirname "$0")/submit" "$@"\n')
        (self.ws / "try_poc").chmod(0o755)
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            for cand in sorted(self.req.glob("*")):
                if not cand.is_file():
                    continue
                shutil.copy2(cand, self.blobs / cand.name)
                try:
                    verdict, _ = grade_blob(self.bug_dir, cand)
                    crashed = bool(verdict.get("crashed"))
                    sig = verdict.get("signature") or ""
                    detail = (f"crash: {sig}" if crashed else "clean: no fault")
                except Exception as e:  # a grading failure must be visible, not a silent clean
                    crashed, sig, detail = False, "", f"error: {e}"
                self.log.append({"blob": cand.name, "size": cand.stat().st_size,
                                 "crashed": crashed, "signature": sig})
                (self.res / cand.name).write_text(detail + "\n")
                cand.unlink(missing_ok=True)
            self._stop.wait(0.2)

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=10)

    def signatures(self) -> set[str]:
        return {e["signature"] or "crash|<unnamed>" for e in self.log if e["crashed"]}


# ---------------------------------------------------------------- the cell

def run_cell(cell_dir: Path, bug: str, model: str, timeout_s: int,
             max_turns: int = 100, *, manifest: Manifest, api_key: str | None = None,
             preserve_pocs: bool = True) -> dict | None:
    """Stage a challenge, run the external agent over it, grade what it left."""
    cell_dir = Path(cell_dir)
    real = find_bug(bug)
    if not real:
        return {"error": f"bug not found: {bug}"}
    alias = _full_scan_alias(str(real))
    image = challenge_image(alias)

    root = Path(tempfile.mkdtemp(prefix=f"ext-{alias}-"))
    ws = root / "workspace"
    try:
        try:
            stage(image, ws)
        except RuntimeError as e:
            return {"error": str(e)}

        judge = Judge(ws, Path(real))
        judge.start()
        shell = _write_sandbox_shell(root, manifest.allow_network)

        argv = manifest.render(workspace=str(ws), timeout=str(timeout_s),
                               opening=DEFAULT_OPENING, submit="./submit")
        env = dict(os.environ)
        # The manifest's own directory goes on PYTHONPATH, so a Python agent can
        # `python3 -m its_package.run` from the staged workspace without knowing
        # an absolute path. Harmless to agents that do not import anything.
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(manifest.base), env.get("PYTHONPATH", "")) if p)
        if manifest.shell_env:
            env[manifest.shell_env] = str(shell)
        env["SHELL"] = str(shell)
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key

        started = time.time()
        terminated = "done"
        try:
            proc = subprocess.run(argv, cwd=str(ws), env=env, capture_output=True,
                                  text=True, timeout=timeout_s + 300)
            log = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            terminated = "wall-clock"
            log = (e.stdout or "") if isinstance(e.stdout, str) else ""
        duration = time.time() - started
        judge.stop()

        cell_dir.mkdir(parents=True, exist_ok=True)
        (cell_dir / "agent.log").write_text(log)
        if preserve_pocs:
            for e in judge.log:
                sub = cell_dir / "pocs" / ("crashed" if e["crashed"] else "clean")
                sub.mkdir(parents=True, exist_ok=True)
                src = judge.blobs / e["blob"]
                if src.is_file():
                    shutil.copy(src, sub / e["blob"])
        sigs = judge.signatures()
        best = next((judge.blobs / e["blob"] for e in judge.log if e["crashed"]), None)
        if best and best.is_file():
            shutil.copy(best, cell_dir / "best_blob")

        score = {
            "bug_id": bug, "model": model, "seed": 0,
            "unique_crashes": len(sigs), "crash_signatures": sorted(sigs),
            "score": len(sigs), "grading": "in-image",
            "terminated_reason": terminated, "duration_s": round(duration, 1),
            "blobs_written": len(judge.log), "max_turns": max_turns,
            "agent": manifest.name,
            "network": "allowed" if manifest.allow_network else "blocked",
            "tokens_used": None, "total_usd": None,
        }
        (cell_dir / "score.json").write_text(json.dumps(score, indent=2))
        (cell_dir / "cost.json").write_text(json.dumps(
            {"model": model, "agent": manifest.name, "pricing_source": "external",
             "total_usd": None}, indent=2))
        return score
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit("the external arm has no standalone CLI.\n"
             "use:  fb-bench run <bugs> --agent path/to/agent.yaml")
