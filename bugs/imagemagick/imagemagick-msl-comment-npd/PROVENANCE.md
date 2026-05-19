# Provenance

**Bug**: NULL pointer passed to DeleteImageProperty from MSLEndElement
**Upstream**: https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-5vx3-wx4q-6cj8
**Vuln commit**: cbc802d38b2d37a4ae101c6ef80a465678cc6a49

## Harness

`harness/msl_fuzzer.cc` is the OSS-Fuzz MSL libFuzzer harness pasted in
the advisory body. `harness/utils.cc` is a minimal reimplementation
of the OSS-Fuzz helper that exports `IsInvalidSize()` — the upstream
oss-fuzz copy wasn't pasted in the advisory.

## Triggering input

`poc/poc.bin` is 61 bytes:
`<?xml version="1.0"?><group><image><comment/></image></group>`

The `<comment/>` element inside `<image>` makes MSLEndElement call
`DeleteImageProperty(image, "comment")` with `image == NULL`, hitting
the assertion at MagickCore/property.c:297 → SIGABRT.

## Build

`./configure --disable-shared --enable-static --without-modules` plus
a long list of `--without-<delegate>` flags to skip every codec. Must
keep `--with-xml` (libxml2) because the MSL coder is xml-driven.
`-DMAGICKCORE_QUANTUM_DEPTH=16 -DMAGICKCORE_HDRI_ENABLE=1` defines
are required at harness compile time because the public headers
otherwise refuse to build.

## K_b

`capability_set: [reach, crash, site]` (no `class`). The crash is an
`assert()` abort, not a sanitizer-classified failure — per SPEC §2.2.2,
an assertion-driven abort earns `crash` but cannot earn `class`
because the SPEC vocabulary has no "abort" entry. The real bug class
is null-deref; the abort fires because the asserter detected it
before any sanitizer instrumentation could.
