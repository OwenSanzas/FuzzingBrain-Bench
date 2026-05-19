# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=b357f083ea5ae0ee7b09a65a48621f345e710588
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential cmake pkg-config python3 nasm meson ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Pin libavif to the vulnerable commit (Android JNI shim with int length).
RUN git clone --filter=blob:none https://github.com/AOMediaCodec/libavif /src/libavif \
 && git -C /src/libavif checkout ${VULN_COMMIT}

COPY harness/ /src/harness/
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug 2>&1 \
 && /src/harness/build.sh harness debug-asan 2>&1 \
 && /src/harness/build.sh harness release-asan 2>&1 \
 && /src/harness/build.sh harness coverage 2>&1

RUN ls -la /out/*/harness
