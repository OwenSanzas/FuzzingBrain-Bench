"""Turn one harness run's raw output into a stable crash signature.

A signature answers exactly one question: *are these two crashes the same bug?*
What is being identified is a CRASH, not a defect: two faults of different
kinds, or at different places, are different crashes, and whether they share an
underlying bug is not a question this layer answers. So everything that varies
between runs of the SAME fault (heap addresses, pids, allocation sizes, timings)
is stripped, while everything that locates the fault — class, function, file,
line — is kept.

    canon_sig = "class|func|func|func"

over the top three application frames. A frame is named by its function; an
unsymbolized frame keeps the module offset that stands in for one, with the
directory dropped — see `_norm_file` and `_key_part`.

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

import json
import re
from dataclasses import dataclass, field

# How many application frames identify a crash site. Three — the faulting
# function and the two frames that called it — tells two paths into the same
# defect apart, which is deliberate: reaching one bug through a different call
# path counts as a separate find. One frame would merge them.
TOP_FRAMES = 3

# How many frames to KEEP in the stored `frames` column. Storing more than the
# signature consumes is what makes TOP_FRAMES revisable later: bumping it to 3 or
# 4 re-derives from stored rows instead of re-grading.
KEEP_FRAMES = 4

# Bumped whenever a rule below changes, so a half-rebuilt crash_signature table
# is detectable (rows carry the version that produced them).
#   2: frame paths are reduced to their basename before hashing (_norm_file).
#   3: function names drop parameter lists, template arguments and Rust
#      instantiation hashes (_norm_func); a cyclic trace is ordered like stack
#      exhaustion whatever its class (_frame_keys).
#   4: the identity is the READABLE joined form again — "class|f1|f2|f3" over
#      three function names — instead of a sha256 over (func, file, line)
#      triples. Signatures from 3 and 4 are not comparable: the key changed
#      shape, not just content.
SIG_VERSION = 4

# Signature text is display-only, so it can be cut. C++ templates demangle to
# hundreds of characters and would otherwise dominate. The KEY is never cut —
# truncating an identity is how two different crashes become one.
SIG_TEXT_MAX = 500

_NO_FRAMES = "<no-frames>"

# The identity separator, and the escape that makes it unambiguous.
#
# A joined string is only a valid key if the separator cannot occur inside a
# component, and "|" can: `operator|` is a legal C++ function name, and
# demangled operator overloads reach these traces routinely. Unescaped,
# "a|b" + "c" and "a" + "b|c" are the same string, so two distinct crashes
# would share one identity and the run would be credited with one find instead
# of two. Escaping the backslash first keeps the mapping reversible.
SIG_SEP = "|"


def _escape(part: str) -> str:
    return part.replace("\\", "\\\\").replace(SIG_SEP, "\\" + SIG_SEP)


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


def _all_frames(text: str) -> list[dict]:
    """Every application frame, top first, uncapped.

    Native frames win when both kinds are present: a JVM challenge that also
    prints a native trace crashed in native code.
    """
    return _native_frames(text) or _java_frames(text)


def extract_frames(text: str) -> list[dict]:
    """Application frames, top first, capped at KEEP_FRAMES."""
    return _all_frames(text)[:KEEP_FRAMES]


# What a blown stack looks like: very deep, and built from very few distinct
# functions. Both halves are load-bearing, and each one alone is wrong:
#
#   depth alone — an ordinary crash can sit far down a call chain.
#   repetition alone — mongoose-02 faults four frames down with
#     mg_mqtt_next_prop appearing twice, because the compiler inlined it into
#     itself. Treating that as a cycle sorted its frames and merged seven
#     distinct faults (lines 4121…4155) into one crash.
#
# openscreen-01, for contrast, is 66 frames drawn from 3 functions.
RECURSION_MIN_DEPTH = 20
RECURSION_MAX_DISTINCT = 0.5


def _is_cyclic(frames: list[dict]) -> bool:
    """Whether this stack is a repeating cycle, so its top frame is arbitrary."""
    if len(frames) < RECURSION_MIN_DEPTH:
        return False
    distinct = {(f["func"], f["file"]) for f in frames}
    return len(distinct) <= len(frames) * RECURSION_MAX_DISTINCT


# C++ demanglers disagree about one space. LLVM 14 renders nested template
# closers as `> >`, newer LLVM as `>>`, so the SAME function in the SAME binary
# reads differently depending on which llvm-symbolizer resolved it:
#
#   ...basic_string<char, std::char_traits<char>, std::allocator<char> >&...
#   ...basic_string<char, std::char_traits<char>, std::allocator<char>>&...
#
# The self-contained image ships llvm-14 (matching the clang the harnesses are
# built with) while the grading backend has a newer toolchain, so three C++
# challenges signed differently on the two sides for no reason to do with the
# fault. Collapsing the space makes the two agree, and is idempotent for
# whichever side already emits the compact form.
_TEMPLATE_GAP = re.compile(r">\s+>")

# Rust's legacy mangling ends every path with the symbol hash: `::h` + 16 hex. It
# encodes the crate version and the generic instantiation, so the same function
# in a rebuilt crate carries a different one.
_RUST_HASH = re.compile(r"::h[0-9a-f]{16}$")

# Rust escapes punctuation it cannot put in a symbol. Decoded, these read as
# ordinary Rust; left alone they are the least legible part of any Rust frame.
_RUST_ESCAPES = {
    "$LT$": "<", "$GT$": ">", "$u20$": " ", "$u5b$": "[", "$u5d$": "]",
    "$u7b$": "{", "$u7d$": "}", "$C$": ",", "$RF$": "&", "$BP$": "*",
    "$LP$": "(", "$RP$": ")",
}

# Names where a bracket belongs to the FUNCTION rather than to its signature, and
# so must survive the stripping below. Longest first: `operator<=>` has to win
# over `operator<=`, which has to win over `operator<`.
_BRACKET_IS_THE_NAME = [
    "(anonymous namespace)",
    "operator<=>", "operator<<=", "operator>>=", "operator<=", "operator>=",
    "operator<<", "operator>>", "operator()", "operator[]", "operator->*",
    "operator->", "operator new[]", "operator delete[]",
    "operator<", "operator>",
]

# Trailing qualifiers, left stranded once the parameter list they followed is
# gone: `Foo::bar(int) const` -> `Foo::bar const`.
_TRAILING_QUAL = re.compile(r"\s*\b(const|volatile|noexcept)\b\s*$")
_TRAILING_REF = re.compile(r"\s*&&?\s*$")


def _strip_balanced(name: str, opener: str, closer: str) -> str:
    """Drop every balanced `opener…closer` group.

    Unbalanced input is returned untouched. Frames are truncated by output caps
    often enough that guessing where a cut-off argument list ended would corrupt
    more names than it cleaned.
    """
    out, depth = [], 0
    for ch in name:
        if ch == opener:
            depth += 1
        elif ch == closer:
            if depth == 0:
                return name
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out) if depth == 0 else name


def _norm_func(func: str) -> str:
    """A frame's function name, reduced to what identifies the function.

    A demangler prints everything the linker needed to keep symbols apart —
    parameter types, template arguments, Rust's instantiation hash. None of it
    says where the crash was, and all of it varies with things the fault does
    not depend on:

        WelsSampleSad8x8_c(unsigned char*, int, unsigned char*, int)
          -> WelsSampleSad8x8_c
        harfbuzz_rust::font::_hb_fontations_glyph_name::h4948ba84dce9f35a
          -> harfbuzz_rust::font::_hb_fontations_glyph_name

    That these renderings are not properties of the crash is not a guess: the
    `> >` case below is one binary's own frame signing two ways depending on
    which symbolizer read it. Parameter lists and instantiation hashes are the
    same kind of noise with more characters.

    The namespace and class stay. `Packer::doPack` and `PackPs1::pack` are
    different functions and must not collapse onto `pack`.
    """
    prev = None
    while prev != func:                       # `> > >` needs more than one pass
        prev = func
        func = _TEMPLATE_GAP.sub(">>", func)

    func = _RUST_HASH.sub("", func)

    # Hide the brackets that ARE the name, strip the ones that merely describe
    # it, then put them back.
    held: list[str] = []
    for token in _BRACKET_IS_THE_NAME:
        while token in func:
            func = func.replace(token, f"\x00{len(held)}\x00", 1)
            held.append(token)
    func = _strip_balanced(func, "(", ")")    # parameter list
    func = _strip_balanced(func, "<", ">")    # template arguments
    for i, token in enumerate(held):
        func = func.replace(f"\x00{i}\x00", token)

    func = _TRAILING_REF.sub("", _TRAILING_QUAL.sub("", func))

    # After the angle brackets are gone, so nothing decoded here can be mistaken
    # for a template argument and stripped: `_$LT$impl$u20$$u5b$T$u5d$$GT$` names
    # the impl block a method belongs to and has to survive.
    for escape, ch in _RUST_ESCAPES.items():
        func = func.replace(escape, ch)

    return func.strip() or _NO_FRAMES


def _norm_file(path: str) -> str:
    """The part of a frame's file that belongs to the crash: its basename.

    A path says where the harness was graded, which is not a property of the
    fault. The grading backend unpacks each run into a fresh temp dir, and a
    self-contained image runs the harness from its own baked-in prefix, so ONE
    fault signs three different ways:

        /tmp/fbgrade-f4p96gu7/oracle/binaries/vuln/asan/harness+0xacb92
        /tmp/fbgrade-q2wk1x8p/oracle/binaries/vuln/asan/harness+0xacb92
        /opt/fbbench/oracle-root/cups-01/binaries/release-asan/harness+0xacb92

    Keeping the directory made crash identity a property of the grading run
    rather than of the crash. It went unnoticed because `sig_text` already cut
    the path down to its basename while the hash did not, so two rows read
    identically and still counted twice — and the hash is what dedups. Measured
    on a live pool: 41 rows carried such a path, across 41 distinct temp dirs.
    Not one of them had ever deduplicated against another.

    The basename keeps everything that locates a fault — the source file, or the
    module and the offset that stands in for a line number when a frame is
    unsymbolized — and drops only the part that says where the grader put it.
    Two source files sharing a basename would have to also share a function name
    and a line number to collide, which the func and line in the key rule out.

    `Signature.frames` keeps the paths unreduced, so this stays revisable from
    stored rows: it is the same bargain KEEP_FRAMES makes for TOP_FRAMES.
    """
    return path.rsplit("/", 1)[-1]


# A frame whose "file" is a module plus an offset rather than a source path.
_MODULE_OFFSET = re.compile(r"\+0x[0-9a-fA-F]+$")


def _key_part(frame: dict) -> str:
    """The one string that identifies a frame in the joined signature.

    A function name, normally — that is the readable form, and the form that
    survives the rebuilds this benchmark does constantly.

    An UNSYMBOLIZED frame is the exception, and it has to be. It reads
    `main (/path/harness+0xacb92)`: the name is whatever public symbol the
    linker left behind, so every fault in the binary shares it, and the offset
    is the only thing saying WHERE. Reducing such a frame to its name alone
    merges every crash in the module into one signature — the exact
    class-counting collapse KEEP_FRAMES exists to prevent, arriving through the
    other door. The offset stays; _norm_file has already dropped the directory,
    so the same fault still signs identically whether it was graded in a temp
    dir or inside an image.
    """
    fn = _norm_func(frame["func"])
    fl = _norm_file(frame["file"])
    return f"{fn}@{fl}" if _MODULE_OFFSET.search(fl) else fn


def _frame_keys(frames: list[dict], klass: str,
                cyclic: bool = False) -> list[tuple[str, str, int | None]]:
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

    Which traces those are is decided by the shape of the stack (`cyclic`, from
    _is_cyclic) as well as by the class name. ASan reports a recursion blowup as
    whatever signal actually killed the process, so openscreen-01 — 66 frames of
    readValue/readArray alternating — arrives classed `abrt` and the name test
    never fires. It signed three ways from one input shape, depending only on
    where in the cycle the stack ran out. The class test stays for the traces
    that are named honestly.
    """
    # Function names only. The identity is the readable joined form, and a name
    # is what stays stable across the rebuilds this benchmark does constantly:
    # bumping a toolchain moves every line number in a file without moving a
    # single defect. harfbuzz-01 demonstrated exactly that — the same crash,
    # from the same PoC, signed differently under rustc 1.88 and 1.97 because
    # `slice/mod.rs` had shifted from line 3746 to 4325.
    #
    # The cost is real and is the trade this format makes: two faults at
    # different lines OF THE SAME FUNCTION now share an identity and count once.
    # `Signature.frames` keeps file and line unreduced, so a finer key can be
    # re-derived from stored rows without re-grading.
    keys = [(_key_part(f),) for f in frames]
    # Collapse repeats the name-only key creates. Dropping file and line merges
    # frames that used to differ — a recursive helper called from two lines of
    # itself — and without this they fill TOP_FRAMES with one name.
    keys = list(dict.fromkeys(keys))
    if cyclic:
        # Identify the cycle by the frames that REPEAT. Sorting the top-of-stack
        # window is not enough on its own: which frames are in the window is
        # itself decided by where the stack ran out, so openscreen-01 still
        # signed three ways with the window sorted. What does not move is the set
        # of frames the recursion goes round — and taking only the repeating ones
        # leaves out the entry path below the cycle, which would otherwise win a
        # sort on nothing but its name.
        seen: dict = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        repeated = sorted(k for k, n in seen.items() if n > 1)
        return (repeated or sorted(seen))[:TOP_FRAMES]
    if klass in _STACK_EXHAUSTION:
        keys = sorted(set(keys))
    return keys[:TOP_FRAMES]


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------


@dataclass
class Signature:
    """One crash's identity plus the evidence it was derived from."""

    canon_sig: str                       # the joined identity — the key
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

    all_frames = _all_frames(text)
    frames = all_frames[:KEEP_FRAMES]
    # A cycle is identified from the WHOLE stack, not from the stored window:
    # the window holds whichever turn of the cycle was on top when the stack
    # ran out, which is the thing that has to stop mattering.
    cyclic = _is_cyclic(all_frames)
    keys = _frame_keys(all_frames if cyclic else frames, klass, cyclic=cyclic)

    # The identity IS the joined string — "class|f1|f2|f3" — not a digest over
    # it. A reader can see which crash a run found without a lookup, which is
    # the whole point of the format; the escaping above is what makes joining
    # safe enough to key on.
    parts = [klass] + [fn for (fn,) in keys] if keys else [klass, _NO_FRAMES]
    canon = SIG_SEP.join(_escape(p) for p in parts)

    # Rendered from the SAME parts that form the key, so the two can never
    # disagree — a duplicate that reads as identical to a human must also count
    # as identical. Only spacing and length differ, and only the display is cut.
    text_repr = " | ".join(parts)[:SIG_TEXT_MAX]

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
