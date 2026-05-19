# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=22be3f1b8e3d8d09de89794a6d59f00176b32b2d
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential perl pkg-config \
    && rm -rf /var/lib/apt/lists/*

# OpenSSL repo is medium-sized; shallow clone of master then fetch the
# vuln SHA at extended depth.
ENV GIT_TERMINAL_PROMPT=0
RUN git clone --depth 1 --no-tags https://github.com/openssl/openssl /src/openssl \
 && git -C /src/openssl fetch --depth 500 origin master \
 && git -C /src/openssl checkout ${VULN_COMMIT}

COPY harness/des_ofb_fuzzer.c /src/harness/des_ofb_fuzzer.c
COPY harness/build.sh         /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
