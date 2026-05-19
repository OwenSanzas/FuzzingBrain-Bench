#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address,undefined -fno-sanitize-recover=undefined -g -O1"; LF="-fsanitize=address,undefined" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/openssl /src/openssl-${CONFIG_LIB}
        pushd /src/openssl-${CONFIG_LIB} >/dev/null
        env CC=clang \
            ./Configure linux-x86_64 \
                no-shared no-tests no-asm \
                -DPURIFY \
                -fno-omit-frame-pointer \
                "${CF}" \
                -ldl -lpthread "${LF}"
        make -j${JOBS} build_generated
        make -j${JOBS} libcrypto.a
        popd >/dev/null
    done
    echo "openssl libcrypto built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/openssl-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/openssl-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/openssl-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/openssl-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang ${CFH} ${SAN} -fmacro-prefix-map=/src/= \
        -I "${BUILD}/include" \
        /src/harness/des_ofb_fuzzer.c \
        "${BUILD}/libcrypto.a" \
        -ldl -lpthread \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
