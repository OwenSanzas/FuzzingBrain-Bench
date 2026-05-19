# Provenance

**Bug**: Memory leak in TransliteratorIDParser::parseFilterID()
**Upstream**: https://unicode-org.atlassian.net/browse/ICU-23365
**Vuln commit**: 8be6575f5c4ea397d384493a435b2932d5c2eaca

## Harness

`harness/translit_fuzzer.cpp` is a libFuzzer wrapper around the
upstream standalone reproducer (repro.cpp). Input layout:

    data[0]      direction bit (0 = REVERSE, 1 = FORWARD)
    data[1..]    UTF-16 rules string

Calls `icu::Transliterator::createFromRules("test", rules, dir, pe, st)`,
runs a sample `transliterate("Hello")` on success, deletes the
transliterator.

## Triggering input

`poc/poc.bin` is 15 bytes — discovered by libFuzzer in ~30s. The leak
is reproducible and stable across rounds.

## Notes

The upstream ticket headlines a UAF in TransliterationRule's
destructor. This harness consistently triggers a *leak* in
`TransliteratorIDParser::parseFilterID()` (tridpars.cpp:229) on its
recorded PoC — same library, related transliterator-rules path, but
a different precise site than the ticket title. The leak is real and
load-bearing for the bug class; expected.yaml is set against the
observed site so the oracle is deterministic.

`capability_set` excludes `crash` per SPEC §2.3 (memory-leak bugs
report at exit time, not at a crashing site).
