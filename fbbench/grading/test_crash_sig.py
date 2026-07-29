"""Self-contained checks for crash_sig.crash_signature — the unique-crash scorer.

Asserts the signature is stable, dedups recursion/allocator frames, separates
crashes by type and by location, and returns None for a clean run.

  python -m fbbench.grading.test_crash_sig
"""
from __future__ import annotations

import sys

from fbbench.grading.crash_sig import crash_signature

# A real libFuzzer OOM trace (ghidra rust-demangler), trimmed — the allocator
# interceptor (#0) must be skipped and the top app frames kept.
_OOM = {
    "exit_code": 71, "signal": "",
    "stderr": """==631494== ERROR: libFuzzer: out-of-memory (used: 257Mb; limit: 256Mb)
    #0 0x55 in __interceptor_realloc (/oracle/binaries/release-asan/harness+0xe6026)
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
    #3 0x53 in LLVMFuzzerTestOneInput /src/harness.c:20:3""",
    "stdout": "",
}
# Same location as _BOF but a different fault type -> must be a distinct crash.
_UAF = {
    "exit_code": 1, "signal": "",
    "stderr": """==9==ERROR: AddressSanitizer: heap-use-after-free on address 0x60
    #0 0x49 in __asan_memcpy asan.cpp:1
    #1 0x51 in png_handle_iCCP /src/libpng/pngrutil.c:1447:5""",
    "stdout": "",
}
_CLEAN = {"exit_code": 0, "signal": "",
          "stderr": "INFO: Seed 1\nExecuted candidate in 0 ms\n", "stdout": ""}
_FLAKE = {"exit_code": -6, "signal": "ABRT", "stderr": "", "stdout": ""}  # signal, no output


def main() -> int:
    checks = [
        ("oom top frame", crash_signature(_OOM),
         "out-of-memory|str_buf_reserve|str_buf_append|str_buf_demangle_callback"),
        ("bof skips interceptor+fuzzer", crash_signature(_BOF),
         "heap-buffer-overflow|png_handle_iCCP|png_read_info"),
        ("uaf distinct type", crash_signature(_UAF),
         "heap-use-after-free|png_handle_iCCP"),
        ("clean is None", crash_signature(_CLEAN), None),
        ("bare-signal flake is None", crash_signature(_FLAKE), None),
    ]
    ok = True
    for name, got, want in checks:
        good = got == want
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got!r}")
        if not good:
            print(f"         expected: {want!r}")
    # Distinctness: BOF and UAF at the same site must NOT collapse.
    distinct = crash_signature(_BOF) != crash_signature(_UAF)
    ok = ok and distinct
    print(f"  [{'PASS' if distinct else 'FAIL'}] same-site different-type stays distinct")
    print("crash_sig:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
