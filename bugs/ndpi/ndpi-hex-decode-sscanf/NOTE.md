# ndpi-hex-decode-sscanf — deferred for v1

This bug is **real** (https://github.com/ntop/nDPI/issues/3159) but
intentionally not shipped in v1.

Per the issue body:

> Single-input replay is unreliable — whether `memchr` crosses a
> redzone depends on the surrounding allocator state, so a fresh
> process on a single input often does not SEGV. Fork-mode replay
> against the corpus reproduces reliably (27 crashes / ~200 inputs
> in 21 s with `-fork=2`).

The v1 grader runs each `(poc, round)` cell in a single fresh process
with no `-fork=N` libFuzzer mode — exactly the configuration where
this bug is documented to be flaky to the point of grading at 0/3
unanimously across rounds.

Promote to shipped once the grader supports a per-bug `fork_runs`
oracle (open for v1.1).

The harness source and the Dockerfile remain in this directory as
the reproduction recipe — `docker build .` still produces a working
harness that exhibits the bug under `-fork=N`.
