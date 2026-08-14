# Sealed challenges — public, answer-free challenge images

Run FB-Bench publicly without ever shipping the answer key. Each bug becomes one
**public, answer-free challenge image** that grades itself. Users get the harness
output and a crash signature — never the PoC, the expected class/site, the fix, or
the ground-truth binaries.

## Architecture

```
  ┌─ challenge image (public, docker.io) ─────────────────────────┐
  │  src@vuln_commit + harness + bench.yaml*  (the agent reads these)
  │                                                               │
  │  /opt/fbbench/oracle/  root-owned 0700, unreadable by exec()  │
  │    binaries/vuln/asan/harness   the instrumented harness      │
  │    oracle.yaml                  timeout / leak-detection      │
  │                                                               │
  │  agent crafts an input, calls run_poc_on_harness() ──┐        │
  │                     ◄── harness output + novelty ────┘        │
  └───────────────────────────────────────────────────────────────┘
        * bench.yaml is scrubbed: no fix_commit / fix_patch / capability_set
```

Nothing crosses a network. The agent never runs the graded binary itself and never
sees an answer file: `exec()` drops to an unprivileged uid, and the oracle
directory is root-owned 0700, so the only route to the harness is the tool — which
returns what the harness printed plus whether that crash is one this episode has
already produced.

The harness is compiled from source the image already publishes, so an image is
worth no more to someone reading it than that source already is. What it does not
carry: no `expected.yaml`, no reference PoC, no coverage build, no build at the fix
commit. Nothing in it says where the defect is.

## Build (operator)

```bash
# one bug
python tools/sealed/build_challenge.py <bug_id>
# whole corpus
python tools/sealed/build_all.py
```
`build_challenge.py` runs a **leak audit** before every build and refuses to build
if any `poc/ grader/ binaries/ expected.yaml` or `fix_commit`/`fix_patch` would land
in the image (upstream `src/` is exempt — public OSS may carry `*.bin` fixtures).

## Use a challenge (end user)

```bash
docker pull docker.io/osanzas/fbbench-challenge-<alias>:latest
docker run -i --security-opt seccomp=unconfined \
    docker.io/osanzas/fbbench-challenge-<alias>:latest mcp-server
```
The image speaks the stdio MCP protocol with everything baked in: `setup`, `exec`,
`run_poc_on_harness`. That is the single canonical runtime — identical for the
maintainer and for any external user — and it needs no network at all.

`fb-bench run <alias>` drives exactly this, adding only the model API call on the
host. `fb-bench grade <alias> <file>` drives it with no model at all: it stages
your bytes into the container's workspace and runs them through the same harness.

## Audit (anyone)

```bash
python tools/sealed/verify_sealed.py docker.io/osanzas/fbbench-challenge-avro-03:latest
```
Confirms an image ships no answer key, and that grading does not depend on
anything outside it.

The answer artifacts — reference PoCs, expected faults, builds at the fix commit —
live only with the maintainer and are in neither the images nor this repository.
