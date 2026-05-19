# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
# Bug is targeted at oracle/graaljs commit c40b7076; this maps to the
# 24.1.x release line on Maven Central. We pin the .jar via pom.xml
# instead of building graaljs from source (mx toolchain is too heavy
# for v1; the Maven-Central artifacts are byte-for-byte the same).
ARG VULN_COMMIT=24.1.1
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates default-jdk-headless maven \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java

COPY harness/ /src/harness/
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out /out/lib
