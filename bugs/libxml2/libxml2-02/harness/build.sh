#!/bin/bash
# Drives libxml2's own OSS-Fuzz recipe (fuzz/oss-fuzz-build.sh) inside the
# real oss-fuzz-base/base-builder image, reproducing the environment that
# OSS-Fuzz's `compile` script would set up (see
# infra/base-images/base-builder/compile and compile_libfuzzer upstream)
# instead of hand-writing compile/link lines.
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

SRC_ROOT=/src/libxml2
BUILD_CACHE=/root/_build

# Runs fuzz/oss-fuzz-build.sh for one (SANITIZER, CFLAGS) flavor inside a
# private copy of the source tree, mimicking base-builder's compile script.
build_flavor() {
    local flavor="$1" sanitizer="$2" cflags_override="${3:-}"
    local tree="/src/libxml2-${flavor}"
    local work="/tmp/work-${flavor}"
    local out="/tmp/ossout-${flavor}"

    rm -rf "${tree}" "${work}" "${out}"
    cp -r "${SRC_ROOT}" "${tree}"
    mkdir -p "${work}" "${out}"

    local flags="${CFLAGS}"
    if [ -n "${cflags_override}" ]; then
        flags="${flags/-O1/${cflags_override}}"
    fi

    local san_flags_var="SANITIZER_FLAGS_${sanitizer}"
    local san_flags="${!san_flags_var-}"

    local cov_flags="${COVERAGE_FLAGS}"
    local cov_flags_var="COVERAGE_FLAGS_${sanitizer}"
    if [ -n "${!cov_flags_var+x}" ]; then
        cov_flags="${!cov_flags_var}"
    fi

    (
        cd "${tree}"
        export SRC=/src
        export WORK="${work}"
        export OUT="${out}"
        export SANITIZER="${sanitizer}"
        export ARCHITECTURE=x86_64
        export FUZZING_ENGINE=libfuzzer
        export CC=clang
        export CXX=clang++
        export CFLAGS="${flags} ${san_flags} ${cov_flags}"
        export CXXFLAGS="${CFLAGS} ${CXXFLAGS_EXTRA}"
        export LIB_FUZZING_ENGINE="-fsanitize=fuzzer"
        bash fuzz/oss-fuzz-build.sh
    )

    mkdir -p "${BUILD_CACHE}/${flavor}"
    cp "${out}/valid" "${BUILD_CACHE}/${flavor}/valid"
    rm -rf "${tree}" "${work}" "${out}"
}

if [ "${cmd}" = "build-libs" ]; then
    # debug / debug-asan share one -O0 ASan build; release-asan uses the
    # same default (-O1) flags OSS-Fuzz actually built with; coverage gets
    # its own SanitizerCoverage/profiling build.
    build_flavor debug   address  "-O0 -g"
    build_flavor asan    address  ""
    build_flavor cov     coverage ""
    echo "libxml2 valid fuzzer built for debug/asan/coverage flavors"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    case "${CONFIG}" in
        debug)        FLAVOR=debug; OUT=/out/vuln/debug ;;
        debug-asan)   FLAVOR=debug; OUT=/out/vuln/debug-asan ;;
        release-asan) FLAVOR=asan;  OUT=/out/vuln/asan ;;
        coverage)     FLAVOR=cov;   OUT=/out/vuln/cov ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    mkdir -p "${OUT}"
    cp "${BUILD_CACHE}/${FLAVOR}/valid" "${OUT}/harness"
    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi

exit 2
