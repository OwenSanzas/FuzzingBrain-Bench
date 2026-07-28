#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"
JOBS=$(nproc)

SRC=/src/libhevc

if [ "${cmd}" = "build-libs" ]; then
    unset MAKEFLAGS MFLAGS
    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) INSTR="-fsanitize=address -fno-omit-frame-pointer -g -O1" ;;
            cov)  INSTR="-fprofile-instr-generate -fcoverage-mapping -g -O0" ;;
        esac
        cmake -S ${SRC} -B /src/build-${CONFIG_LIB} \
            -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
            -DCMAKE_BUILD_TYPE=Debug \
            -DCMAKE_C_FLAGS="${INSTR}" \
            -DCMAKE_CXX_FLAGS="${INSTR}" \
            -DCMAKE_EXE_LINKER_FLAGS="${INSTR}" \
            >/dev/null
        cmake --build /src/build-${CONFIG_LIB} -j${JOBS} --target libhevcdec >/dev/null 2>&1
    done
    echo "libhevcdec built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug|debug-asan|release-asan)
            CFLAGS_H="$([ "${CONFIG}" = "release-asan" ] && echo "-O2 -g" || echo "-g -O0")"
            BUILD=/src/build-asan
            SAN="-fsanitize=fuzzer,address"
            ;;
        coverage)
            CFLAGS_H="-g -O0 -fprofile-instr-generate -fcoverage-mapping"
            BUILD=/src/build-cov
            SAN="-fsanitize=fuzzer"
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang++ \
        ${CFLAGS_H} \
        ${SAN} \
        -std=c++17 \
        -msse4.2 -mno-avx \
        -DX86 -DX86_LINUX=1 -DDISABLE_AVX2 -DDEFAULT_ARCH=D_ARCH_X86_SSE42 \
        -fmacro-prefix-map=/src/= \
        -I "${SRC}/common" -I "${SRC}/decoder" -I "${SRC}/common/x86" \
        -I "${SRC}/decoder/x86" -I "${BUILD}" \
        /src/harness/harness.cpp \
        "${BUILD}/libhevcdec.a" \
        -lm -lpthread \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
