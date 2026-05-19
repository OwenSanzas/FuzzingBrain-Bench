# Provenance

**Bug**: Null pointer passed to memcpy (nonnull attribute) in str_buf_append()
**Upstream**: https://github.com/NationalSecurityAgency/ghidra/security/advisories/GHSA-m94m-fqr3-x442
**Vuln commit**: caeeac88ddb5b004736de440db86380ff6e61229

## Build trick

Ghidra is multi-GB. We only need 5 files from its bundled libiberty at
`GPL/DemanglerGnu/src/demangler_gnu_v2_41/`. The Dockerfile `curl`s them
directly via `raw.githubusercontent.com` at the vuln SHA — no clone.
Sources live under `c/`, headers under `headers/`.

## Triggering input

`poc/poc.bin` is 16 bytes (`_RYYRYYARAAARAC0`). v0 Rust prefix `_R`
takes the demangler into the path that hits the null-pointer-to-memcpy
in `str_buf_append`. libFuzzer ~30s from empty corpus.

## Notes

Sibling of `binutils-rust-demangle-oom`. The two ship the same root
upstream (libiberty/rust-demangle.c) at different commits — the
binutils copy fires UBSan "applying zero offset to null pointer" at
line 1572; the Ghidra-bundled copy fires "null pointer passed as
argument 2" at line 1576. Same demangler, slightly different vendored
versions, both null-deref class.
