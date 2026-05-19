#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

ICU_DIR=/src/icu/icu4c/source

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address,undefined -fno-sanitize-recover=undefined -g -O1"; LF="-fsanitize=address,undefined" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/icu /src/icu-${CONFIG_LIB}
        pushd /src/icu-${CONFIG_LIB}/icu4c/source >/dev/null
        env CC=clang CXX=clang++ CFLAGS="${CF}" CXXFLAGS="${CF} -std=c++17" LDFLAGS="${LF}" \
            ./runConfigureICU Linux --enable-static --disable-shared \
                                    --disable-tests --disable-samples \
                                    --disable-extras --disable-tools
        mkdir -p lib stubdata
        make -j${JOBS} -C stubdata
        make -j${JOBS} -C common
        make -j${JOBS} -C i18n
        popd >/dev/null
    done
    echo "icu libs built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/icu-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/icu-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/icu-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/icu-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang++ ${CFH} ${SAN} -std=c++17 -fmacro-prefix-map=/src/= \
        -I "${BUILD}/icu4c/source/common" \
        -I "${BUILD}/icu4c/source/i18n" \
        /src/harness/translit_fuzzer.cpp \
        "${BUILD}/icu4c/source/lib/libicui18n.a" \
        "${BUILD}/icu4c/source/lib/libicuuc.a" \
        "${BUILD}/icu4c/source/stubdata/libicudata.a" \
        -lpthread -lm -ldl \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
