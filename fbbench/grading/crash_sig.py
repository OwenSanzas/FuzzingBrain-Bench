"""Crash de-duplication: turn a harness's raw sanitizer output into a stable
crash *signature*, so an episode can count the number of DISTINCT crashes an
agent found rather than a fixed capability ladder.

A signature is `<crash-type>|<frame1>|<frame2>|...` where the frames are the top
few *application* stack frames (sanitizer / libFuzzer / allocator internals are
skipped, and consecutive identical frames — recursion — are collapsed). Two
inputs that fault the same way at the same place get the same signature and count
once; a fault of a different type, or at a different location, counts separately.
This mirrors how libFuzzer / ClusterFuzz dedup crashes (crash-type + top-N
frames), which is the de-facto standard for "unique crashes".

`crash_signature(harness_output)` returns the signature string, or None when the
output shows no fault (so a clean run never inflates the count).
"""
from __future__ import annotations

import re
from typing import Optional

# How many application frames define the crash location. 3 is the ClusterFuzz
# default: enough to separate distinct call sites, few enough that a deep shared
# tail doesn't split one bug into many. Tune here if dedup is too coarse/fine.
TOP_FRAMES = 3

# --- crash TYPE detection ---------------------------------------------------
# Ordered: the first match wins, most specific first. Each pattern's group(1)
# (lowercased, whitespace-collapsed) becomes the type token.
_TYPE_PATTERNS = [
    re.compile(r"ERROR: AddressSanitizer:\s+([a-zA-Z0-9_-]+)"),
    re.compile(r"ERROR: libFuzzer:\s+(out-of-memory|timeout|deadly signal)"),
    re.compile(r"(LeakSanitizer:\s+detected memory leaks)"),
    re.compile(r"UndefinedBehaviorSanitizer:\s+([a-zA-Z0-9_-]+)"),
    re.compile(r"runtime error:\s+([a-z][a-z0-9 '\-]+)"),   # UBSan, kind only
    re.compile(r"== Java Exception:\s+([\w.$]+)"),           # Jazzer
    re.compile(r"Exception in thread \"[^\"]*\"\s+([\w.$]+)"),
    re.compile(r"(?:^|\W)(Assertion)\b.*?failed"),
]

# Frames that are NOT the agent's bug: sanitizer runtime, allocator interceptors,
# and the fuzzer driver. Matched against the function name (and, for a couple,
# the file path) of a parsed frame.
_SKIP_FUNC = re.compile(
    r"^(__interceptor_|__asan|__ubsan|__lsan|__msan|__sanitizer|"
    r"operator new|operator delete|malloc|calloc|realloc|free|"
    r"LLVMFuzzer|fuzzer::)")
_SKIP_FILE = re.compile(r"compiler-rt|/sanitizer|libfuzzer|/asan/", re.I)

# Native ASan/libFuzzer frame:  #7  0xADDR in <func> <file>:<line>[:col]
_FRAME_NATIVE = re.compile(
    r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+(?P<func>.+?)\s+(?P<file>[^\s]+):\d+")
# Native frame with no source (module+offset only): still a frame, keep the func.
_FRAME_NATIVE_NOSRC = re.compile(
    r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+(?P<func>[^\s(]+)")
# JVM (Jazzer) frame:  at pkg.Class.method(File.java:line)
_FRAME_JVM = re.compile(r"\bat\s+(?P<func>[\w.$]+)\(")


def _norm_type(raw: str) -> str:
    t = " ".join(raw.strip().lower().split())
    # Collapse UBSan's specific phrasing to a stable kind (drop trailing values).
    t = re.sub(r"\s+of type .*$", "", t)
    return t


def _iter_frames(text: str):
    """Yield application frame keys (func or func@file) top-to-bottom, skipping
    sanitizer/allocator/fuzzer internals and collapsing consecutive duplicates
    (recursion). Prefers native frames; falls back to JVM frames if none."""
    out: list[str] = []
    for line in text.splitlines():
        m = _FRAME_NATIVE.search(line) or _FRAME_NATIVE_NOSRC.search(line)
        if m:
            func = m.group("func").strip()
            file = (m.groupdict().get("file") or "")
            if _SKIP_FUNC.search(func) or (file and _SKIP_FILE.search(file)):
                continue
            key = func
            if not out or out[-1] != key:
                out.append(key)
            continue
    if out:
        yield from out
        return
    # No native frames — try JVM frames.
    for line in text.splitlines():
        m = _FRAME_JVM.search(line)
        if m:
            key = m.group("func")
            if not out or out[-1] != key:
                out.append(key)
    yield from out


def crash_type(text: str) -> Optional[str]:
    """The crash type token, or None if the output shows no known fault marker."""
    for pat in _TYPE_PATTERNS:
        m = pat.search(text)
        if m:
            return _norm_type(m.group(1))
    return None


def crash_signature(harness_output: dict) -> Optional[str]:
    """Stable signature `<type>|<frame1>|...` for a faulting harness run, or None
    if the run did not fault. `harness_output` is the run_poc_on_harness payload:
    {stdout, stderr, exit_code, signal}."""
    if not isinstance(harness_output, dict):
        return None
    stderr = harness_output.get("stderr") or ""
    stdout = harness_output.get("stdout") or ""
    text = stderr + "\n" + stdout
    typ = crash_type(text)
    if typ is None:
        # No sanitizer/exception marker. A bare terminating signal WITH output is
        # still a crash (e.g. SIGSEGV with a partial trace); signal with no output
        # is the known ASan-startup flake and must NOT count (mirrors the oracle).
        sig = (harness_output.get("signal") or "").strip()
        if sig and (stderr.strip() or stdout.strip()):
            typ = f"signal:{sig.lower()}"
        else:
            return None
    frames = []
    for f in _iter_frames(text):
        frames.append(f)
        if len(frames) >= TOP_FRAMES:
            break
    return typ + "|" + "|".join(frames) if frames else typ + "|<no-frames>"
