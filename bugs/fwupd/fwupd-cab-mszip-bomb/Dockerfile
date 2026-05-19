# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
# bench-corpus has no specific vuln_commit; track main branch tip at the
# time of build. The MSZIP decompression bomb path lives in
# libfwupdplugin/fu-cab-firmware.c and has been present long before
# the issue was filed.
ARG VULN_COMMIT=2.0.18
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential pkg-config meson ninja-build python3 \
        libglib2.0-dev libjson-glib-dev libxmlb-dev libcurl4-openssl-dev \
        libgudev-1.0-dev libgusb-dev libpolkit-gobject-1-dev \
        libsqlite3-dev libarchive-dev libgnutls28-dev libsmbios-dev \
        libreadline-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${VULN_COMMIT} https://github.com/fwupd/fwupd /src/fwupd

COPY harness/ /src/harness/
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
