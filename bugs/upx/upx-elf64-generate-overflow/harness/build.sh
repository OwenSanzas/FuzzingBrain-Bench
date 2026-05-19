#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

# UPX strategy:
# 1. cmake build target `upx` to get all .o files
# 2. include main.cpp.o (so upx_main / main_set_exit_code / progname etc.
#    are present) but use a renamed main(), so libFuzzer's main() wins
# 3. our harness calls upx_main() inside LLVMFuzzerTestOneInput
#
# The rename is via objcopy: rename `main` symbol in main.cpp.o to a
# different name post-compile.

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address,undefined -fno-sanitize-recover=undefined -g -O1"; LF="-fsanitize=address,undefined" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/upx /src/upx-${CONFIG_LIB}
        BUILD_DIR=/src/upx-${CONFIG_LIB}/build
        mkdir -p "${BUILD_DIR}"
        pushd "${BUILD_DIR}" >/dev/null
        env CC=clang CXX=clang++ CFLAGS="${CF}" CXXFLAGS="${CF} -funsigned-char -Wno-writable-strings" LDFLAGS="${LF}" \
            cmake .. \
                -DCMAKE_BUILD_TYPE=Debug \
                -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
                -DCMAKE_C_FLAGS="${CF}" \
                -DCMAKE_CXX_FLAGS="${CF} -funsigned-char -Wno-writable-strings" \
                -DCMAKE_EXE_LINKER_FLAGS="${LF}"
        for tgt in upx_vendor_ucl upx_vendor_zlib upx_vendor_bzip2 upx_vendor_zstd; do
            make -j${JOBS} ${tgt} 2>/dev/null || true
        done
        make -j${JOBS} upx || true
        # Take all upx.dir .o files. Rename main symbol so libFuzzer wins.
        cd CMakeFiles/upx.dir
        objcopy --redefine-sym main=upx_orig_main src/main.cpp.o
        find . -name '*.o' > /tmp/upx-objs-${CONFIG_LIB}.list
        ar rcs /src/upx-${CONFIG_LIB}/libupx.a $(cat /tmp/upx-objs-${CONFIG_LIB}.list)
        popd >/dev/null
    done
    echo "upx libs built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/upx-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/upx-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/upx-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/upx-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang++ ${CFH} ${SAN} -fmacro-prefix-map=/src/= -std=c++17 -funsigned-char -Wno-writable-strings \
        -I "${BUILD}/src" \
        -I "${BUILD}/vendor" \
        -I "${BUILD}/vendor/ucl/include" \
        -I "${BUILD}/vendor/lzma-sdk" \
        -I "${BUILD}/vendor/zlib" \
        /src/harness/pack_file_fuzzer.cpp \
        -Wl,--whole-archive "${BUILD}/libupx.a" -Wl,--no-whole-archive \
        "${BUILD}/build/libupx_vendor_ucl.a" \
        "${BUILD}/build/libupx_vendor_zlib.a" \
        $(test -f "${BUILD}/build/libupx_vendor_bzip2.a" && echo "${BUILD}/build/libupx_vendor_bzip2.a") \
        $(test -f "${BUILD}/build/libupx_vendor_zstd.a" && echo "${BUILD}/build/libupx_vendor_zstd.a") \
        -lz -llzma -lpthread -lm \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
