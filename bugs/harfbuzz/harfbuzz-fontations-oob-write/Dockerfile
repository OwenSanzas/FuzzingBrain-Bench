# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim
ARG VULN_COMMIT=edc8c18ef88262fc467a30ab74f05080d6aea6cf
ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates clang libclang-rt-14-dev \
        build-essential pkg-config meson ninja-build python3 \
        libclang-dev curl \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:${PATH}

# rustup with nightly: harfbuzz fontations meson invokes cargo with
# `-Z unstable-options --artifact-dir`, which still requires nightly even
# on cargo >= 1.85 because the meson wrapper passes -Z explicitly.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain nightly-2025-04-15 --profile minimal --no-modify-path \
 && rustup component add rustfmt

# bindgen is required by harfbuzz's fontations Rust crate
RUN cargo install --locked bindgen-cli --version 0.69.4 \
 && ln -s /usr/local/cargo/bin/bindgen /usr/local/bin/bindgen

RUN git clone --depth 1 https://github.com/harfbuzz/harfbuzz /src/harfbuzz \
 && git -C /src/harfbuzz fetch --depth 1 origin ${VULN_COMMIT} \
 && git -C /src/harfbuzz checkout ${VULN_COMMIT}

COPY harness/fontations_glyph_name.c /src/harness/fontations_glyph_name.c
COPY harness/build.sh                /src/harness/build.sh
RUN chmod +x /src/harness/build.sh

RUN /src/harness/build.sh build-libs
RUN mkdir -p /out \
 && /src/harness/build.sh harness debug \
 && /src/harness/build.sh harness debug-asan \
 && /src/harness/build.sh harness release-asan \
 && /src/harness/build.sh harness coverage

RUN ls -la /out/*/harness
