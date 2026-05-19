# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
# Use OwenSanzas/fwupd fork's logitech-bulkcontroller-oob-repro branch
# which contains fuzzing/protocol_fuzzer.c + the device-side wrapper +
# the patched oss-fuzz.py needed to build it. Verbatim PoC from PR #9791.
ARG VULN_COMMIT=logitech-bulkcontroller-oob-repro
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
        python-is-python3 zip protobuf-compiler \
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

RUN git clone --depth 1 --branch ${VULN_COMMIT} https://github.com/OwenSanzas/fwupd /src/fwupd

WORKDIR /src/fwupd
RUN python3 contrib/ci/oss-fuzz.py

RUN mkdir -p /out/debug /out/debug-asan /out/release-asan /out/coverage \
 && for cfg in debug debug-asan release-asan coverage; do \
        cp /src/.ossfuzz/out/logitech-bulkcontroller-protocol_fuzzer /out/${cfg}/harness; \
    done \
 && ls -la /out/*/harness
