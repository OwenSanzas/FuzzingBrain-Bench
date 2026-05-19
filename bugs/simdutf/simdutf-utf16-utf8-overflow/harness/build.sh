#!/bin/bash
# Build script for simdutf-utf16-utf8-overflow.
set -euo pipefail

cmd="${1:?usage: build.sh build-libs | harness <config>}"

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan)
                # UBSan triggers a clang-14 backend bug on simdutf's SIMD code;
                # drop UBSan and use ASan only.
                INSTR="-fsanitize=address -g -O1"
                ;;
            cov)
                INSTR="-fprofile-instr-generate -fcoverage-mapping -g -O0"
                ;;
        esac

        cmake -S /src/simdutf -B /src/build-${CONFIG_LIB} \
            -DCMAKE_C_COMPILER=clang \
            -DCMAKE_CXX_COMPILER=clang++ \
            -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_C_FLAGS="${INSTR}" \
            -DCMAKE_CXX_FLAGS="${INSTR}" \
            -DCMAKE_EXE_LINKER_FLAGS="${INSTR}" \
            -DBUILD_SHARED_LIBS=OFF \
            -DSIMDUTF_TESTS=OFF \
            -DSIMDUTF_BENCHMARKS=OFF \
            -DSIMDUTF_TOOLS=OFF
        cmake --build /src/build-${CONFIG_LIB} -j${JOBS} --target simdutf
    done
    echo "simdutf libs built"
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

    LIBS=$(find "${BUILD}" -name 'libsimdutf*.a' -type f)

    clang++ \
        ${CFLAGS_H} \
        ${SAN} \
        -fmacro-prefix-map=/src/= \
        -std=c++17 \
        -I /src/simdutf/include \
        /src/harness/fuzz_find_safe.cpp \
        ${LIBS} \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
echo "unknown subcommand: ${cmd}" >&2
exit 2
