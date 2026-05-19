# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=16305c1ed42f6aa310c5a60689690ee8ddfa3d22
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
        clang libclang-rt-14-dev \
        build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/simdutf/simdutf /src/simdutf \
 && git -C /src/simdutf checkout ${VULN_COMMIT}

COPY harness/fuzz_find_safe.cpp /src/harness/fuzz_find_safe.cpp
COPY harness/build.sh           /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs

RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
