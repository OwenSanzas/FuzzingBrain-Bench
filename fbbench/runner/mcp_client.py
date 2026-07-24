"""Line-delimited JSON-RPC 2.0 client for the FuzzingBrain Bench MCP server.

The server is a Go subprocess; we talk over its stdin/stdout. This is a
narrow shim — just enough to drive the 6-tool contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any

import yaml

from fbbench.paths import REPO

# Upper bound (seconds) on an exec tool call's timeout_s. A single blocking
# exec pins the whole episode (the client waits on the server's read), so a
# model that asks for a multi-hour timeout on a runaway command would stall a
# worker indefinitely. Kept in sync with the cap in tools/mcp-server/exec.go.
EXEC_TIMEOUT_CAP_S = 300

# Cached library-source checkouts (repo @ vuln_commit), shared across episodes.
# gitignored. Each entry is the source tree at the buggy commit with .git removed,
# so the agent can read/grep the real (vulnerable) code but cannot walk history to
# the fix. This is a cache, not run output, so it lives at its own top-level
# .srccache/ rather than inside the output/ tree.
SRCCACHE = REPO / ".srccache"

# URLs in staged harness sources (e.g. a "see issues/5946" comment) point the
# agent at the upstream bug report. Network egress is already blocked, but we
# also redact the link text from the agent's view so it gets no pointer at all.
_URL_RE = re.compile(rb'https?://[^\s"\'<>)\]]+')

# Entries copied into the agent-facing sandbox bug view. Everything else in
# the real bug dir — grader/ (answer key), poc/ (reference solution), and
# binaries/ (ground-truth builds) — is deliberately withheld: the agent
# reasons from harness source and tests via grade(), which runs the trusted
# binaries from the oracle dir server-side.
#
# Deliberately NOT staged (they leak the solution):
#   - PROVENANCE.md  : discovery notes, root cause, exact crash site.
#   - Dockerfile     : `git clone <repo> && git checkout <vuln_commit>` — hands
#                      the agent the upstream repo+commit (the agent never
#                      builds; grade() uses the pre-built oracle binaries).
SANDBOX_ENTRIES = ("description.txt", "bench.yaml", "harness")

# Files stripped from any staged subtree (e.g. harness/PROVENANCE.md).
SANDBOX_IGNORE = ("PROVENANCE.md",)

# bench.yaml keys withheld from the agent: they identify the upstream report /
# repo / commit, which an agent could use to look up the fix or reference PoC.
# Everything the oracle needs at runtime (harness.*, capability_set) is kept.
_BENCH_SCRUB_TOP = ("upstream_report", "cve")
# repo/vuln_commit identify the upstream so the agent can't look up the fix;
# fix_commit/fix_patch (the patch-differential provenance) point AT the
# fix directly and must never reach the agent's view either.
_BENCH_SCRUB_TARGET = ("repo", "vuln_commit", "fix_commit", "fix_patch")


def _ignore_leaky(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n in SANDBOX_IGNORE]


def _ensure_source_cache(repo: str, commit: str) -> str | None:
    """Clone repo@commit (blobless), strip .git, cache it. Returns the cache
    path, or None if the source can't be fetched. One-time per (repo, commit)."""
    if not repo or not commit:
        return None
    key = hashlib.sha1(f"{repo}@{commit}".encode()).hexdigest()[:16]
    cache = SRCCACHE / key
    if (cache / ".ready").exists():
        return str(cache)
    SRCCACHE.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="srcclone-", dir=str(SRCCACHE))
    try:
        # blobless clone keeps the fetch small; checkout pulls only this commit's
        # blobs. --no-checkout first so we control which commit lands.
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                        "--quiet", repo, tmp], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        # vuln_commit is not always a sha: it can be a fork branch name
        # (fwupd -> origin/<branch>) or a release/version label (graaljs
        # "24.1.2" -> tag graal-24.1.2). Resolve to something checkout-able.
        ref = _resolve_ref(tmp, commit)
        if ref is None:
            raise ValueError(f"ref {commit!r} not found (not a sha/branch/tag)")
        subprocess.run(["git", "-C", tmp, "checkout", "--quiet", "--detach", ref],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=600)
        shutil.rmtree(os.path.join(tmp, ".git"), ignore_errors=True)
        open(os.path.join(tmp, ".ready"), "w").close()
        try:
            os.replace(tmp, cache)   # atomic same-fs move into place
        except OSError:
            # Another worker populated this same (repo, commit) concurrently
            # (many bugs share a repo). Theirs is fine — drop ours.
            shutil.rmtree(tmp, ignore_errors=True)
        return str(cache) if (cache / ".ready").exists() else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
            ValueError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[stage_source] could not fetch {repo}@{commit}: {e}", flush=True)
        return None


def _resolve_ref(repo_dir: str, commit: str) -> str | None:
    """Resolve vuln_commit to a checkout-able ref: a sha/tag as-is, else a fork
    branch (origin/<name>), else a tag whose name ends with the version label
    (e.g. "24.1.2" -> "graal-24.1.2")."""
    def ok(r: str) -> bool:
        return subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--verify", "--quiet", r + "^{commit}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if ok(commit):
        return commit
    if ok("origin/" + commit):
        return "origin/" + commit
    tags = subprocess.run(["git", "-C", repo_dir, "tag", "--list"],
                          capture_output=True, text=True).stdout.split()
    matches = [t for t in tags if t.endswith(commit)]
    return matches[0] if matches else None


def _stage_source(real_bug_dir: str, sandbox: str) -> None:
    """Stage the library source (repo @ vuln_commit) into <sandbox>/src/ so the
    agent can read/grep the real vulnerable code. Infrastructure-provided — the
    agent never clones or reaches the network itself. Best-effort: if the source
    can't be fetched the episode still runs (no src/)."""
    if os.environ.get("BENCH_STAGE_SOURCE", "1") == "0":
        return
    try:
        bench = yaml.safe_load(open(os.path.join(real_bug_dir, "bench.yaml"))) or {}
    except OSError:
        return
    tgt = bench.get("target", {}) or {}
    cache = _ensure_source_cache(tgt.get("repo", ""), tgt.get("vuln_commit", ""))
    if not cache:
        return
    dst = os.path.join(sandbox, "src")
    os.makedirs(dst, exist_ok=True)
    # hardlink-copy (cheap, shares inodes) when same fs; else a plain copy.
    if subprocess.run(["cp", "-al", cache + "/.", dst + "/"],
                      stderr=subprocess.DEVNULL).returncode != 0:
        subprocess.run(["cp", "-a", cache + "/.", dst + "/"], stderr=subprocess.DEVNULL)


def _redact_urls_in_tree(root: str) -> None:
    """Redact http(s) URLs from text files under a staged subtree."""
    for dirpath, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "rb") as fp:
                    data = fp.read()
            except OSError:
                continue
            if b"http://" not in data and b"https://" not in data:
                continue
            new = _URL_RE.sub(b"[redacted-url]", data)
            if new != data:
                with open(p, "wb") as fp:
                    fp.write(new)


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


# Source files whose leading block comment is stripped in full-scan (it often
# names the bug region / CVE cluster). Inline comments that explain the INPUT
# FORMAT are kept — the agent legitimately needs them to craft an input.
_SRC_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".java")
_ENTRYPOINT_MARKERS = ("LLVMFuzzerTestOneInput", "fuzzerTestOneInput")
# Neutral class name a JVM entrypoint class is renamed to in full-scan. The
# original name can describe the fault (e.g. `DecompressionBombFuzzer` names a
# decompression-bomb / memory-exhaustion bug); renaming only the file doesn't hide
# it because `public class <Name>`, build.sh `-DtargetClass=<Name>`, and bench.yaml
# `entrypoint: <Name>.method` all still spell it out. _stage_bench_yaml rewrites
# the entrypoint field to the same constant so the two stay consistent.
_JAVA_NEUTRAL_CLASS = "Harness"

# Author hint words that, in a comment, would hand the agent the answer
# ("/* this is where the vulnerability triggers */", "// found the bug here").
# The fuzzed-API code is kept; only comments matching this are dropped.
_HINT_RE = re.compile(
    r"(vulnerab|the bug|buggy|trigger|exploit|overflow|underflow|use-after|uaf|"
    r"oob|out-of-bound|null[- ]?deref|npd|memory ?leak|memleak|double-free|"
    r"the crash|the fault|the defect|negative|sanitiz|payload that|malicious|"
    r"upstream fuzzer|fuzz-[a-z-]+\.c|build script for)",
    re.I,
)


def _scrub_hint_comments(text: str) -> str:
    """Remove comments whose text would reveal the bug, leaving the code intact.
    Drops /* ... */ blocks and // tails (and # lines, for build.sh) that match
    _HINT_RE; non-matching comments and all code are preserved."""
    text = re.sub(r"/\*.*?\*/", lambda m: "" if _HINT_RE.search(m.group(0)) else m.group(0),
                  text, flags=re.S)
    out = []
    for ln in text.splitlines(keepends=True):
        i = ln.find("//")
        if i >= 0 and _HINT_RE.search(ln[i:]):
            code = ln[:i].rstrip()
            out.append(code + ("\n" if ln.endswith("\n") else ""))
            continue
        s = ln.lstrip()
        if s.startswith("#") and not s.startswith("#!") and _HINT_RE.search(ln):
            continue  # build.sh comment line that leaks
        out.append(ln)
    return "".join(out)


def _strip_leading_comment(text: str) -> str:
    """Drop a leading run of blank lines / // lines / /* ... */ blocks (the
    license + descriptive header) up to the first real code line. Comments
    further down (e.g. input-layout notes next to the parsing code) are kept."""
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s == "" or s.startswith("//"):
            i += 1
            continue
        if s.startswith("/*"):
            while i < n and "*/" not in lines[i]:
                i += 1
            i += 1  # consume the line containing */
            continue
        break
    return "\n".join(lines[i:]).lstrip("\n")


def _neutralize_java_entry_class(harness_dir: str, old_class: str) -> None:
    """full-scan: rename the JVM entrypoint class `old_class` (from bench.yaml's
    `entrypoint: <Class>.method`) to the neutral `_JAVA_NEUTRAL_CLASS`, so a
    descriptive class name (e.g. `DecompressionBombFuzzer`) can't name the fault.

    `old_class` is AUTHORITATIVE — do NOT guess it from the `fuzzerTestOneInput`
    marker, which also matches a reflective runner (PocRunner's
    `getMethod("fuzzerTestOneInput")`) and would rename the wrong file. Whole-word
    replaces the identifier across every staged harness file (the class source,
    build.sh's `-DtargetClass=`/compile path, any file naming it) and renames the
    declaring file to `<Neutral>.java` so `public class` still matches the filename.
    Edits only the staged copy; the oracle's own binary/bench.yaml are untouched,
    so grading is unaffected."""
    if not old_class or old_class == _JAVA_NEUTRAL_CLASS:
        return
    decl = re.compile(r'\bclass\s+' + re.escape(old_class) + r'\b')
    word = re.compile(r'\b' + re.escape(old_class) + r'\b')
    entry_file = None
    for root, _, files in os.walk(harness_dir):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                data = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if decl.search(data):
                entry_file = p          # the file that declares the class
            new = word.sub(_JAVA_NEUTRAL_CLASS, data)
            if new != data:
                with open(p, "w") as fp:
                    fp.write(new)
    if entry_file:                      # rename declaring file to <Neutral>.java
        new_path = os.path.join(os.path.dirname(entry_file),
                                _JAVA_NEUTRAL_CLASS + ".java")
        if os.path.abspath(new_path) != os.path.abspath(entry_file) \
                and not os.path.exists(new_path):
            os.rename(entry_file, new_path)


def _jvm_entry_class(real_bug_dir: str) -> str | None:
    """The JVM entrypoint CLASS from bench.yaml `entrypoint: <Class>.method` (only
    for java/jvm harnesses with an unpackaged, single-dot entrypoint), else None.
    Authoritative name for _neutralize_java_entry_class; must match the class
    rewrite in _stage_bench_yaml's entrypoint field."""
    try:
        data = yaml.safe_load(open(os.path.join(real_bug_dir, "bench.yaml"))) or {}
    except OSError:
        return None
    h = data.get("harness")
    if not isinstance(h, dict) or h.get("type") not in ("java", "jvm"):
        return None
    ep = h.get("entrypoint")
    if isinstance(ep, str) and ep.count(".") == 1:
        return ep.split(".", 1)[0]
    return None


def _neutralize_harness(harness_dir: str, bug_name: str | None = None,
                        alias: str | None = None,
                        entry_class: str | None = None) -> None:
    """full-scan: scrub the staged harness of everything that names or locates
    the bug, while leaving the fuzzed-API code intact (the agent needs it to know
    the input shape). Specifically: strip the leading license/descriptive header;
    drop any inline comment that would reveal the bug (`_scrub_hint_comments`,
    incl. build.sh `# ...` lines); replace the descriptive bug id with its neutral
    alias everywhere; drop note files (*.md); rename a descriptive JVM entrypoint
    class to a neutral one (`entry_class`, see _neutralize_java_entry_class); and
    rename a C/C++ entrypoint source to a neutral `harness.<ext>`."""
    if not os.path.isdir(harness_dir):
        return
    for root, _, files in os.walk(harness_dir):
        for fn in files:
            p = os.path.join(root, fn)
            if fn.endswith(".md"):       # NOTES/PROVENANCE next to the harness leak
                os.remove(p)
                continue
            # build.sh leaks benchmark-shaped build infra: a coverage-instrumented
            # twin build (partial-credit scoring), the debug/asan/coverage config
            # matrix, and — after the C entrypoint is renamed to harness.<ext> —
            # a dangling reference to the original descriptive filename. The agent
            # never builds (run_input runs the pre-built oracle harness), so drop
            # it entirely; harness.<ext> alone is a clean fuzz-target layout.
            if fn == "build.sh" or (fn.endswith(".sh") and root == harness_dir):
                os.remove(p)
                continue
            ext = os.path.splitext(fn)[1]
            if fn != "build.sh" and ext not in _SRC_EXTS:
                continue
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            new = txt
            if ext in _SRC_EXTS:
                new = _strip_leading_comment(new)
            new = _scrub_hint_comments(new)
            if bug_name and alias:
                new = new.replace(bug_name, alias)
            if new != txt:
                with open(p, "w") as fp:
                    fp.write(new)
    if entry_class:
        # JVM: rename the authoritative entrypoint class everywhere it's named
        # (a descriptive class like `DecompressionBombFuzzer` names the fault).
        _neutralize_java_entry_class(harness_dir, entry_class)
        return
    # C/C++: DO NOT rename the entrypoint source. The oracle's pre-built binary
    # carries the ORIGINAL filename in its debug info, so every sanitizer stack
    # frame prints it (e.g. `datafile_fuzzer.c:87`) no matter what we call the
    # staged copy — renaming the copy to harness.<ext> only creates a visible/
    # crash-trace MISMATCH that reads as a repackaged benchmark. The C filename
    # (naming the fuzzed API, which the harness body reveals anyway) is not a
    # bug-locating hint, so keeping it costs nothing and removes the seam.


def _stage_bench_yaml(src: str, dst: str, full_scan: bool = False,
                      alias: str | None = None) -> None:
    """Copy bench.yaml with upstream/repo/commit identifiers stripped.

    In full_scan mode the fields that name or categorize the bug are also removed:
    `title` (names the class + function), `capability_set` (reveals the fault
    class — e.g. no `crash` => a leak), and the descriptive `bug_id` is replaced
    by a neutral `<project>-NN` alias. Otherwise these would hand back the very
    description full_scan is meant to withhold.
    """
    data = yaml.safe_load(open(src)) or {}
    for k in _BENCH_SCRUB_TOP:
        data.pop(k, None)
    if full_scan:
        # Rebuild a MINIMAL fuzz-target manifest holding only the build/harness
        # facts an ordinary fuzzing setup would expose. Everything that frames
        # this as a catalogued benchmark case is dropped: bug_id (the case
        # alias), harness.provenance ("fuzzingbrain"), status ("fixed"),
        # reproducibility snapshot metadata, and title/capability_set/notes. The
        # result reads like a normal project's harness descriptor, not a graded
        # benchmark entry.
        h = data.get("harness") or {}
        ep = h.get("entrypoint")
        # A JVM entrypoint `<Class>.method` can name the fault via the class
        # (e.g. `DecompressionBombFuzzer`); _neutralize_harness renames it to
        # _JAVA_NEUTRAL_CLASS in the staged harness, so keep the manifest in sync.
        if (isinstance(ep, str) and h.get("type") in ("java", "jvm")
                and ep.count(".") == 1):
            ep = f"{_JAVA_NEUTRAL_CLASS}.{ep.split('.', 1)[1]}"
        slim: dict = {"project": data.get("project"),
                      "target": {"language": (data.get("target") or {}).get("language")}}
        # Keep only what the agent needs to understand the target (type +
        # entrypoint + invocation). rss_limit_mb / timeout_s are run-limit knobs
        # that read as benchmark/OSS-Fuzz config in a manifest, and the agent-side
        # setup() never surfaces them, so drop them from the staged manifest.
        harness = {k: h[k] for k in ("type", "entrypoint", "invocation")
                   if isinstance(h, dict) and h.get(k) is not None}
        if ep is not None:
            harness["entrypoint"] = ep
        slim["harness"] = harness
        with open(dst, "w") as fp:
            yaml.safe_dump(slim, fp, sort_keys=False)
        return
    # Non-full-scan (internal hinted view): keep the rich manifest, only stripping
    # the upstream repo/commit identifiers and swapping in the neutral alias.
    if alias:
        data["bug_id"] = alias
    tgt = data.get("target")
    if isinstance(tgt, dict):
        for k in _BENCH_SCRUB_TARGET:
            tgt.pop(k, None)
    with open(dst, "w") as fp:
        yaml.safe_dump(data, fp, sort_keys=False)


def stage_bug_view(real_bug_dir: str, full_scan: bool = False) -> str:
    """Build a per-episode sandbox dir holding only agent-safe entries.

    Returns the sandbox path; the caller passes it as BENCH_BUG_DIR and the
    real bug dir as BENCH_ORACLE_DIR. Withheld: grader/, poc/, binaries/,
    PROVENANCE.md, Dockerfile, and upstream/repo/commit fields of bench.yaml.

    full_scan mode additionally withholds description.txt and, from bench.yaml,
    the title / capability_set / notes, and replaces the descriptive bug_id with
    a neutral `<project>-NN` alias — the agent gets only the harness (the fuzz
    target) and must discover the fault with no statement of what or where it is.
    """
    sandbox = tempfile.mkdtemp(prefix="fbbench-bugview-")
    # mkdtemp is 0700; the agent's exec() may run under a different uid
    # (Tier 2 privsep), so make the view traversable/readable.
    os.chmod(sandbox, 0o755)
    alias = _full_scan_alias(real_bug_dir)   # neutral bug_id in all modes
    entry_class = _jvm_entry_class(real_bug_dir) if full_scan else None
    entries = SANDBOX_ENTRIES
    if full_scan:
        entries = tuple(e for e in entries if e != "description.txt")
    for name in entries:
        src = os.path.join(real_bug_dir, name)
        if not os.path.exists(src):
            continue
        # In full-scan the manifest is renamed bench.yaml -> target.yaml: the
        # literal filename "bench.yaml" is itself a benchmark tell to a wary agent.
        out_name = "target.yaml" if (full_scan and name == "bench.yaml") else name
        dst = os.path.join(sandbox, out_name)
        if name == "bench.yaml":
            _stage_bench_yaml(src, dst, full_scan=full_scan, alias=alias)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, ignore=_ignore_leaky)
            _redact_urls_in_tree(dst)
            if full_scan and name == "harness":
                _neutralize_harness(dst, bug_name=os.path.basename(
                    os.path.normpath(real_bug_dir)), alias=alias,
                    entry_class=entry_class)
        else:
            shutil.copy2(src, dst)
    # No description.txt is staged: setup() ships no task/description field (the
    # task is conveyed entirely by the system prompt), and the Go server's old
    # leaky synthDescription fallback has been removed, so there is nothing to
    # suppress here.
    # Stage the real (vulnerable) library source as infrastructure — all modes.
    _stage_source(real_bug_dir, sandbox)
    return sandbox


class MCPClient:
    def __init__(self, server_bin: str, bug_dir: str, workspace: str,
                 oracle_dir: str | None = None, image: str | None = None):
        env = os.environ.copy()
        self._image = image
        self._cidfile: str | None = None
        self._cid_dir: str | None = None
        if image:
            # Canonical path: drive the PUBLIC challenge image's own mcp-server
            # over stdio. The challenge surface + BENCH_* (incl. the remote
            # BENCH_GRADE_URL) are baked into the image, so what we measure here
            # is byte-identical to what any external user runs — no local-vs-Docker
            # divergence. The container is ephemeral (--rm) and self-contained.
            #
            # seccomp=unconfined lets the in-container mcp-server create the
            # user+network namespace that exec() isolation needs (the default
            # Docker seccomp profile blocks unshare(CLONE_NEWUSER)). Without it,
            # exec() would be REFUSED inside the container and the agent couldn't
            # compile/run test inputs. With it, exec'd children still get `-n`
            # (no network — they cannot brute-force the remote oracle), while the
            # server's own grade() call keeps the container's network. The
            # container is ephemeral and answer-free, so this relaxation leaks
            # nothing. (--security-opt is a `docker run` flag — must precede the
            # image name.)
            # BENCH_GRADE_REVEAL=1 marks this as the TRUSTED runner: the in-image
            # mcp-server returns the verdict (capabilities/evidence) so the runner
            # can score, then strips it before the model sees the grade result.
            # Codex / sealed images do NOT set it, so grade() returns only
            # harness_output there (no verdict leak to the agent).
            # --cidfile lets us `docker cp` grade candidates out of the live
            # container (the agent's inputs live in the container's /workspace,
            # unreachable by a host-side path check). docker requires the file to
            # not pre-exist, so we hand it a fresh path inside a temp dir.
            self._cid_dir = tempfile.mkdtemp(prefix="fbcid-")
            self._cidfile = os.path.join(self._cid_dir, "cid")
            cmd = ["docker", "run", "-i", "--rm",
                   "--cidfile", self._cidfile,
                   "--security-opt", "seccomp=unconfined",
                   "-e", "BENCH_GRADE_REVEAL=1",
                   image, "mcp-server"]
            bug_dir, workspace = "/src", "/workspace"
        else:
            # Dev/local path: a host mcp-server graded against the local oracle.
            env["BENCH_BUG_DIR"] = bug_dir
            env["BENCH_WORKSPACE"] = workspace
            # Grader reads expected.yaml + binaries from the oracle dir; the agent
            # never sees it. Defaults to bug_dir for back-compat when unset.
            env["BENCH_ORACLE_DIR"] = oracle_dir or bug_dir
            env["BENCH_GRADE_REVEAL"] = "1"  # trusted runner: see note above
            cmd = [server_bin]
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
        """Copy a file the agent produced (a grade candidate) to the host.

        In the canonical docker path the workspace lives inside the container, so
        a host os.path check always fails — `docker cp` from the live container is
        the only way to persist the PoC. In the dev/local path the file is already
        on the host, so a plain copy suffices. Returns True iff the file landed.
        """
        dest = str(dest)
        if self._image:
            cidfile = self._cidfile
            if not cidfile:
                return False
            try:
                with open(cidfile) as f:
                    cid = f.read().strip()
            except OSError:
                return False
            if not cid:
                return False
            try:
                r = subprocess.run(["docker", "cp", f"{cid}:{path}", dest],
                                   capture_output=True, timeout=30)
                return r.returncode == 0
            except Exception:
                return False
        try:
            if os.path.isfile(path):
                shutil.copy2(path, dest)
                return True
        except OSError:
            pass
        return False

    @staticmethod
    def _clamp_exec_timeout(name: str, arguments: dict) -> dict:
        # Weak models routinely set an absurd exec timeout_s (e.g. 10000s on a
        # runaway `grep -R ..`), which blocks the episode for hours since the
        # client waits on the server's blocking read. Clamp it here so the fix
        # applies even to the server baked into the challenge docker image
        # (which we don't rebuild). Server-side exec.go enforces the same cap
        # for --local runs. Copy so the transcript keeps the model's real request.
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
