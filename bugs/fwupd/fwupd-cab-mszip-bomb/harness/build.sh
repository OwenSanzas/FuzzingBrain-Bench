#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

# fwupd build strategy: use Debian's system glib + json-glib + libxmlb
# rather than oss-fuzz.py's source-built deps. Meson configures fwupd
# with only libfwupdplugin (no daemon, no plugins, no Cab/firmware
# autostart). The cab_fuzzer binary is the meson custom_target that
# substitutes fu-fuzzer-firmware.c.in with @INCLUDE@=fu-cab-firmware.h
# and @GTYPE@=FU_TYPE_CAB_FIRMWARE.

JOBS=$(nproc)

if [ "${cmd}" = "build-libs" ]; then
    unset MAKEFLAGS MFLAGS

    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) CF="-fsanitize=address -g -O1"; LF="-fsanitize=address" ;;
            cov)  CF="-fprofile-instr-generate -fcoverage-mapping -g -O0"; LF="-fprofile-instr-generate -fcoverage-mapping" ;;
        esac

        cp -r /src/fwupd /src/fwupd-${CONFIG_LIB}
        pushd /src/fwupd-${CONFIG_LIB} >/dev/null
        env CC=clang CXX=clang++ CFLAGS="${CF}" CXXFLAGS="${CF}" LDFLAGS="${LF}" \
            meson setup build-${CONFIG_LIB} \
                --default-library=static \
                --buildtype=debug \
                -Dtests=false \
                -Ddocs=disabled \
                -Dintrospection=disabled \
                -Dbash_completion=false \
                -Dfish_completion=false \
                -Dman=false \
                -Dpolkit=disabled \
                -Dlvfs=disabled \
                -Dsupported_build=disabled \
                -Dsystemd=disabled \
                -Dhsi=disabled || true
        meson compile -C build-${CONFIG_LIB} -j ${JOBS} cab_fuzzer || true
        popd >/dev/null
    done
    echo "fwupd cab_fuzzer build attempted"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug|debug-asan|release-asan) BUILD=/src/fwupd-asan/build-asan ;;
        coverage) BUILD=/src/fwupd-cov/build-cov ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    SRC_BIN=$(find "${BUILD}" -name 'cab_fuzzer' -executable | head -1)
    test -n "${SRC_BIN}" || { echo "cab_fuzzer not built" >&2; exit 3; }
    cp "${SRC_BIN}" "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
