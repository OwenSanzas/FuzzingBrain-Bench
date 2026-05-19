# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=4672850ee7eb44c3356499cd44254bdf5b6eb9eb
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates default-jdk-headless curl tar \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java

# pdfbox enforces Maven 3.9+; Debian bookworm ships 3.8.7. Pull the tarball.
RUN curl -fsSL https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.tar.gz \
    -o /tmp/mvn.tgz \
 && tar -C /opt -xzf /tmp/mvn.tgz \
 && ln -sf /opt/apache-maven-3.9.9/bin/mvn /usr/local/bin/mvn \
 && rm /tmp/mvn.tgz

RUN git clone --filter=blob:none https://github.com/apache/pdfbox /src/pdfbox \
 && git -C /src/pdfbox checkout ${VULN_COMMIT}

COPY harness/ /src/harness/
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out /out/lib /out/release-asan/harness
