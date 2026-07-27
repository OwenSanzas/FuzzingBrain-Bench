#!/bin/bash
# Build script for openscreen-01: build the target library (asan + coverage)
# and link the harness. Usage: build.sh build-libs | harness <config>
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"
JOBS=$(nproc)

JSONCPP=/src/jsoncpp
PRODDEF="-DNDEBUG -DJSON_USE_EXCEPTION=0 -fno-exceptions"

if [ "${cmd}" = "build-libs" ]; then
    unset MAKEFLAGS MFLAGS
    for CONFIG_LIB in asan cov; do
        case "${CONFIG_LIB}" in
            asan) INSTR="-fsanitize=address -g -O1" ;;
            cov)  INSTR="-fprofile-instr-generate -fcoverage-mapping -g -O0" ;;
        esac
        BUILD=/src/build-${CONFIG_LIB}
        mkdir -p "${BUILD}"
        for u in json_reader json_value json_writer; do
            clang++ -c ${INSTR} -std=c++17 ${PRODDEF} \
                -fmacro-prefix-map=/src/= \
                -I "${JSONCPP}/include" \
                "${JSONCPP}/src/lib_json/${u}.cpp" -o "${BUILD}/${u}.o"
        done
        ar rcs "${BUILD}/libjsoncpp.a" \
            "${BUILD}/json_reader.o" "${BUILD}/json_value.o" "${BUILD}/json_writer.o"
    done
    echo "jsoncpp built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/build-asan; SAN="-fsanitize=address" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/build-asan; SAN="-fsanitize=address" ;;
        release-asan) CFH="-O1 -g"; BUILD=/src/build-asan; SAN="-fsanitize=address" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/build-cov; SAN="" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang++ ${CFH} ${SAN} -std=c++17 ${PRODDEF} \
        -fmacro-prefix-map=/src/= \
        -I "${JSONCPP}/include" \
        /src/harness/jsoncpp_harness.cc \
        "${BUILD}/libjsoncpp.a" \
        -lpthread -lm \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
