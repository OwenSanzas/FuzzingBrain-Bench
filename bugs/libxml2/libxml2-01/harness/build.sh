#!/bin/bash
# libxml2 ships its own OSS-Fuzz target at fuzz/regexp.c (+ fuzz/fuzz.c helpers)
# inside the checked-out tree -- no harness source to COPY in. Build the library
# twice (asan, coverage) via autogen.sh, then link fuzz/regexp.o + fuzz/fuzz.o
# against the static lib.
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"
JOBS=$(nproc)

if [ "${cmd}" = "build-libs" ]; then
    cp -r /src/libxml2 /src/libxml2-asan
    cp -r /src/libxml2 /src/libxml2-cov

    pushd /src/libxml2-asan >/dev/null
    CC=clang CXX=clang++ \
        CFLAGS="-fsanitize=address -g -O1 -fno-omit-frame-pointer" \
        CXXFLAGS="-fsanitize=address -g -O1 -fno-omit-frame-pointer" \
        LDFLAGS="-fsanitize=address" \
        ./autogen.sh --disable-shared --without-debug --without-http \
            --without-python --with-zlib >/dev/null
    make -j${JOBS} >/dev/null 2>&1
    popd >/dev/null

    pushd /src/libxml2-cov >/dev/null
    CC=clang CXX=clang++ \
        CFLAGS="-fprofile-instr-generate -fcoverage-mapping -g -O0" \
        CXXFLAGS="-fprofile-instr-generate -fcoverage-mapping -g -O0" \
        LDFLAGS="-fprofile-instr-generate -fcoverage-mapping" \
        ./autogen.sh --disable-shared --without-debug --without-http \
            --without-python --with-zlib >/dev/null
    make -j${JOBS} >/dev/null 2>&1
    popd >/dev/null

    echo "libxml2 built (asan + coverage)"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"

    case "${CONFIG}" in
        debug|debug-asan|release-asan)
            CFLAGS_H="$([ "${CONFIG}" = "release-asan" ] && echo "-O2 -g" || echo "-g -O0")"
            BUILD=/src/libxml2-asan
            SAN="-fsanitize=fuzzer,address"
            ;;
        coverage)
            CFLAGS_H="-g -O0 -fprofile-instr-generate -fcoverage-mapping"
            BUILD=/src/libxml2-cov
            SAN="-fsanitize=fuzzer"
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang \
        ${CFLAGS_H} \
        ${SAN} \
        -fmacro-prefix-map=/src/= \
        -I "${BUILD}/include" -I "${BUILD}" \
        -c "${BUILD}/fuzz/regexp.c" -o "${OUT}/regexp.o"
    clang \
        -g -O1 \
        -fmacro-prefix-map=/src/= \
        -I "${BUILD}/include" -I "${BUILD}" \
        -c "${BUILD}/fuzz/fuzz.c" -o "${OUT}/fuzz.o"

    clang \
        ${CFLAGS_H} \
        ${SAN} \
        "${OUT}/regexp.o" "${OUT}/fuzz.o" \
        "${BUILD}/.libs/libxml2.a" \
        -Wl,-Bstatic -lz -Wl,-Bdynamic -llzma \
        -o "${OUT}/harness"

    rm -f "${OUT}/regexp.o" "${OUT}/fuzz.o"
    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
