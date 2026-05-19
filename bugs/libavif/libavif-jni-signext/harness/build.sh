#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

JOBS=$(nproc)

if [ "${cmd}" = "build-libs" ]; then
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address -g -O1"; LF="-fsanitize=address" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/libavif /src/libavif-${CONFIG_LIB}
        BUILD_DIR=/src/libavif-${CONFIG_LIB}/build
        mkdir -p "${BUILD_DIR}"
        pushd "${BUILD_DIR}" >/dev/null
        cmake .. \
            -DCMAKE_BUILD_TYPE=Debug \
            -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
            -DCMAKE_C_FLAGS="${CF}" \
            -DCMAKE_CXX_FLAGS="${CF}" \
            -DCMAKE_EXE_LINKER_FLAGS="${LF}" \
            -DBUILD_SHARED_LIBS=OFF \
            -DAVIF_BUILD_APPS=OFF \
            -DAVIF_BUILD_TESTS=OFF \
            -DAVIF_CODEC_DAV1D=LOCAL \
            -DAVIF_LIBYUV=OFF \
            -DAVIF_JPEG=OFF \
            -DAVIF_LIBSHARPYUV=OFF \
            -DAVIF_ZLIBPNG=OFF
        make -j${JOBS} avif
        popd >/dev/null
    done
    echo "libavif built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/libavif-asan; SAN="-fsanitize=fuzzer,address" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/libavif-asan; SAN="-fsanitize=fuzzer,address" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/libavif-asan; SAN="-fsanitize=fuzzer,address" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/libavif-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    AVIF_LIB=$(find "${BUILD}/build" -maxdepth 2 -name 'libavif*.a' | head -1)
    test -n "${AVIF_LIB}" || { echo "no libavif*.a found under ${BUILD}/build" >&2; exit 3; }
    DAV1D_LIB=$(find "${BUILD}/build" -name 'libdav1d.a' | head -1)

    clang++ ${CFH} ${SAN} -fmacro-prefix-map=/src/= -std=c++17 \
        -I "${BUILD}/include" \
        /src/harness/jni_signext_fuzzer.cpp \
        "${AVIF_LIB}" ${DAV1D_LIB} \
        -lpthread -lm -ldl \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
