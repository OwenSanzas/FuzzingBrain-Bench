# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=4.x
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential cmake pkg-config \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# The opencv issue (#28619) reports the bug exists on master/4.x; pin to
# the upstream 4.x branch tip at this clone time.
RUN git clone --depth 1 --branch 4.x https://github.com/opencv/opencv /src/opencv

COPY harness/ /src/harness/
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
