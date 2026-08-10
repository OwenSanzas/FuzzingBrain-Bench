#!/bin/bash
# Drives assimp's own OSS-Fuzz recipe (oss-fuzz's projects/assimp/build.sh)
# inside the real oss-fuzz-base/base-builder image, reproducing the
# environment that OSS-Fuzz's `compile` script / compile_libfuzzer would set
# up (see infra/base-images/base-builder/compile{,_libfuzzer} upstream)
# instead of hand-writing compile/link lines.
#
# That upstream recipe is not vendored inside the assimp repo -- it lives in
# the oss-fuzz repo (projects/assimp/build.sh) and is COPYed into $SRC by
# OSS-Fuzz's own Dockerfile at image-build time. build_flavor() below embeds
# that recipe verbatim (cmake configure line + the `build_fuzzer` helper),
# trimmed to build only the assimp_fuzzer_fbx target instead of all seven
# fuzzers projects/assimp/build.sh normally builds.
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

SRC_ROOT=/src/assimp
BUILD_CACHE=/root/_build

# Builds libassimp + assimp_fuzzer_fbx for one (SANITIZER, CFLAGS) flavor in
# a private copy of the source tree, mirroring base-builder's compile script
# plus the cmake/ninja/build_fuzzer commands from projects/assimp/build.sh.
build_flavor() {
    local flavor="$1" sanitizer="$2" cflags_override="${3:-}"
    local tree="/src/assimp-${flavor}"
    local out="/tmp/ossout-${flavor}"

    rm -rf "${tree}" "${out}"
    cp -r "${SRC_ROOT}" "${tree}"
    mkdir -p "${out}"

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
        export OUT="${out}"
        export SANITIZER="${sanitizer}"
        export ARCHITECTURE=x86_64
        export FUZZING_ENGINE=libfuzzer
        export CC=clang
        export CXX=clang++
        export CFLAGS="${flags} ${san_flags} ${cov_flags}"
        export CXXFLAGS="${CFLAGS} ${CXXFLAGS_EXTRA}"
        # Same as infra/base-images/base-builder/compile_libfuzzer: the
        # libFuzzer engine link flag is independent of $SANITIZER.
        export LIB_FUZZING_ENGINE="-fsanitize=fuzzer"

        # --- verbatim from projects/assimp/build.sh: library build ---
        mkdir -p build
        cd build
        cmake .. \
          -G Ninja \
          -DCMAKE_C_COMPILER="${CC}" \
          -DCMAKE_CXX_COMPILER="${CXX}" \
          -DCMAKE_C_FLAGS="${CFLAGS}" \
          -DCMAKE_CXX_FLAGS="${CXXFLAGS}" \
          -DASSIMP_BUILD_ZLIB=ON \
          -DASSIMP_BUILD_TESTS=OFF \
          -DASSIMP_BUILD_ASSIMP_TOOLS=OFF \
          -DBUILD_SHARED_LIBS=OFF \
          -DASSIMP_BUILD_ALL_IMPORTERS_BY_DEFAULT=ON \
          -DASSIMP_BUILD_ALL_EXPORTERS_BY_DEFAULT=ON
        ninja

        # --- verbatim from projects/assimp/build.sh: build_fuzzer(), run
        # only for assimp_fuzzer_fbx (the fuzz target this bundle covers) ---
        "${CXX}" ${CXXFLAGS} -I../include -I../build/include -c "../fuzz/assimp_fuzzer_fbx.cc" -o "assimp_fuzzer_fbx.o"
        "${CXX}" ${CXXFLAGS} ${LIB_FUZZING_ENGINE} "assimp_fuzzer_fbx.o" -o "${OUT}/assimp_fuzzer_fbx" \
            ./lib/libassimp.a \
            ./contrib/zlib/libzlibstatic.a \
            -lpthread -ldl
    )

    mkdir -p "${BUILD_CACHE}/${flavor}"
    cp "${out}/assimp_fuzzer_fbx" "${BUILD_CACHE}/${flavor}/assimp_fuzzer_fbx"
    rm -rf "${tree}" "${out}"
}

if [ "${cmd}" = "build-libs" ]; then
    # debug / debug-asan share one -O0 ASan build; release-asan uses the
    # same default (-O1) flags OSS-Fuzz actually built with; coverage gets
    # its own SanitizerCoverage/profiling build.
    build_flavor debug   address  "-O0 -g"
    build_flavor asan    address  ""
    build_flavor cov     coverage ""
    echo "assimp_fuzzer_fbx built for debug/asan/coverage flavors"
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
    cp "${BUILD_CACHE}/${FLAVOR}/assimp_fuzzer_fbx" "${OUT}/harness"
    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi

exit 2
