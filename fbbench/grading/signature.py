"""Turn one harness run's raw output into a stable crash signature.

A signature answers exactly one question: *are these two crashes the same bug?*
What is being identified is a CRASH, not a defect: two faults of different
kinds, or at different places, are different crashes, and whether they share an
underlying bug is not a question this layer answers. So everything that varies
between runs of the SAME fault (heap addresses, pids, allocation sizes, timings)
is stripped, while everything that locates the fault — class, function, file,
line — is kept.

    canon_sig = sha256([class, [func, file, line], [func, file, line]])

The rules below are not guesses: each one was derived by running this module's
prototype over all 68 crash logs in the answers repo, and each failure it fixes
is named in the comment. See `_internal/CRASH-DEDUP.md` for the full write-up.

This lives in Python, not in the Go judge, on purpose. The raw output is
archived, so a rule change here re-derives every signature a pool has ever held
without re-running a single harness — and these rules WILL change, which is a
bad fit for a compiled binary. The judge decides *whether* a run crashed; this
decides *which* crash it was.

Two callers share this ONE file, which is why it is stdlib-only and standalone:

  * the grading backend, scoring sweeps server-side;
  * the self-contained challenge image, where it is baked at
    /opt/fbbench/signature.py and shelled out to by mcp-server (see the CLI at
    the bottom of this file).

Two implementations of "are these the same crash?" that drift apart produce
scores nobody can compare, so this is copied, never reimplemented. Keep it
importable with no package around it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

# How many application frames identify a crash site. Two — the faulting function
# and its immediate caller — is the smallest value that still tells two paths
# into the same defect apart, which is deliberate: reaching one bug through a
# different call path counts as a separate find. One frame would merge them.
TOP_FRAMES = 2

# How many frames to KEEP in the stored `frames` column. Storing more than the
# signature consumes is what makes TOP_FRAMES revisable later: bumping it to 3 or
# 4 re-derives from stored rows instead of re-grading.
KEEP_FRAMES = 4

# Bumped whenever a rule below changes, so a half-rebuilt crash_signature table
# is detectable (rows carry the version that produced them).
SIG_VERSION = 1

# Signature text is display-only — the sha256 is the key — so it can be cut. C++
# templates demangle to hundreds of characters and would otherwise dominate.
SIG_TEXT_MAX = 500

_NO_FRAMES = "<no-frames>"


# --------------------------------------------------------------------------
# Fault class
# --------------------------------------------------------------------------

# The SUMMARY line, not the ERROR line. ASan's ERROR line embeds values that
# change every run:
#     ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000
#     ERROR: AddressSanitizer: requested allocation size 0xca59d61e008
# The second one is not even a class name — reading it yields "requested". The
# SUMMARY line carries the canonical token ("allocation-size-too-big") instead.
# The sanitizer's own name is dropped: stack-overflow is reported by both ASan
# and UBSan in this corpus, and which one saw it is a build detail, not a
# property of the defect.
_SUMMARY = re.compile(r"SUMMARY:\s+\w*Sanitizer:\s+([A-Za-z0-9_-]+)")
_LF_SUMMARY = re.compile(r"SUMMARY:\s+libFuzzer:\s+([a-z-]+)")
_LF_ERROR = re.compile(r"ERROR:\s+libFuzzer:\s+(out-of-memory|timeout|deadly signal)")

# Leaks are the exception to "the SUMMARY line is clean" — LSan puts a byte count
# there ("SUMMARY: AddressSanitizer: 1070 byte(s) leaked in 3 allocation(s)"), so
# checking _SUMMARY first would classify the three leak challenges as "1070",
# "200" and "76". This has to be tested BEFORE _SUMMARY.
_LSAN = re.compile(r"ERROR:\s+LeakSanitizer:\s+detected memory leaks")

# UBSan's SUMMARY is always the useless generic "undefined-behavior" — all three
# UBSan defects in the corpus collapse onto it. The specific kind only appears on
# the "runtime error:" line, which itself embeds addresses, offsets and type
# names, so match on the invariant phrasing and keep none of the values.
_UB_RUNTIME = [
    (re.compile(r"load of misaligned address"), "misaligned-access"),
    (re.compile(r"applying non-zero offset .* to null pointer"), "nullptr-arith"),
    (re.compile(r"outside the range of representable values"), "float-cast-overflow"),
    (re.compile(r"signed integer overflow"), "integer-overflow"),
    (re.compile(r"index \d+ out of bounds"), "oob-read"),
]

# Java: the exception CLASS only, never the message. graal-01's message contains
# the entire fuzz input, so a message in the signature would make every input its
# own "unique bug". `Caused by:` is matched too and the LAST hit wins: graal-01
# and graaljs-01 both surface as java.lang.RuntimeException at the top, with the
# real fault further down the chain.
_JAVA_EXC = re.compile(r'(?:Exception in thread "[^"]*"|Caused by:)\s+([\w.$]+\.[\w$]+)')

# Recursion blows the stack at an arbitrary point in the cycle, so the frame
# ORDER is not stable for these — see `_frame_keys`.
_STACK_EXHAUSTION = {"stack-overflow", "stack-exhaustion"}


def crash_class(text: str) -> str | None:
    """The fault class, or None when the output shows no fault marker at all."""
    m = _JAVA_EXC.findall(text)
    if m:
        return m[-1].lower()
    if _LSAN.search(text):
        return "memory-leak"
    m2 = _SUMMARY.search(text)
    if m2:
        token = m2.group(1).lower()
        if token != "undefined-behavior":
            return token
        for pattern, mapped in _UB_RUNTIME:
            if pattern.search(text):
                return mapped
        # Unmapped UBSan kind. Still a valid signature, but the caller should
        # treat it as a prompt to extend _UB_RUNTIME rather than as a result:
        # every unmapped kind shares this one token.
        return "undefined-behavior"
    for pattern in (_LF_SUMMARY, _LF_ERROR):
        m3 = pattern.search(text)
        if m3:
            return m3.group(1).lower()
    return None


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------

# "#1 0x51 in png_handle_iCCP /src/libpng/pngrutil.c:1447:5"
_FRAME = re.compile(
    r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+(?P<func>.+?)\s+(?P<file>[^\s:]+):(?P<line>\d+)")
# "#0 0x559 in vp9_rc_get_svc_params (/path/harness+0x3c363f)" — no source, but
# the function name is still the bug's identity.
_FRAME_NO_SRC = re.compile(
    r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+(?P<func>[^\s(]+)\s+\((?P<file>[^)]+)\)")
# "\tat org.json.JSONML.toJSONArray(JSONML.java:110)"
_JAVA_FRAME = re.compile(r"\bat\s+(?P<func>[\w.$]+)\((?P<file>[^:)]+)(?::(?P<line>\d+))?")

# System libraries. This one rule is what keeps the seven SIGABRT challenges
# apart: an assert failure buries the real site under five frames of libc
# (pthread_kill -> raise -> abort -> anonymous -> __assert_fail), and without
# this filter all seven produce the identical signature "abrt|pthread_kill|raise".
_SYS_LIB = re.compile(
    r"^/(lib|lib64|usr/lib|usr/lib64)/|libc\.so|libstdc\+\+\.so|libpthread\.so"
    r"|libgcc_s\.so|ld-linux")

# The C++ standard library, which sits between the interceptor and the code that
# actually owns the bug: flatbuffers-01 faults through char_traits::length and
# basic_string::append before reaching flexbuffers::Reference::ToString.
_STDLIB_HEADER = re.compile(r"/include/c\+\+/|/bits/", re.IGNORECASE)

# Sanitizer runtime, allocator interceptors, the libFuzzer driver, and the abort
# machinery again by name (it is statically linked into some targets, where the
# path test cannot see it).
_SKIP_FUNC = re.compile(
    r"^(__interceptor_|__asan|__ubsan|__lsan|__msan|__sanitizer"
    r"|operator new|operator delete|malloc|calloc|realloc|free"
    r"|LLVMFuzzer|fuzzer::|__libc_|__assert_fail|abort|raise|gsignal|pthread_kill)")
_SKIP_FILE = re.compile(r"compiler-rt|/sanitizer|libfuzzer", re.IGNORECASE)

# NOTE: do NOT filter on the oracle directory or on "/asan/". A statically linked
# target's own functions live inside the harness binary, so their frames read
# "vp9_rc_get_svc_params (<oracle>/binaries/vuln/asan/harness+0x3c363f)" — path
# filtering there drops the entire stack and libvpx-03/04 lose all frames. The
# driver is excluded by FUNCTION name (_SKIP_FUNC) instead.

# The Java harness wrapper. Without this every JVM challenge signs as
# "RegExpFuzzer.fuzzerTestOneInput | PocRunner.main".
_SKIP_JAVA = re.compile(r"Harness|PocRunner|Fuzzer\.|fuzzerTestOneInput|jazzer", re.IGNORECASE)


def _native_frames(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        m = _FRAME.search(line) or _FRAME_NO_SRC.search(line)
        if not m:
            continue
        func = m.group("func").strip()
        # Unsymbolized frames name a module plus an offset
        # ("<oracle>/binaries/vuln/asan/harness+0x3ad023"). The offset IS the code
        # location for these targets — the counterpart of a line number — so it
        # stays, same as line numbers do.
        file = m.group("file")
        if _SYS_LIB.search(file) or _STDLIB_HEADER.search(file) or _SKIP_FILE.search(file):
            continue
        if _SKIP_FUNC.search(func):
            continue
        line_no = m.groupdict().get("line")
        frame = {"func": func, "file": file, "line": int(line_no) if line_no else None}
        # Collapse consecutive repeats: a recursive cycle is hundreds of frames
        # of the same function and would otherwise fill TOP_FRAMES by itself.
        if not out or (out[-1]["func"], out[-1]["file"]) != (func, file):
            out.append(frame)
    return out


def _java_frames(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        m = _JAVA_FRAME.search(line)
        if not m:
            continue
        func, file = m.group("func"), m.group("file")
        if _SKIP_JAVA.search(func) or _SKIP_JAVA.search(file):
            continue
        line_no = m.groupdict().get("line")
        if not out or (out[-1]["func"], out[-1]["file"]) != (func, file):
            out.append({"func": func, "file": file, "line": int(line_no) if line_no else None})
    return out


def extract_frames(text: str) -> list[dict]:
    """Application frames, top first, capped at KEEP_FRAMES.

    Native frames win when both kinds are present: a JVM challenge that also
    prints a native trace crashed in native code.
    """
    frames = _native_frames(text) or _java_frames(text)
    return frames[:KEEP_FRAMES]


def _frame_keys(frames: list[dict], klass: str) -> list[tuple[str, str, int | None]]:
    """The (func, file, line) triples the signature is built from.

    The line number stays in. What is being counted here is CRASHES, not
    defects: two faults at different lines are two different crashes, and
    deciding they share an underlying bug is an inference this layer does not
    make. If that inference is ever wanted it belongs in a clustering pass on top
    (see _internal/CRASH-DEDUP.md), which can run off stored rows.

    Stack exhaustion is ordered differently. The stack is a repeated cycle and it
    blows at whichever frame happened to cross the guard page, so `a->b->c->a`
    and `b->c->a->b` are the same crash seen from different starting points.
    Sorting the distinct frames makes the signature invariant to that rotation.
    """
    keys = [(f["func"], f["file"], f["line"]) for f in frames]
    if klass in _STACK_EXHAUSTION:
        keys = sorted(set(keys))
    return keys[:TOP_FRAMES]


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------


@dataclass
class Signature:
    """One crash's identity plus the evidence it was derived from."""

    canon_sig: str                       # sha256 hex — the key
    sig_text: str                        # the same thing, readable, truncated
    klass: str                           # normalized fault class
    frames: list[dict] = field(default_factory=list)  # up to KEEP_FRAMES, with lines
    version: int = SIG_VERSION


def signature(harness_output: dict) -> Signature | None:
    """Signature for one harness run, or None if the output shows no fault.

    `harness_output` is grade-core's per-round payload: {stdout, stderr,
    exit_code, signal}. None here does NOT mean "no crash" — grade-core owns that
    verdict. It means this output carries no marker we can name a crash by, and
    the caller records the round as clean.
    """
    if not isinstance(harness_output, dict):
        return None
    text = (harness_output.get("stderr") or "") + "\n" + (harness_output.get("stdout") or "")
    klass = crash_class(text)
    if klass is None:
        return None

    frames = extract_frames(text)
    keys = _frame_keys(frames, klass)

    # Hash a canonical JSON array rather than a joined string. Separators can
    # occur inside the components themselves — "operator|" is a legal C++
    # function name — so "a|b" + "c" and "a" + "b|c" would otherwise collide.
    payload = json.dumps([klass] + [list(k) for k in keys],
                         ensure_ascii=False, separators=(",", ":"))
    canon = hashlib.sha256(payload.encode()).hexdigest()

    if keys:
        shown = " | ".join(
            f"{fn}@{fl.rsplit('/', 1)[-1]}" + (f":{ln}" if ln is not None else "")
            for fn, fl, ln in keys)
    else:
        shown = _NO_FRAMES
    text_repr = f"{klass} | {shown}"[:SIG_TEXT_MAX]

    return Signature(canon_sig=canon, sig_text=text_repr, klass=klass, frames=frames)


# --------------------------------------------------------------------------
# CLI — how the challenge image calls this
# --------------------------------------------------------------------------
# mcp-server (Go) pipes one run's {stdout, stderr} in as JSON and reads the
# signature back as JSON, or `null` when the output names no fault. Shelling out
# per crash is cheap next to running the harness itself, and it keeps a single
# copy of the rules rather than a Go translation of them that has to be kept in
# step.
if __name__ == "__main__":
    import sys

    _run = json.load(sys.stdin)
    _sig = signature(_run)
    if _sig is None:
        print("null")
    else:
        json.dump({"canon_sig": _sig.canon_sig, "sig_text": _sig.sig_text,
                   "klass": _sig.klass, "frames": _sig.frames,
                   "version": _sig.version}, sys.stdout)
        print()
