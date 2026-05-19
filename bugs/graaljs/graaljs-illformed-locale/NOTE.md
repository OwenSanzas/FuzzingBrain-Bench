# graaljs-illformed-locale — partial build infra, deferred

Bug fully built and runs:
- Dockerfile uses Maven Central `org.graalvm.polyglot:js-community:24.1.1`
  (the release line that corresponds to oracle/graaljs commit c40b7076,
  the bench-corpus vuln_commit) — sidesteps GraalVM's `mx` toolchain.
- `IntlFuzzer.java` adapted from upstream issue #985 to take byte[] so
  PocRunner can drive it without Jazzer.
- ~50 MB of transitive deps (truffle, regex, js-language, icu4j) but
  builds in 35s.

The blocker is **finding a trigger**: 24.1.1 is the patched line on
Maven Central. The fix for issue #985 (oracle/graaljs PR js/3678) was
backported, so all Maven-published 24.1.x releases reject the
malformed locale path that originally surfaced as InternalError.
Tried ~25 candidate locale strings (private-use tags, repeated
extensions, length 4+ subtags, underscore confusion, etc.) — all
returned cleanly.

Promotion path:
- pin to an older Maven Central release (23.0.x or 22.x) which
  predates the backport, OR
- mvn-pull the `js-community` jar from the actual c40b7076 commit
  range (Sonatype OSS staging may have it for that line) and verify
  the InternalError path is reachable, OR
- build oracle/graaljs from source via `mx` (out of v1 scope).
