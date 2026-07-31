"""Checks for signature.py — the rules that decide which crash a run produced.

signature.py is a COPY of the grading backend's rules, and the backend has the
authoritative tests that pin them to all 68 crash logs in the answers repo. This
file exists for the other risk: the copy silently drifting from the original, in
a repo where nothing else would notice. So it asserts the properties the score
depends on rather than re-deriving the corpus.

    two faults of different kinds at one site are two crashes
    one fault reached two ways is two crashes
    frames that belong to the sanitizer, the allocator or the driver are not
        the bug and never identify it
    a clean run has no signature at all

Both the top-level `canon_sig` (the key everything is counted by) and the
readable `sig_text` are checked: the hash is what dedups, and the text is what a
human reads when a number looks wrong.

  python -m fbbench.grading.test_signature
"""
from __future__ import annotations

import sys

from fbbench.grading.signature import _norm_func, signature

# A real libFuzzer OOM trace (ghidra rust-demangler), trimmed — the allocator
# interceptor (#0) must be skipped and the top app frames kept.
_OOM = {
    "exit_code": 71, "signal": "",
    "stderr": """==631494== ERROR: libFuzzer: out-of-memory (used: 257Mb; limit: 256Mb)
SUMMARY: libFuzzer: out-of-memory
    #0 0x55 in __interceptor_realloc (/oracle/binaries/vuln/asan/harness+0xe6026)
    #1 0x55 in str_buf_reserve /src/ghidra-demangler/rust-demangle.c:1553:21
    #2 0x55 in str_buf_append /src/ghidra-demangler/rust-demangle.c:1572:3
    #3 0x55 in str_buf_demangle_callback /src/ghidra-demangler/rust-demangle.c:1583:3
    #4 0x55 in print_str /src/ghidra-demangler/rust-demangle.c:283:5""",
    "stdout": "",
}
_BOF = {
    "exit_code": 1, "signal": "ABRT",
    "stderr": """==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x60d
    #0 0x49 in __interceptor_memcpy compiler-rt/asan/asan_interceptors.cpp:8
    #1 0x51 in png_handle_iCCP /src/libpng/pngrutil.c:1447:5
    #2 0x52 in png_read_info /src/libpng/pngread.c:123:7
    #3 0x53 in LLVMFuzzerTestOneInput /src/harness.c:20:3
SUMMARY: AddressSanitizer: heap-buffer-overflow /src/libpng/pngrutil.c:1447:5""",
    "stdout": "",
}
# Same site as _BOF, different fault type -> a different crash.
_UAF = {
    "exit_code": 1, "signal": "",
    "stderr": """==9==ERROR: AddressSanitizer: heap-use-after-free on address 0x60
    #0 0x49 in __asan_memcpy asan.cpp:1
    #1 0x51 in png_handle_iCCP /src/libpng/pngrutil.c:1447:5
    #2 0x52 in png_read_info /src/libpng/pngread.c:123:7
SUMMARY: AddressSanitizer: heap-use-after-free /src/libpng/pngrutil.c:1447:5""",
    "stdout": "",
}
# Same fault type and same faulting function as _BOF, reached from a different
# caller -> a different crash, because the second frame is part of the identity.
_BOF_OTHER_PATH = {
    "exit_code": 1, "signal": "ABRT",
    "stderr": """==13==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x60d
    #0 0x49 in __interceptor_memcpy compiler-rt/asan/asan_interceptors.cpp:8
    #1 0x51 in png_handle_iCCP /src/libpng/pngrutil.c:1447:5
    #2 0x52 in png_read_end /src/libpng/pngread.c:900:7
SUMMARY: AddressSanitizer: heap-buffer-overflow /src/libpng/pngrutil.c:1447:5""",
    "stdout": "",
}
_CLEAN = {"exit_code": 0, "signal": "",
          "stderr": "INFO: Seed 1\nExecuted candidate in 0 ms\n", "stdout": ""}
_FLAKE = {"exit_code": -6, "signal": "ABRT", "stderr": "", "stdout": ""}


def _unsym(harness_path: str, offset: str = "0xacb92") -> dict:
    """One fault with an unsymbolized top-level frame, as seen from `harness_path`.

    Where the grader unpacked the oracle is not part of the crash. The backend
    uses a fresh temp dir per run and a self-contained image uses its own baked
    prefix, so the same fault arrives under a different path every time.
    """
    return {
        "exit_code": 1, "signal": "ABRT",
        "stderr": f"""==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x60d
    #0 0x49 in __interceptor_memcpy compiler-rt/asan/asan_interceptors.cpp:8
    #1 0x51 in cupsUTF8ToCharset /src/cups/cups/transcode.c:245:5
    #2 0x52 in main ({harness_path}+{offset})
SUMMARY: AddressSanitizer: heap-buffer-overflow /src/cups/cups/transcode.c:245:5""",
        "stdout": "",
    }


# The same binary, the same offsets, two grading runs.
_GRADED_REMOTE = _unsym("/tmp/fbgrade-f4p96gu7/oracle/binaries/vuln/asan/harness")
_GRADED_REMOTE_AGAIN = _unsym("/tmp/fbgrade-q2wk1x8p/oracle/binaries/vuln/asan/harness")
_GRADED_IN_IMAGE = _unsym("/opt/fbbench/oracle-root/cups-01/binaries/release-asan/harness")
# A different offset IS a different code location — the counterpart of a line
# number for an unsymbolized frame — and must stay a different crash.
_GRADED_OTHER_SITE = _unsym("/tmp/fbgrade-f4p96gu7/oracle/binaries/vuln/asan/harness",
                            offset="0x154627")


# Each function recurses from its own call site, so its line is a property of the
# function and not of where in the cycle it happens to appear. (These are the
# real lines from openscreen's jsoncpp.)
_LINE_OF = {
    "Json::OurReader::readValue": 1075,
    "Json::OurReader::readArray": 1529,
    "Json::OurReader::readObject": 1601,
}


def _recursion(cycle: list[str]) -> dict:
    """A stack blown by `cycle`, repeated — and reported as ABRT, which is how
    ASan classes a recursion blowup that libc aborted on."""
    trace = "\n".join(
        f"    #{i} 0x5{i} in {cycle[i % len(cycle)]} "
        f"/src/jsoncpp/json_reader.cpp:{_LINE_OF[cycle[i % len(cycle)]]}:9"
        for i in range(40))
    return {"exit_code": 66, "signal": "",
            "stderr": "==1==ERROR: AddressSanitizer: ABRT on unknown address\n"
                      + trace + "\nSUMMARY: AddressSanitizer: ABRT (libc.so.6+0x9eb2c)",
            "stdout": ""}


# One input shape, two runs whose stacks ran out at different points in the same
# cycle. Nothing about the fault differs — only where the guard page fell.
_CYCLE_FROM_ARRAY = _recursion(["Json::OurReader::readArray", "Json::OurReader::readValue"])
_CYCLE_FROM_VALUE = _recursion(["Json::OurReader::readValue", "Json::OurReader::readArray"])
# A different cycle through the same reader IS a different crash: `{{{{` does not
# recurse the way `[[[[` does.
_CYCLE_OBJECT = _recursion(["Json::OurReader::readObject", "Json::OurReader::readValue"])


def _text(run: dict) -> str | None:
    sig = signature(run)
    return sig.sig_text if sig else None


def main() -> int:
    checks: list[tuple[str, object, object]] = [
        # The allocator interceptor at #0 is not the bug; the first application
        # frame is. Only TOP_FRAMES of them identify the crash.
        ("oom skips the allocator frame", _text(_OOM),
         "out-of-memory | str_buf_reserve@rust-demangle.c:1553 | "
         "str_buf_append@rust-demangle.c:1572"),
        # __interceptor_memcpy (sanitizer) and LLVMFuzzerTestOneInput (driver)
        # are both dropped.
        ("bof skips interceptor and driver", _text(_BOF),
         "heap-buffer-overflow | png_handle_iCCP@pngrutil.c:1447 | "
         "png_read_info@pngread.c:123"),
        ("uaf keeps its own type", _text(_UAF),
         "heap-use-after-free | png_handle_iCCP@pngrutil.c:1447 | "
         "png_read_info@pngread.c:123"),
        ("a clean run has no signature", signature(_CLEAN), None),
        # A terminating signal with no output at all is a host flake, not a
        # finding. Nothing names it, so nothing counts it.
        ("an output-less signal has no signature", signature(_FLAKE), None),
    ]
    ok = True
    for name, got, want in checks:
        good = got == want
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got!r}")
        if not good:
            print(f"         expected: {want!r}")

    # The two distinctness properties the score rests on. Compared on canon_sig,
    # not on the text: the hash is what the crash pool actually keys by, and a
    # bug that made the two agree in text but differ in hash (or the reverse)
    # would pass a text-only check while miscounting every sweep.
    for name, a, b in (
        ("same site, different type stays distinct", _BOF, _UAF),
        ("same fault, different caller stays distinct", _BOF, _BOF_OTHER_PATH),
        ("different offset in one module stays distinct", _GRADED_REMOTE,
         _GRADED_OTHER_SITE),
    ):
        distinct = signature(a).canon_sig != signature(b).canon_sig
        ok = ok and distinct
        print(f"  [{'PASS' if distinct else 'FAIL'}] {name}")

    # The counterpart property, and the one that actually bit: a crash must sign
    # the same way wherever it was graded. Comparing a signature to itself only
    # proves the function is deterministic, which no plausible bug breaks — what
    # broke was one fault arriving under a different path per grading run and
    # counting again every time.
    # A demangler's bookkeeping is not part of a crash. Checked on _norm_func
    # directly: these are the shapes the corpus actually produces, and asserting
    # them here is what keeps a later rule from quietly eating a name it should
    # have kept.
    for name, got, want in [
        ("parameter list dropped",
         _norm_func("WelsSampleSad8x8_c(unsigned char*, int, unsigned char*, int)"),
         "WelsSampleSad8x8_c"),
        ("template arguments dropped",
         _norm_func("WelsVP::CSceneChangeDetection<WelsVP::CSceneChangeDetectorVideo>"
                    "::Process(int, SPixMap*, SPixMap*)"),
         "WelsVP::CSceneChangeDetection::Process"),
        ("rust instantiation hash dropped",
         _norm_func("harfbuzz_rust::font::_hb_fontations_glyph_name::h4948ba84dce9f35a"),
         "harfbuzz_rust::font::_hb_fontations_glyph_name"),
        ("rust escapes decoded, impl block kept",
         _norm_func("core::slice::_$LT$impl$u20$$u5b$T$u5d$$GT$::copy_from_slice"
                    "::hc84a7ee1d4de3ee8"),
         "core::slice::_<impl [T]>::copy_from_slice"),
        # The brackets in these are the function's own name, not a signature.
        ("operator() survives",
         _norm_func("WelsVP::CSceneChangeDetectorVideo::operator()(WelsVP::SLocalParam&)"),
         "WelsVP::CSceneChangeDetectorVideo::operator()"),
        ("operator< survives", _norm_func("Cmp::operator<(Cmp const&) const"),
         "Cmp::operator<"),
        ("anonymous namespace survives", _norm_func("(anonymous namespace)::DoThing(int)"),
         "(anonymous namespace)::DoThing"),
        # The class qualifies the function; dropping it would merge real bugs.
        ("class qualification kept", _norm_func("PackPs1::pack(OutputFile*)"),
         "PackPs1::pack"),
        # A truncated name is left whole rather than cut at a guess.
        ("unbalanced name left alone", _norm_func("Foo::bar(int, std::vector<int"),
         "Foo::bar(int, std::vector<int"),
        ("a plain C name is untouched", _norm_func("mg_match"), "mg_match"),
    ]:
        good = got == want
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got!r}")
        if not good:
            print(f"         expected: {want!r}")

    # Two different cycles through one parser are two crashes; the same cycle
    # entered at a different point is one.
    diff_cycle = signature(_CYCLE_FROM_ARRAY).canon_sig != signature(_CYCLE_OBJECT).canon_sig
    ok = ok and diff_cycle
    print(f"  [{'PASS' if diff_cycle else 'FAIL'}] a different cycle stays distinct")

    for name, a, b in (
        ("one crash, two grading runs", _GRADED_REMOTE, _GRADED_REMOTE_AGAIN),
        ("one crash, graded remotely and in-image", _GRADED_REMOTE, _GRADED_IN_IMAGE),
        # ABRT, not stack-overflow: caught by the frames, not by the class name.
        ("one cycle, two blow-up points", _CYCLE_FROM_ARRAY, _CYCLE_FROM_VALUE),
    ):
        same = signature(a).canon_sig == signature(b).canon_sig
        ok = ok and same
        print(f"  [{'PASS' if same else 'FAIL'}] {name}")

    # sig_text is what a human reads when a count looks wrong, so it has to be a
    # rendering of the hashed keys and not a second, looser reduction of them.
    # It was the two disagreeing that hid the bug above for as long as it hid.
    agree = _text(_GRADED_REMOTE) == _text(_GRADED_IN_IMAGE)
    ok = ok and agree
    print(f"  [{'PASS' if agree else 'FAIL'}] the text says what the hash counts")

    print("signature:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
