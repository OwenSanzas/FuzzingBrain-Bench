# syntax=docker/dockerfile:1.6
#
# Per-bug reproducibility build for openldap-ldif-stack-underflow.

FROM debian:bookworm-slim

ARG VULN_COMMIT=5129517b6956f4679b110af854e04f0d3f7be576
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        clang \
        libclang-rt-14-dev \
        build-essential \
        autoconf \
        automake \
        libtool \
        groff-base \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/openldap/openldap /src/openldap \
 && git -C /src/openldap checkout ${VULN_COMMIT}

COPY harness/fuzz_schema.c /src/harness/fuzz_schema.c
COPY harness/build.sh    /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

# Library builds (slow — ~5min × 2)
RUN /src/harness/build.sh openldap-libs

# Harness link per config (fast)
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
