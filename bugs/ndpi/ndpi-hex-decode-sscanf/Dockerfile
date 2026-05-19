# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=68c7159a72dee1c42988eed65c7bfa08c510002e
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
        clang libclang-rt-14-dev \
        build-essential autoconf automake libtool pkg-config \
        libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/ntop/nDPI /src/ndpi \
 && git -C /src/ndpi checkout ${VULN_COMMIT}

COPY harness/fuzz_ndpi_decode_tls_blocks.c /src/harness/fuzz_ndpi_decode_tls_blocks.c
COPY harness/build.sh                       /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs

RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
