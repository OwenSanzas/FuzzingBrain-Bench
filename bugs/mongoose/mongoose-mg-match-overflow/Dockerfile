# syntax=docker/dockerfile:1.6
#
# Per-bug reproducibility build for mongoose-mg-match-overflow.

FROM debian:bookworm-slim

ARG VULN_COMMIT=b313d697ff8c2156e85d411d7b3c226e4a0fefa9
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        clang \
        libclang-rt-14-dev \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/cesanta/mongoose /src \
 && git -C /src checkout ${VULN_COMMIT}

COPY harness/fuzzer.c   /src/harness/fuzzer.c
COPY harness/stubs.c    /src/harness/stubs.c
COPY harness/build.sh   /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN mkdir -p /out \
 && /src/harness/build.sh debug \
 && /src/harness/build.sh debug-asan \
 && /src/harness/build.sh release-asan \
 && /src/harness/build.sh coverage

RUN ls -la /out/*/harness
