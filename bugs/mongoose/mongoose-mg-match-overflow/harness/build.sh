#!/bin/bash
# Build script for mongoose-mg-match-overflow harness.
# Produces one harness binary per config under /out/<config>/harness.

set -euo pipefail

CONFIG="${1:?usage: build.sh <config>}"
SRC=/src
OUT=/out/${CONFIG}

mkdir -p "${OUT}"

case "${CONFIG}" in
    debug)        CFLAGS="-g -O0"; SAN="-fsanitize=fuzzer" ;;
    debug-asan)   CFLAGS="-g -O0"; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
    release-asan) CFLAGS="-O2 -g"; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
    coverage)     CFLAGS="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; SAN="-fsanitize=fuzzer" ;;
    *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
esac

# fuzzer.c does `#include "mongoose.c"` which pulls in the whole library
# (mongoose ships as a single amalgamated source file). -I "${SRC}" lets
# the include path resolve.
clang \
    ${CFLAGS} \
    ${SAN} \
    -fmacro-prefix-map="${SRC}/=" \
    -I "${SRC}" \
    "${SRC}/harness/fuzzer.c" \
    "${SRC}/harness/stubs.c" \
    -o "${OUT}/harness"

echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
