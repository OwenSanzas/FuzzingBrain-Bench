#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh harness <config>}"

# Ghidra ships a vendored libiberty copy under GPL/DemanglerGnu. We
# only need three TUs:
#   rust-demangle.c, safe-ctype.c, demangle.h
# (plus safe-ctype.h, ansidecl.h, libiberty.h pulled in by the above).
# Those are pre-fetched into /src/ghidra-demangler at image build time.

SRC=/src/ghidra-demangler

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        debug-asan)   CFH="-g -O0"; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        release-asan) CFH="-O1 -g -fno-omit-frame-pointer"; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang ${CFH} ${SAN} -fmacro-prefix-map=/src/= \
        -DHAVE_STRING_H -DHAVE_STDLIB_H \
        -I "${SRC}" \
        /src/harness/fuzz_rust_demangle.c \
        "${SRC}/rust-demangle.c" \
        "${SRC}/safe-ctype.c" \
        -lpthread -lm \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
