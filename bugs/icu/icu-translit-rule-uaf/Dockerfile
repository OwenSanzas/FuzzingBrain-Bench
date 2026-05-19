# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=8be6575f5c4ea397d384493a435b2932d5c2eaca
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential pkg-config python3 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/unicode-org/icu /src/icu \
 && git -C /src/icu fetch --depth 1 origin ${VULN_COMMIT} \
 && git -C /src/icu checkout ${VULN_COMMIT}

COPY harness/translit_fuzzer.cpp /src/harness/translit_fuzzer.cpp
COPY harness/build.sh             /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
