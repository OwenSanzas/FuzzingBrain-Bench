#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address -g -O1"; LF="-fsanitize=address" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/opencv /src/opencv-${CONFIG_LIB}
        BUILD_DIR=/src/opencv-${CONFIG_LIB}/build
        mkdir -p "${BUILD_DIR}"
        pushd "${BUILD_DIR}" >/dev/null
        # core-only build (drop imgproc/imgcodecs/highgui/etc.) keeps cmake fast.
        cmake .. \
            -DCMAKE_BUILD_TYPE=Debug \
            -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
            -DCMAKE_C_FLAGS="${CF}" \
            -DCMAKE_CXX_FLAGS="${CF}" \
            -DCMAKE_EXE_LINKER_FLAGS="${LF}" \
            -DBUILD_LIST=core \
            -DBUILD_SHARED_LIBS=OFF \
            -DBUILD_EXAMPLES=OFF \
            -DBUILD_TESTS=OFF \
            -DBUILD_PERF_TESTS=OFF \
            -DBUILD_PROTOBUF=OFF \
            -DBUILD_opencv_apps=OFF \
            -DBUILD_opencv_python2=OFF \
            -DBUILD_opencv_python3=OFF \
            -DBUILD_opencv_java=OFF \
            -DWITH_PROTOBUF=OFF \
            -DWITH_IPP=OFF \
            -DWITH_TBB=OFF \
            -DWITH_OPENCL=OFF \
            -DWITH_OPENMP=OFF \
            -DWITH_EIGEN=OFF \
            -DWITH_LAPACK=OFF \
            -DWITH_QUIRC=OFF \
            -DWITH_ITT=OFF \
            -DOPENCV_GENERATE_PKGCONFIG=OFF \
            -DCV_DISABLE_OPTIMIZATION=ON
        make -j${JOBS} opencv_core
        popd >/dev/null
    done
    echo "opencv core built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/opencv-asan; SAN="-fsanitize=fuzzer,address" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/opencv-asan; SAN="-fsanitize=fuzzer,address" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/opencv-asan; SAN="-fsanitize=fuzzer,address" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/opencv-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    # opencv_core static archive + transitive deps (zlib only with our minimal config).
    CV_LIB=$(find "${BUILD}/build" -name 'libopencv_core.a' | head -1)
    test -n "${CV_LIB}"

    # Sometimes opencv bundles zlib in 3rdparty.
    EXTRA_LIBS=$(find "${BUILD}/build/3rdparty" -name '*.a' 2>/dev/null | xargs -r echo)

    clang++ ${CFH} ${SAN} -fmacro-prefix-map=/src/= -std=c++17 \
        -I "${BUILD}/include" \
        -I "${BUILD}/modules/core/include" \
        -I "${BUILD}/build" \
        /src/harness/yaml_fuzzer.cpp \
        "${CV_LIB}" ${EXTRA_LIBS} \
        -lz -lpthread -lm -ldl \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
