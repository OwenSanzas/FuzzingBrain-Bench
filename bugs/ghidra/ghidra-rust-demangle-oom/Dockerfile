# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=caeeac88ddb5b004736de440db86380ff6e61229
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates clang libclang-rt-14-dev \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Ghidra bundles a libiberty copy at GPL/DemanglerGnu/src/demangler_gnu_v2_41/c.
# We only need 5 files (.c × 2 + .h × 3) — fetch them directly via raw.github
# at the vuln commit. Avoids cloning the multi-GB Ghidra tree.
ENV GHIDRA_BASE=https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/${VULN_COMMIT}/GPL/DemanglerGnu/src/demangler_gnu_v2_41
RUN mkdir -p /src/ghidra-demangler \
 && for f in rust-demangle.c safe-ctype.c; do \
        curl -fsSL "${GHIDRA_BASE}/c/${f}" -o "/src/ghidra-demangler/${f}" ; \
    done \
 && for f in demangle.h safe-ctype.h ansidecl.h libiberty.h; do \
        curl -fsSL "${GHIDRA_BASE}/headers/${f}" -o "/src/ghidra-demangler/${f}" ; \
    done \
 && ls -la /src/ghidra-demangler/

COPY harness/fuzz_rust_demangle.c /src/harness/fuzz_rust_demangle.c
COPY harness/build.sh             /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
