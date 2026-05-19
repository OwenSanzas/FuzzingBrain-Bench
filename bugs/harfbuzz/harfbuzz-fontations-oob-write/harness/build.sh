#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

# HarfBuzz with fontations enabled. The fontations backend is a Rust
# crate (read-fonts + skrifa); meson invokes cargo for it.

if [ "${cmd}" = "build-libs" ]; then
    JOBS=$(nproc)
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address -g -O1"; LF="-fsanitize=address" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/harfbuzz /src/hb-${CONFIG_LIB}
        pushd /src/hb-${CONFIG_LIB} >/dev/null
        # The fontations bug is an OOB write that happens AFTER an unsigned
        # subtract underflows. Rust's debug build catches the underflow as
        # `attempt to subtract with overflow` (panic) BEFORE the OOB happens,
        # masking the true vulnerability. Disable overflow checks so the
        # subtraction wraps and the OOB write reaches ASan.
        env CC=clang CXX=clang++ CFLAGS="${CF}" CXXFLAGS="${CF}" LDFLAGS="${LF}" \
            RUSTFLAGS="-C overflow-checks=no" \
            meson setup build-${CONFIG_LIB} \
                --default-library=static \
                --buildtype=debug \
                -Dfontations=enabled \
                -Dtests=disabled \
                -Ddocs=disabled \
                -Dutilities=disabled \
                -Dintrospection=disabled \
                -Dgobject=disabled \
                -Dcairo=disabled \
                -Dfreetype=disabled \
                -Dglib=disabled \
                -Dicu=disabled \
                -Dchafa=disabled \
                -Db_lundef=false
        meson compile -C build-${CONFIG_LIB} -j ${JOBS}
        popd >/dev/null
    done
    echo "harfbuzz w/ fontations built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/hb-asan; BUILD_SUB=asan; SAN="-fsanitize=fuzzer,address" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/hb-asan; BUILD_SUB=asan; SAN="-fsanitize=fuzzer,address" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/hb-asan; BUILD_SUB=asan; SAN="-fsanitize=fuzzer,address" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/hb-cov; BUILD_SUB=cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    HB_LIBS=$(ls "${BUILD}/build-${BUILD_SUB}"/src/libharfbuzz*.a 2>/dev/null | head -20)
    if [ -z "${HB_LIBS}" ]; then
        HB_LIBS=$(find "${BUILD}" -name 'libharfbuzz*.a' -not -path '*test*' 2>/dev/null)
    fi
    HB_RUST=$(find "${BUILD}/build-${BUILD_SUB}" -name 'libharfbuzz_rust.a' 2>/dev/null | head -1)

    clang ${CFH} ${SAN} -fmacro-prefix-map=/src/= \
        -I "${BUILD}/src" \
        -I "${BUILD}/build-${BUILD_SUB}/src" \
        /src/harness/fontations_glyph_name.c \
        ${HB_LIBS} ${HB_RUST} \
        -lpthread -lm -ldl -lstdc++ \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
