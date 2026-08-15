# FuzzingBrain Bench

**A benchmark for LLM-driven vulnerability reproduction on 77 real zero-day
bugs across 43 open-source projects (C / C++ / Java).**

Each challenge gives the agent only the **fuzz harness** (the target) and the
project source at the vulnerable revision — no patch, no fix commit, no target
line. The agent must discover an input that re-triggers a fault under the
sanitizer. Every grade is **deterministic** (no LLM-as-judge) and happens
**in-image and offline**: the candidate runs through the official
sanitizer-instrumented harness baked into the challenge container, and the run
is scored by the distinct crashes the agent triggered. Nothing leaves the
machine and no service has to be up.

| Challenges | Projects | Languages | Grader |
|---|---|---|---|
| **77** end-to-end | **43** | C · C++ · Java | deterministic — in-image, offline |

Nothing in the images or this repository reveals what a bug is — challenges are
named by neutral alias (`<project>-NN`, e.g. `avro-03`), and the answer key
(PoC, expected fault, fixed build) is in neither: it stays with the maintainer.
**Browse all 77:** [`tools/sealed/CHALLENGES.md`](tools/sealed/CHALLENGES.md).

---

## Quick start

### 1. Setup

```bash
git clone https://github.com/fuzzingbrain/FuzzingBrain-Bench
cd FuzzingBrain-Bench

python3 -m venv .venv && source .venv/bin/activate   # recommended (and required on
                                                     # Debian/Ubuntu, PEP 668)
pip install -e .                              # needs Python ≥ 3.10 and Docker

# put your model key(s) in ./.env — auto-loaded on every run, no need to export
cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
DEEPSEEK_API_KEY=sk-...
EOF

fb-bench list                                 # the 77 challenges (by alias)
fb-bench models                               # supported models + which keys are loaded
```

(`./.env` is read automatically; a plain `export ANTHROPIC_API_KEY=...` also works.)

> Re-`source .venv/bin/activate` in each new shell. Or skip the venv with
> `pip install --break-system-packages -e .` (not recommended).

`fb-bench run` pulls the public challenge image, drives the agent loop on the
host (calling your model API), and grades every candidate **inside that image** —
no network, nothing to reach. Only Docker + your model key are required, and a
run scores the **distinct crashes** the agent found — a crash's identity is its
sanitizer fault type plus its top stack frames, so the same fault hit twenty
times counts once.

> The default `--arm api` needs nothing beyond the above. The `--arm codex` and
> `--arm claudecode` backends need extra **vendor CLIs — optional**, installed
> separately (never part of `pip install -e .`); see [§4](#4-agent-modes--same-run-pick-the-backend-with---arm).

### 2. Run one challenge with a model

```bash
# Claude family  (haiku is cheapest/fastest; swap in opus/sonnet for harder runs)
fb-bench run avro-03 --model claude-haiku-4-5

# GPT family
fb-bench run avro-03 --model gpt-5.5

# Gemini family
fb-bench run avro-03 --model gemini-3.1-pro-preview

# DeepSeek family  (OpenAI-compatible endpoint; needs DEEPSEEK_API_KEY)
fb-bench run avro-03 --model deepseek-v4-flash
```

Models: `claude-haiku-4-5` · `claude-sonnet-4-6` · `claude-opus-4-8` ·
`gpt-5.5` · `gpt-5.4` · `gpt-5` · `gemini-3.1-pro-preview` · `gemini-2.5-flash` ·
`deepseek-v4-pro` · `deepseek-v4-flash`
(any catalog id works via `--model`; see `fb-bench models`).

### 3. Run many — same command, one or many

`fb-bench run` takes one bug or many, one model or many. A single run is just a
matrix of size one, so there is no separate "sweep" command:

```bash
# recommended full run: one model over the whole corpus, named output, PoCs
# preserved (the default) for later inspection. The agent keeps hunting past its
# first crash unless you pass --stop-on-crash
fb-bench run all --model claude-haiku-4-5 --output run1 --max-turns 100

# the curated cross-model roster, all challenges, 4 cells in parallel
fb-bench run all --model default-lineup --output sweep1 --jobs 4

# a couple of bugs, 3 samples each
fb-bench run avro-03,jq-01 --model gpt-5.5 --samples 3 --output probe

# just re-print the leaderboard from an existing run
fb-bench run all --model claude-haiku-4-5 --output run1 --report-only
```

`<bugs>` is one alias, a comma list, or `all`; `--model` is one id, a comma list,
`default-lineup`, or `all`. Results land in `output/<name>/<bug>/<model>/seed-N/`
(`score.json`, `episode.jsonl`, `transcript.jsonl`, `cost.json`, distilled
`traj.md`); a leaderboard is printed at the end. `--output` takes a bare name
(nested under `output/`) or a path (used as-is). **Every run gets its own
folder**: omit `--output` and it lands in `output/run_<timestamp>`; name a folder
that already exists and a fresh run forks `<name>_<timestamp>` rather than
resuming into it — so two runs never share results (`--report-only` is the one
reader, opening a folder in place).

### 4. Agent modes — same `run`, pick the backend with `--arm`

The three agent backends share **one entry**. `--arm` selects which one drives
the challenge; everything else (`<bugs>`, `--jobs`, `--samples`, `--output`,
the per-run folder, the leaderboard) is identical across arms.

```bash
fb-bench run avro-03 --model gpt-5.5            # --arm api (default): provider model
fb-bench run avro-03 --arm codex               # OpenAI codex CLI (default gpt-5.5)
fb-bench run avro-03 --arm claudecode --model sonnet --auth sub   # Claude Code CLI
fb-bench run all     --arm codex --jobs 4      # whole corpus, batched
```

- **`--arm codex`** drives OpenAI's `codex exec` over the bench MCP server.
  `--model` sets the codex model (default `gpt-5.5`), pinned via its config.toml.
- **`--arm claudecode`** drives the Claude Code CLI. `--model` picks the claude
  model (`sonnet`/`opus`/`haiku`).

Both vendor arms take **`--auth {api,sub}`**: `api` = the provider API key
(`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, pay-go, no throttle), `sub` = a
subscription sign-in (codex: a ChatGPT **Plus/Pro/Business/Edu/Enterprise** plan;
claudecode: claude.ai OAuth). Default is **auto** — prefer `api` when the API key
is present, else fall back to `sub`.

#### Optional — install the vendor CLI for the arm you use

These are **optional extras** and are **not** installed by `pip install -e .`.
The default `--arm api` never needs them. Install only the CLI whose arm you plan
to run (both need Node):

```bash
# --arm codex → OpenAI Codex CLI. Authenticate once, matching the --auth you use:
npm install -g @openai/codex
#   --auth api (default when OPENAI_API_KEY is set):
printenv OPENAI_API_KEY | codex login --with-api-key
#   --auth sub (needs a ChatGPT Plus/Pro/Business/Edu/Enterprise plan; a free
#   ChatGPT account can't use the codex models):
codex login                                  # sign in with your ChatGPT plan

# --arm claudecode → Claude Code CLI.
npm install -g @anthropic-ai/claude-code
#   --auth api (default when ANTHROPIC_API_KEY is set): nothing to do
#   --auth sub: one-time claude.ai OAuth login
claude
```

---

## The task is always blind

The agent gets the fuzz harness and the project source at the vulnerable
revision — no description, no patch, no fix commit, no target line. It must find
a crashing input cold. The turn budget is **100** and the per-episode wall clock
is **1800 s**; an episode does not stop at its first crash but keeps hunting for
more distinct ones until one of those budgets runs out.

The sanitizer the build is judged under, and a description of that sanitizer's
general fault family, ARE disclosed — a real auditor always knows them from
their own build. The specific crash class is never stated, because that is the
capability under test.

## What a run scores

**Distinct crashes, weighted by difficulty.** A crash's identity is its
sanitizer fault type plus its top three application frames, so the same fault
reached twenty times counts once, and repeats across a challenge's samples
collapse into one.

**A crash has to reproduce.** Every candidate is run **3 times** inside the
image and counts only if it faults in all three *and* every round lands in the
same place. One execution cannot separate a real defect from a race, an
ASLR-dependent overflow or an allocator coincidence. An input that faults in
only some rounds comes back `flaky_rounds`; one that faults every round but
somewhere different each time comes back `flaky_location`. Neither scores, and
`run_poc_on_harness` reports `crashed_rounds` / `total_rounds` /
`distinct_crashes` so the agent can see why.

Each challenge carries a difficulty coefficient **D (1–5)** from a **frozen**
table (`fbbench/report/difficulty.json`), measured once from a fixed 3-model
panel. D is read off two facts: how much of the panel crashed the challenge at
all, and how freely it gave crashes up to whoever did.

```
D5   nobody crashed it
D4   at most half the panel got in, and nobody got more than 2
D3   anything else
D2   at least half the panel got in, and somebody got 3 or more
D1   every model crashed it at least once
```

A model's score is `min(crashes, 3) × D` summed over the challenges it ran. The
cap keeps one challenge that yields eight signatures for a single underlying
defect from drowning out the rest. **The denominator is run-scoped**: a 7-challenge
run is scored out of those 7, so a partial sweep still reports a real fraction —
but two runs over different challenge sets are not comparable, and the summary
page says so when the models in one sweep covered different sets.

The table is frozen on purpose. A run must not derive the scale it is then
scored on, and recomputing it silently would move every historical score. A
challenge added after the freeze has no coefficient and is reported as unscored
rather than scored zero.

Deciding whether a crash is *the* defect a challenge was built around needs an
answer key — the PoC, the documented fault, a build at the fix commit — and no
image ships one. So a run can tell you an input crashed, and whether that crash
is one it had not produced before, but not that it crashed the *right* way.

## Other parameters

```bash
fb-bench run <bugs> \
    --model gpt-5.5 \         # one id, comma list, default-lineup, or all
    --max-turns 100 \         # turn budget per episode
    --timeout 1800 \          # per-episode wall-clock seconds
    --jobs 4 \                # run N cells in parallel
    --samples 3 \             # repeat each (model, bug) N times
    --output my-experiment \  # results under output/my-experiment/ (name or path)
    --no-preserve-pocs \      # graded blobs are KEPT by default; pass this to drop them
    --stop-on-crash           # end at the first crash; off by default, so an
                              # episode keeps hunting for more distinct crashes
```

Grade a hand-crafted or external (AFL++ / libFuzzer / honggfuzz) PoC without any
LLM — the grader is vendor-neutral:

```bash
fb-bench grade <alias> my-input.bin        # -v for the evidence
```

---

## How it works (sealed challenges)

Every challenge is a public, **answer-free** Docker image. The agent talks to it
over an MCP server (`setup` / `exec` / `run_poc_on_harness`);
`run_poc_on_harness()` runs the candidate through the sanitizer harness and
returns only what the harness printed plus whether that crash is one this episode
has already produced — never an answer key.

```
docker.io/osanzas/fbbench-challenge-<alias>:latest      # one image per challenge
```

One image, one tag, and it judges itself. It carries the sanitizer-instrumented
harness built from the source it already ships, the crash-signature rules, and a
pre-built mcp-server that can grade, so a run needs no network at all. What it
does not carry is any answer: no reference PoC, no expected fault, no build at the
fix commit, nothing that says where the defect is — the harness is compiled from
source the image publishes anyway, so the image is worth no more to someone
reading it than that source already is. The seal architecture and the answer-free
verifier live in [`tools/sealed/`](tools/sealed/) — anyone can audit that no
answer key ships with an image:

```bash
python tools/sealed/verify_sealed.py --only avro-03
```

## What's in this repo

```
bugs/<project>/<alias>/   one challenge: fuzz harness + neutral metadata
                          (project, language, sanitizer, harness interface)
fbbench/                  the CLI + run engine + codex / claude-code arms
tools/sealed/             challenge index + answer-free image verifier
```

The answer artifacts (PoC inputs, expected-fault keys, the build at the fix
commit) are **not** in this repository and not in the images either — they stay
with the maintainer. Which is why a run can tell you that an input crashed, and
whether that crash is one it had not produced before, but not that it crashed the
*right* way.

## License

MIT. See `LICENSE`.
