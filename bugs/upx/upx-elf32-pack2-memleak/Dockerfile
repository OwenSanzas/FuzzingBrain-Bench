# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=v5.0.2
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev binutils \
        build-essential cmake pkg-config \
        zlib1g-dev liblzma-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/upx/upx /src/upx \
 && git -C /src/upx fetch --depth 1 origin ${VULN_COMMIT} \
 && git -C /src/upx checkout ${VULN_COMMIT} \
 && git -C /src/upx submodule update --init --recursive

COPY harness/pack_file_fuzzer.cpp /src/harness/pack_file_fuzzer.cpp
COPY harness/build.sh             /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
