#!/bin/bash
# Build script for openldap-ldif-stack-underflow.
#
# Strategy: build openldap (just liblber + libldap) twice —
#   - /src/build-asan/   compiled with -fsanitize=address,undefined
#   - /src/build-cov/    compiled with coverage instrumentation
# Then link the harness in four configs, reusing those two library
# builds. This is roughly 2× the openldap configure-make cost (~5 min
# each) plus ~2 sec per harness link.
#
# Usage:
#   build.sh openldap-libs     # builds both library trees, called once
#   build.sh harness <config>  # links the harness for one config

set -euo pipefail

cmd="${1:?usage: build.sh openldap-libs | harness <config>}"

if [ "${cmd}" = "openldap-libs" ]; then
    JOBS=$(nproc)

    # asan/ubsan-instrumented build
    cp -r /src/openldap /src/build-asan
    pushd /src/build-asan >/dev/null
    CC=clang \
        CFLAGS="-fsanitize=address,undefined -fno-sanitize-recover=undefined -g -O1" \
        LDFLAGS="-fsanitize=address,undefined" \
        ./configure --without-tls --without-cyrus-sasl --without-systemd \
                    --disable-slapd --disable-overlays --disable-modules \
                    --enable-static=yes --enable-shared=no >/dev/null
    make -j${JOBS} depend >/dev/null 2>&1
    # Build only the library tree; skip clients/servers which depend on
    # extra libs we don't need. Build all libraries together so deps
    # (liblutil -> liblber -> libldap) resolve.
    make -j${JOBS} -C libraries >/dev/null 2>&1
    popd >/dev/null

    # coverage-instrumented build
    cp -r /src/openldap /src/build-cov
    pushd /src/build-cov >/dev/null
    CC=clang \
        CFLAGS="-fprofile-instr-generate -fcoverage-mapping -g -O0" \
        LDFLAGS="-fprofile-instr-generate -fcoverage-mapping" \
        ./configure --without-tls --without-cyrus-sasl --without-systemd \
                    --disable-slapd --disable-overlays --disable-modules \
                    --enable-static=yes --enable-shared=no >/dev/null
    make -j${JOBS} depend >/dev/null 2>&1
    make -j${JOBS} -C libraries >/dev/null 2>&1
    popd >/dev/null

    echo "openldap libraries built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"

    case "${CONFIG}" in
        debug)
            CFLAGS="-g -O0"
            LIBS_DIR=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        debug-asan)
            CFLAGS="-g -O0"
            LIBS_DIR=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        release-asan)
            CFLAGS="-O2 -g"
            LIBS_DIR=/src/build-asan
            SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined"
            ;;
        coverage)
            CFLAGS="-g -O0 -fprofile-instr-generate -fcoverage-mapping"
            LIBS_DIR=/src/build-cov
            SAN="-fsanitize=fuzzer"
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    # -include stdio.h: the user-authored harness omits stdio.h but
    # openldap's ldif.h uses FILE. Force-include keeps fuzz_ldif.c
    # unchanged.
    clang \
        ${CFLAGS} \
        ${SAN} \
        -include stdio.h \
        -fmacro-prefix-map=/src/= \
        -I "${LIBS_DIR}/include" \
        /src/harness/fuzz_schema.c \
        "${LIBS_DIR}/libraries/libldap/.libs/libldap.a" \
        "${LIBS_DIR}/libraries/liblber/.libs/liblber.a" \
        "${LIBS_DIR}/libraries/liblutil/liblutil.a" \
        -lresolv \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi

echo "unknown subcommand: ${cmd}" >&2
exit 2
