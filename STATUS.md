# FuzzingBrain Bench — Status

Snapshot of where the v1 benchmark stands.

## Phases

| Phase | What | Status |
|-------|------|--------|
| 1 | SPEC + bug corpus | done — see [docs/SPEC.md](docs/SPEC.md), [docs/bench-corpus.json](docs/bench-corpus.json) |
| 2 | Per-bug builds (Dockerfile + binaries + harness + grader) | **14 / 39 shipped**, 25 deferred — see below |
| 3 | MCP server (Go, 6 tools) | done — [tools/mcp-server/](tools/mcp-server/) |
| 3 | Python runner | done — [runner/](runner/) |
| 4 | Site integration | done — [docs/benchmark.html](docs/benchmark.html) |

## Phase 2 — shipped bugs

End-to-end (bench.yaml, description.txt, grader/expected.yaml, harness/build.sh,
Dockerfile, 4 prebuilt binaries, poc/poc.bin):

1. `dtc-fdt32-misalign` — reference build pattern
2. `mongoose-mg-match-overflow` — heap overflow in `mg_match`
3. `openldap-ldif-stack-underflow` — stack underflow in LDIF parser
4. `openldap-parse-whsp` — leak in attribute-list parser
5. `jq-dump-op-npd` — null deref on op dump
6. `ots-processgeneric-npd` — null deref in OpenType sanitizer
7. `simdutf-utf16-utf8-overflow` — heap overflow in UTF16→UTF8 path
8. `ndpi-hex-decode-sscanf` — sscanf state-dependent crash
9. `netsnmp-vacm-parse-npd` — null deref in `vacm_parse_config_group`
10. `avro-neg-block-size` — null deref in Avro C file_read_header path
11. `avro-neg-string-len` — null deref in Avro C union schema parser
12. `icu-translit-rule-uaf` — leak in ICU transliterator parseFilterID
13. `binutils-rust-demangle-oom` — UBSan null ptr arithmetic in libiberty rust-demangle
14. `ghidra-rust-demangle-oom` — sibling bug in Ghidra's bundled libiberty (curl-build trick)

All fourteen grade cleanly against the MCP server (3-round unanimity).

## Phase 2 — deferred bugs

The remaining 25 v1 bugs are listed with `phase2_status: deferred` in
[docs/bench-corpus.json](docs/bench-corpus.json). They cluster into:

- **Java targets** (graaljs, json-java, pdfbox, ghidra) — need a Jazzer
  build chain; deferred to v1.1 alongside the JaCoCo reach adapter.
- **upx × 4** — meson + non-trivial harness extraction.
- **fwupd × 4** — needs gnutls/glib2/libxmlb stack pinned to the vuln
  commit; long build.
- **freerdp, libavif, opencv, harfbuzz, icu, openssl, binutils,
  imagemagick × 2, avro × 3, libavif-jni** — buildable but lower
  priority than landing the MCP/runner contract.

The shipped 12 cover four sanitizer classes (null-deref, heap-overflow,
leak, stack-underflow), four build systems (autoconf, amalgam, cmake,
meson), and three languages (C, C++, JS-via-C-amalgam) — enough surface
area for the runner contract to be stress-tested end-to-end before
scaling out the corpus.

## What's verified

- MCP server speaks JSON-RPC 2.0 over stdio; all 6 tools work.
- `read_file` denies `grader/expected.yaml` and `grader/buggy_region.json`.
- `write_file` rejects writes outside `BENCH_WORKSPACE`.
- `grade()` drives all 4 oracles against `netsnmp-vacm-parse-npd`:
  - `crash` fires via UBSan SUMMARY trailer detection
  - `class` fires via UBSan → null-deref mapping
  - `site` fires via suffix-match on `/src/build-asan/snmplib/vacm.c:414`
  - `reach` fires via sanitizer-backtrace fallback (coverage build
    segfaults before profile flush — the user's
    "如果llvm-cov不行，可以试试gdb" hint at work)
- Three-round unanimity holds across all rounds.
- Runner CLI parses and imports cleanly. Live API loop not exercised
  here to avoid burning credits — see [runner/README.md](runner/README.md).

## Next

- Extend Phase 2 to the deferred bugs as time permits; current 9 are
  sufficient to publish a v1 numbers post.
- Wire up live (model, bug, seed) runs and publish a leaderboard.
- v2: adaptive coaching arm + vendor-CLI arm (out of v1 scope per SPEC §6.2).
