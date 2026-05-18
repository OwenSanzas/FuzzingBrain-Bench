#!/bin/bash
# Build script invoked from the Dockerfile.
# Produces four harness binaries under /out/<config>/harness.
#
# Usage: build.sh <config>
#   config = debug | debug-asan | release-asan | coverage

set -euo pipefail

CONFIG="${1:?usage: build.sh <config>}"
SRC=/src
OUT=/out/${CONFIG}

mkdir -p "${OUT}"

# Per-config flags.
case "${CONFIG}" in
    debug)
        CFLAGS="-g -O0"
        SAN="-fsanitize=fuzzer"
        ;;
    debug-asan)
        CFLAGS="-g -O0"
        SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
        ;;
    release-asan)
        CFLAGS="-O2 -g"
        SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
        ;;
    coverage)
        CFLAGS="-g -O0 -fprofile-instr-generate -fcoverage-mapping"
        SAN="-fsanitize=fuzzer"
        ;;
    *)
        echo "unknown config: ${CONFIG}" >&2
        exit 2
        ;;
esac

# libfdt is a small C library; statically compile it together with the
# harness so that every config gets consistent instrumentation.
LIBFDT_SOURCES=$(ls "${SRC}"/libfdt/fdt*.c | sort -u)

# Normalize paths in debug info AND in __FILE__ macros (which UBSan
# uses for its runtime-error location strings). Both maps are needed
# to strip /src/ from sanitizer output.
clang \
    ${CFLAGS} \
    ${SAN} \
    -fdebug-prefix-map="${SRC}/=" \
    -fmacro-prefix-map="${SRC}/=" \
    -I "${SRC}/libfdt" \
    "${SRC}/harness/fuzz_fdt.c" \
    ${LIBFDT_SOURCES} \
    -o "${OUT}/harness"

echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
