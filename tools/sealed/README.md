# Sealed challenges — public challenge images + remote grading

Run FB-Bench publicly without ever shipping the answer key. Each bug splits into a
**public, answer-free challenge image** and a **private remote grading oracle**.
Users get only verdicts — never the PoC, the expected class/site, the fix, or the
ground-truth binaries.

## Architecture

```
  ┌─ challenge image (public, ghcr) ──────────┐        ┌─ grade server (private) ─┐
  │  src@vuln_commit + harness + bench.yaml*   │        │  binaries + expected.yaml │
  │  + mcp-server client                       │        │  + poc  (the answer key)  │
  │  agent reads source, crafts an input,      │        │                           │
  │  calls run_poc_on_harness() ───────────────────────────┼─ POST ─┤  runs the harness oracle, │
  │                          ◄─── verdict ─────┼────────┤  returns ONLY the verdict │
  └────────────────────────────────────────────┘        └───────────────────────────┘
        * bench.yaml is scrubbed: no fix_commit / fix_patch
```

The agent never runs a binary and never sees an answer file. The only thing that
crosses the wire is the candidate input (out) and the capability verdict (back).

## Build (operator)

```bash
# one bug
python tools/sealed/build_challenge.py <bug_id> --grade-url https://grade.example/ 
# whole corpus + assemble the private oracle bundles
python tools/sealed/build_all.py --grade-url https://grade.example/
```
`build_challenge.py` runs a **leak audit** before every build and refuses to build
if any `poc/ grader/ binaries/ expected.yaml` or `fix_commit`/`fix_patch` would land
in the image (upstream `src/` is exempt — public OSS may carry `*.bin` fixtures).

## Run the grading oracle (private infra)

```bash
# oracle-root/<bug>/ holds each bug's answer bundle (binaries + expected.yaml + poc)
mcp-server -grade-server :8077 -oracle-root tools/sealed/oracle-root
# POST /run_poc_on_harness?bug=<id> with the candidate bytes -> JSON capability verdict
```
The oracle root and its bundles are **gitignored** — they never enter the public repo.

## Use a challenge (end user)

Public images live on Docker Hub (anonymous pull, answer-free). The full list of
challenge ids is in [CHALLENGES.md](CHALLENGES.md).

```bash
docker pull docker.io/osanzas/fbbench-challenge-dtc-01:latest      # answer-free
docker run -it docker.io/osanzas/fbbench-challenge-dtc-01:latest
# inside /challenge: read src/ + harness/, craft an input, and your agent drives
# the mcp-server over stdio (setup/read/list/write/exec/run_poc_on_harness). run_poc_on_harness() POSTs the
# candidate to BENCH_GRADE_URL (the remote oracle) and returns ONLY the capability
# verdict {reach,crash,differential,class,site} — no answer key is ever on this host.
```

The image names use neutral `<project>-NN` aliases on purpose: the registry name
must not reveal what the bug is. Discovering the class and location is the task.

### Run end-to-end with an LLM agent (canonical path)

`fb-bench run` is the one execution path for **everyone, including the maintainers**:
it pulls the public challenge image, drives the agent loop through the image's own
`mcp-server` over `docker run -i … mcp-server`, and grades via the remote oracle baked
into the image. What we measure is byte-identical to what any external user runs, so
reported scores are reproducible — there is no separate "local" eval that could diverge.

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY / GEMINI_API_KEY
./fb-bench run dtc-01 --model claude-opus-4-7 --max-turns 300
#  -> pulls docker.io/osanzas/fbbench-challenge-dtc-01:latest (answer-free)
#  -> agent reads src/+harness, crafts inputs, run_poc_on_harness() hits the remote oracle
#  -> writes runs/<exp>/<bug>/<model>/run-N/{score.json,episode.jsonl,traj.md}
```

Requirements for the canonical path: **Docker + Python (>= 3.10)** and a provider
API key. No Go toolchain and no host mcp-server are needed — the mcp-server is
baked into the challenge image and grading is a call to the remote oracle.

## Verify

```bash
python tools/sealed/verify_sealed.py --grade-url http://localhost:8077
#  wire   : the bug's real PoC, POSTed to the oracle, fires its full K_b
#  leak   : `docker run` the image and assert no answer file is present
```

## Publish images

```bash
# live registry: Docker Hub (public-by-default; anonymous pull, answer-free)
python tools/sealed/push_all.py --registry docker.io --owner osanzas
```

## Scope note — the git repo itself

Both the *distribution* (images) and the *public repository* are now answer-free.
The git history was rewritten so `origin/main` carries **zero** `poc/ grader/ binaries/`
or `expected.yaml`/`fix_commit` paths (verify: `git ls-tree -r origin/main --name-only
| grep -E '/(poc|grader|binaries)/|expected\.yaml$'` returns nothing). The answers
live only in the private oracle bundles on the grade host (`oracle-root/`, gitignored).
A maintainer's local working tree may still hold the answer dirs for rebuilds — they
are gitignored and never re-enter the repo. See PLAN.md for the rewrite procedure.
