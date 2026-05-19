#!/bin/bash
# Build for ndpi-hex-decode-sscanf. nDPI uses autoconf.
set -euo pipefail

cmd="${1:?usage: build.sh build-libs | harness <config>}"

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    (cd /src/ndpi && autoreconf -fi)

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan)
                CF="-fsanitize=address,undefined -fno-sanitize-recover=undefined -g -O1"
                LF="-fsanitize=address,undefined"
                ;;
            cov)
                CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"
                LF="-fprofile-instr-generate -fcoverage-mapping"
                ;;
        esac

        cp -r /src/ndpi /src/build-${CONFIG_LIB}
        pushd /src/build-${CONFIG_LIB} >/dev/null

        env CC=clang CFLAGS="${CF}" LDFLAGS="${LF}" \
            ./configure --disable-shared --enable-static \
                        --without-pcap --without-zeromq \
                        --without-libgcrypt --without-libnuma

        make -j${JOBS} -C src/lib

        popd >/dev/null
    done
    echo "ndpi libs built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"

    case "${CONFIG}" in
        debug)
            CFH="-g -O0"
            BUILD=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        debug-asan)
            CFH="-g -O0"
            BUILD=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        release-asan)
            CFH="-O2 -g"
            BUILD=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        coverage)
            CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"
            BUILD=/src/build-cov
            SAN="-fsanitize=fuzzer"
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang ${CFH} ${SAN} -fmacro-prefix-map=/src/= \
        -I "${BUILD}/src/include" -I "${BUILD}/src/lib" \
        /src/harness/fuzz_ndpi_decode_tls_blocks.c \
        "${BUILD}/src/lib/libndpi.a" \
        -lpthread -lm \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi

echo "unknown subcommand: ${cmd}" >&2; exit 2
