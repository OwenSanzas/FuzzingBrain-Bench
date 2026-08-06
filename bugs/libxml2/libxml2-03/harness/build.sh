#!/bin/bash
#
# Build recipe for the libxml2 'regexp' libFuzzer target.
#
# This mirrors the autotools flow that fuzz/oss-fuzz-build.sh (shipped in the
# libxml2 tree itself) drives, and the compiler/sanitizer flags that
# OSS-Fuzz's base-clang / base-builder images export for a libFuzzer+ASan (or
# coverage) build:
#   - autogen.sh + make builds the static library exactly as upstream's own
#     OSS-Fuzz integration script does.
#   - fuzz/regexp.c + fuzz/fuzz.c (copied verbatim into harness/) are compiled
#     and linked against that static library, the same way
#     fuzz/oss-fuzz-build.sh compiles/links each `$fuzzer.o` + fuzz.o against
#     ../.libs/libxml2.a.
# Only the 'regexp' target is built (oss-fuzz-build.sh normally loops over all
# eleven fuzzers; that loop, and its seed-corpus zipping, are not needed here).
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"
JOBS=$(nproc)

SRC_ROOT=/src/libxml2
HARNESS_DIR=/src/harness

export CC=clang
export CXX=clang++

# Matches base-clang's ENV CFLAGS (minus -O<n>, which varies per config).
BASE_CFLAGS="-fno-omit-frame-pointer -gline-tables-only \
-Wno-error=incompatible-function-pointer-types -Wno-error=int-conversion \
-Wno-error=deprecated-declarations -Wno-error=implicit-function-declaration \
-Wno-error=implicit-int -Wno-error=unknown-warning-option \
-Wno-error=vla-cxx-extension -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION"

# Matches base-builder's SANITIZER_FLAGS_address.
ASAN_FLAGS="-fsanitize=address -fsanitize-address-use-after-scope"
# Matches base-builder's default COVERAGE_FLAGS (always compiled in for
# libFuzzer builds, even under ASan -- gives libFuzzer its coverage counters).
FUZZER_NO_LINK="-fsanitize=fuzzer-no-link"
# Matches base-builder's COVERAGE_FLAGS_coverage.
COV_FLAGS="-fprofile-instr-generate -fcoverage-mapping"

if [ "${cmd}" = "build-libs" ]; then
    rm -rf "${SRC_ROOT}-asan" "${SRC_ROOT}-cov"
    cp -r "${SRC_ROOT}" "${SRC_ROOT}-asan"
    cp -r "${SRC_ROOT}" "${SRC_ROOT}-cov"

    pushd "${SRC_ROOT}-asan" >/dev/null
    CFLAGS="-O1 ${BASE_CFLAGS} ${ASAN_FLAGS} ${FUZZER_NO_LINK}" \
        CXXFLAGS="-O1 ${BASE_CFLAGS} ${ASAN_FLAGS} ${FUZZER_NO_LINK}" \
        LDFLAGS="-fsanitize=address" \
        ./autogen.sh --disable-shared --without-debug --without-http \
            --without-python --with-zlib
    make -j"${JOBS}"
    popd >/dev/null

    pushd "${SRC_ROOT}-cov" >/dev/null
    CFLAGS="-O0 ${BASE_CFLAGS} ${COV_FLAGS}" \
        CXXFLAGS="-O0 ${BASE_CFLAGS} ${COV_FLAGS}" \
        LDFLAGS="${COV_FLAGS}" \
        ./autogen.sh --disable-shared --without-debug --without-http \
            --without-python --with-zlib
    make -j"${JOBS}"
    popd >/dev/null

    echo "libxml2 built (asan + coverage)"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    case "${CONFIG}" in
        release-asan) OUT=/out/vuln/asan ;;
        coverage)     OUT=/out/vuln/cov ;;
        *)            OUT=/out/vuln/${CONFIG} ;;
    esac
    mkdir -p "${OUT}"

    case "${CONFIG}" in
        debug|debug-asan)
            CFH="-g -O0"
            BUILD="${SRC_ROOT}-asan"
            SAN="-fsanitize=fuzzer,address"
            ;;
        release-asan)
            CFH="-O1 -g -fno-omit-frame-pointer"
            BUILD="${SRC_ROOT}-asan"
            SAN="-fsanitize=fuzzer,address"
            ;;
        coverage)
            CFH="-g -O0 ${COV_FLAGS}"
            BUILD="${SRC_ROOT}-cov"
            SAN="-fsanitize=fuzzer"
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    WORKDIR=$(mktemp -d)
    pushd "${WORKDIR}" >/dev/null

    # Same object-file compiles that fuzz/oss-fuzz-build.sh performs via
    # `make fuzz.o` / `make regexp.o` (AM_CPPFLAGS = -I<top_srcdir>/include
    # -I<top_builddir>/include; for this in-tree build both are ${BUILD}).
    "${CC}" -c ${CFH} ${SAN} ${BASE_CFLAGS} -fmacro-prefix-map=/src/= \
        -I "${BUILD}/include" -I "${HARNESS_DIR}" \
        "${HARNESS_DIR}/fuzz.c" -o fuzz.o
    "${CC}" -c ${CFH} ${SAN} ${BASE_CFLAGS} -fmacro-prefix-map=/src/= \
        -I "${BUILD}/include" -I "${HARNESS_DIR}" \
        "${HARNESS_DIR}/regexp.c" -o regexp.o

    # Same link line as fuzz/oss-fuzz-build.sh's per-fuzzer loop:
    #   $CXX $CXXFLAGS $OBJS fuzz.o -o $OUT/$fuzzer $LIB_FUZZING_ENGINE \
    #       ../.libs/libxml2.a -Wl,-Bstatic -lz -Wl,-Bdynamic
    # LIB_FUZZING_ENGINE for libFuzzer builds is literally "-fsanitize=fuzzer"
    # (see base-builder's compile_libfuzzer), already included in ${SAN}.
    "${CXX}" ${CFH} ${SAN} \
        regexp.o fuzz.o \
        "${BUILD}/.libs/libxml2.a" -Wl,-Bstatic -lz -Wl,-Bdynamic \
        -o "${OUT}/harness"

    popd >/dev/null
    rm -rf "${WORKDIR}"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
