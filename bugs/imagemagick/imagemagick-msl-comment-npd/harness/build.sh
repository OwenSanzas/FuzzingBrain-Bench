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

        cp -r /src/ImageMagick /src/im-${CONFIG_LIB}
        pushd /src/im-${CONFIG_LIB} >/dev/null
        env CC=clang CXX=clang++ CFLAGS="${CF}" CXXFLAGS="${CF}" LDFLAGS="${LF}" \
            ./configure --disable-shared --enable-static \
                        --disable-docs --without-perl \
                        --without-x --without-modules \
                        --disable-openmp \
                        --without-jpeg --without-png --without-tiff --without-webp \
                        --without-zlib --without-bzlib --without-zstd --without-lzma \
                        --without-freetype --without-fontconfig --without-pango \
                        --without-raqm --without-djvu --without-jbig --without-jp2 --without-openjp2 \
                        --without-fpx --without-flif --without-heic --without-jxl \
                        --without-raw --without-lqr
        make -j${JOBS}
        popd >/dev/null
    done
    echo "ImageMagick libs built"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    mkdir -p "${OUT}"
    case "${CONFIG}" in
        debug)        CFH="-g -O0"; BUILD=/src/im-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        debug-asan)   CFH="-g -O0"; BUILD=/src/im-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        release-asan) CFH="-O2 -g"; BUILD=/src/im-asan; SAN="-fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined" ;;
        coverage)     CFH="-g -O0 -fprofile-instr-generate -fcoverage-mapping"; BUILD=/src/im-cov; SAN="-fsanitize=fuzzer" ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    clang++ ${CFH} ${SAN} -fmacro-prefix-map=/src/= -std=c++17 \
        -DMAGICKCORE_QUANTUM_DEPTH=16 -DMAGICKCORE_HDRI_ENABLE=1 \
        -I "${BUILD}" -I "${BUILD}/Magick++/lib" \
        -I /src/harness \
        /src/harness/msl_fuzzer.cc \
        "${BUILD}/Magick++/lib/.libs/libMagick++-7.Q16HDRI.a" \
        "${BUILD}/MagickWand/.libs/libMagickWand-7.Q16HDRI.a" \
        "${BUILD}/MagickCore/.libs/libMagickCore-7.Q16HDRI.a" \
        -lxml2 -lpthread -lm \
        -o "${OUT}/harness"

    echo "built ${OUT}/harness ($(stat -c %s "${OUT}/harness") bytes)"
    exit 0
fi
exit 2
