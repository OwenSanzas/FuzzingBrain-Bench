# syntax=docker/dockerfile:1.6
#
# Per-bug reproducibility build for dtc-fdt32-misalign.
# Produces four harness binaries (debug, debug-asan, release-asan,
# coverage) under /out/, all built against dtc at the vulnerable
# commit. The binaries are extracted out of the image by Phase 2
# tooling and committed under bugs/<id>/binaries/ for the default
# Linux x86_64 user path; the Docker image itself is the cross-platform
# rebuild path.

FROM debian:bookworm-slim
# NOTE: tag-pinned in v1 reference impl. Real digest pin (SPEC §3.3)
# is a follow-up pass.

ARG VULN_COMMIT=a26ef6400bd845beb6e7f369809ca1c42d856904
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

# System deps. We pin Debian snapshot in a follow-up SPEC §3.3 sweep;
# for the v1 reference impl we accept the rolling bookworm-slim digest
# above as the pin.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        clang \
        libclang-rt-14-dev \
        build-essential \
        pkg-config \
        flex \
        bison \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Source @ vulnerable commit.
RUN git clone https://github.com/dgibson/dtc /src \
 && git -C /src checkout ${VULN_COMMIT}

# Drop our harness next to the source. /src/libfdt/* is dtc's own
# library; /src/harness/ is what we add.
COPY harness/fuzz_fdt.c   /src/harness/fuzz_fdt.c
COPY harness/build.sh     /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

# Build all four configs.
RUN mkdir -p /out \
 && /src/harness/build.sh debug \
 && /src/harness/build.sh debug-asan \
 && /src/harness/build.sh release-asan \
 && /src/harness/build.sh coverage

# Sanity: report sizes; useful in image build logs.
RUN ls -la /out/*/harness
