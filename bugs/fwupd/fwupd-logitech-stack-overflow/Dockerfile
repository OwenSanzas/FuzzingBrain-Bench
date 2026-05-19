# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=82a0998339c43fd36855492d9c5d68a20102c338
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev clang-tools \
        build-essential pkg-config meson ninja-build python3 python3-jinja2 \
        autoconf automake libtool gettext \
        zlib1g-dev liblzma-dev libzstd-dev libbz2-dev \
        libcurl4-openssl-dev libgnutls28-dev libsqlite3-dev \
        libreadline-dev help2man libelf-dev \
        cargo rustc libcbor-dev python3-packaging libprotobuf-c-dev protobuf-c-compiler \
        cmake nasm libffi-dev libpcre2-dev libpcre3-dev libmount-dev \
        python-is-python3 zip \
    && rm -rf /var/lib/apt/lists/*

ENV CC=clang
ENV CXX=clang++
ENV SRC=/src
ENV WORK=/src/.ossfuzz
ENV OUT=/src/.ossfuzz/out
ENV CFLAGS="-fsanitize=address -fsanitize=fuzzer-no-link -g -O1"
ENV CXXFLAGS="-fsanitize=address -fsanitize=fuzzer-no-link -g -O1"
ENV LDFLAGS="-fsanitize=address"
ENV LIB_FUZZING_ENGINE=/usr/lib/clang/14/lib/linux/libclang_rt.fuzzer-x86_64.a
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true

RUN git clone --filter=blob:none https://github.com/fwupd/fwupd /src/fwupd \
 && git -C /src/fwupd checkout ${VULN_COMMIT}

# Per upstream issue #9779 reproduction steps, the logitech-rdfu fuzzer is
# not built by default. Patch oss-fuzz.py to add it + tag the plugin's .c
# and .rs sources with # fuzzing.
RUN sed -i 's|        Fuzzer("ebitdo"),|        Fuzzer("ebitdo"),\n        Fuzzer("logitech-rdfu", srcdir="logitech-hidpp", pattern="logitech-rdfu-firmware"),|' /src/fwupd/contrib/ci/oss-fuzz.py \
 && sed -i "s|'fu-logitech-hidpp-rdfu.rs',|'fu-logitech-hidpp-rdfu.rs',  # fuzzing|" /src/fwupd/plugins/logitech-hidpp/meson.build \
 && sed -i "s|'fu-logitech-rdfu-firmware.c',|'fu-logitech-rdfu-firmware.c',  # fuzzing|" /src/fwupd/plugins/logitech-hidpp/meson.build \
 && sed -i "s|'fu-logitech-hidpp-rdfu-struct.rs',|'fu-logitech-hidpp-rdfu-struct.rs',  # fuzzing|" /src/fwupd/plugins/logitech-hidpp/meson.build 2>/dev/null || true

WORKDIR /src/fwupd
RUN python3 contrib/ci/oss-fuzz.py

RUN mkdir -p /out/debug /out/debug-asan /out/release-asan /out/coverage \
 && for cfg in debug debug-asan release-asan coverage; do \
        cp /src/.ossfuzz/out/logitech-rdfu_fuzzer /out/${cfg}/harness; \
    done \
 && ls -la /out/*/harness
