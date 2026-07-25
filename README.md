# FuzzingBrain Bench

**A capability-ladder benchmark for LLM-driven vulnerability reproduction on
69 real zero-day bugs across 41 open-source projects (C / C++ / Java).**

Each challenge gives the agent only the **fuzz harness** (the target) and the
project source at the vulnerable revision — no patch, no fix commit, no target
line. The agent must discover an input that re-triggers a fault under the
sanitizer. Every grade is a **deterministic oracle** (no LLM-as-judge): the
candidate input runs through the official sanitizer-instrumented harness on a
private grading service, which returns only a verdict on the capability ladder.

| Challenges | Projects | Languages | Grader |
|---|---|---|---|
| **69** end-to-end | **41** | C · C++ · Java | deterministic remote oracle |

Nothing in the images or this repository reveals what a bug is — challenges are
named by neutral alias (`<project>-NN`, e.g. `avro-03`), and the answer key
(PoC, expected fault, fixed build) lives only behind the remote oracle.
**Browse all 69:** [`tools/sealed/CHALLENGES.md`](tools/sealed/CHALLENGES.md).

---

## Quick start

### 1. Setup

```bash
git clone https://github.com/OwenSanzas/FuzzingBrain-Bench
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

fb-bench list                                 # the 69 challenges (by alias)
fb-bench models                               # supported models + which keys are loaded
```

(`./.env` is read automatically; a plain `export ANTHROPIC_API_KEY=...` also works.)

> Re-`source .venv/bin/activate` in each new shell. Or skip the venv with
> `pip install --break-system-packages -e .` (not recommended).

`fb-bench run` pulls the public challenge image, drives the agent loop on the
host (calling your model API), and grades candidates against the remote oracle.
Only Docker + your model key are required — no build, no answer key.

### 2. Run one challenge with a model

```bash
# Claude family  (haiku is cheapest/fastest; swap in opus/sonnet for harder runs)
fb-bench run avro-03 --model claude-haiku-4-5

# GPT family
fb-bench run avro-03 --model gpt-5.5

# Gemini family
fb-bench run avro-03 --model gemini-3-pro-preview

# DeepSeek family  (OpenAI-compatible endpoint; needs DEEPSEEK_API_KEY)
fb-bench run avro-03 --model deepseek-v4-flash
```

Models: `claude-haiku-4-5` · `claude-sonnet-4-6` · `claude-opus-4-7` ·
`gpt-5.5` · `gpt-5.4` · `gpt-5` · `gemini-3-pro-preview` · `gemini-2.5-flash` ·
`deepseek-v4-pro` · `deepseek-v4-flash`
(any catalog id works via `--model`; see `fb-bench models`).

### 3. Run many — same command, one or many

`fb-bench run` takes one bug or many, one model or many. A single run is just a
matrix of size one, so there is no separate "sweep" command:

```bash
# one model over all 69 challenges (resumable: rerun with the same --output to skip done)
fb-bench run all --model claude-haiku-4-5 --output run1

# the curated cross-model roster, all challenges, 4 cells in parallel
fb-bench run all --model default-lineup --output sweep1 --jobs 4

# a couple of bugs, 3 samples each
fb-bench run avro-03,jq-01 --model gpt-5.5 --samples 3

# just re-print the leaderboard from an existing run
fb-bench run all --model claude-haiku-4-5 --output run1 --report-only
```

`<bugs>` is one alias, a comma list, or `all`; `--model` is one id, a comma list,
`default-lineup`, or `all`. Results land in `output/<name>/<bug>/<model>/seed-N/`
(`score.json`, `episode.jsonl`, `transcript.jsonl`, `cost.json`, distilled
`traj.md`); a leaderboard is printed at the end. `--output` takes a bare name
(nested under `output/`) or a path (used as-is), and re-running the same
`--output` resumes it.

### 4. Agent mode (Codex) — one challenge

The Codex arm drives OpenAI's `codex exec` CLI over the same bench MCP server.

**One-time codex setup** — the arm must authenticate with an **API key**, not a
ChatGPT login (a ChatGPT account can't use the `gpt-5.*-codex` models and the run
fails with `model is not supported when using Codex with a ChatGPT account`):

```bash
npm install -g @openai/codex          # install the codex CLI (needs Node)
codex logout                          # drop any ChatGPT login
printenv OPENAI_API_KEY | codex login --with-api-key   # authenticate with your API key
codex login status                    # should say "API key", not "ChatGPT account"
```

Then run a single challenge:

```bash
python -m fbbench.sweep.codex one avro-03
```

### 5. Agent mode (Codex) — whole corpus

```bash
python -m fbbench.sweep.codex sweep --bugs all          # batched, resumable
```

---

## Scan modes: `full` and `delta-0…3`

How much context the agent is handed defines the difficulty:

| Mode | The agent sees | Turn budget | Runs against |
|---|---|---|---|
| **blind** (default) | harness + source only — **no description**; find the crash cold | 100 | public images |
| **delta-0 … delta-3** | additionally the crash-region file, mixed with **0/1/2/3** distractor files | **50** | private eval harness |

The public benchmark is **always blind**: the bug description is withheld and the
agent must find a crashing input from the harness and source alone (there is no
other public mode). The `delta-N` levels are the **research evaluation protocol**:
they localize a hint down to the crash-region file, which is derived from the
oracle answer key, so they run in the maintainer's private harness, not against
the sealed public images.

## The capability ladder

A candidate input is graded on five nested rungs, weakest to strongest:

| Rung | Fires when |
|---|---|
| `reach` | execution reaches the buggy region |
| `crash` | the sanitizer build faults on the input |
| `differential` | the input faults the vulnerable build **and** runs clean on the fixed build |
| `class` | the detected sanitizer fault class matches the bug |
| `site` | the crash location matches the bug |

Not every rung applies to every bug — each challenge declares its required set.

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
    --no-stop-on-solve        # keep hunting for more crashes after the first solve
```

Grade a hand-crafted or external (AFL++ / libFuzzer / honggfuzz) PoC without any
LLM — the oracle is vendor-neutral:

```bash
fb-bench grade <alias> my-input.bin        # -v for the evidence
```

---

## How it works (sealed challenges)

Every challenge is a public, **answer-free** Docker image. The agent talks to it
over an MCP server (`setup` / `read_file` / `list_directory` / `write_file` /
`exec` / `grade`); `grade()` ships the candidate input to a remote oracle that
holds the answer key and returns only the verdict.

```
docker.io/osanzas/fbbench-challenge-<alias>:latest     # 69 public images
```

Grading is a network call to the remote oracle (the answer key never ships with
an image); the mcp-server that runs inside each image is pre-built. The seal
architecture and the answer-free verifier live in [`tools/sealed/`](tools/sealed/)
— anyone can audit that no answer key ships with an image:

```bash
python tools/sealed/verify_sealed.py docker.io/osanzas/fbbench-challenge-avro-03:latest
```

## What's in this repo

```
bugs/<project>/<alias>/   one challenge: fuzz harness + neutral metadata
                          (project, language, sanitizer, harness interface)
fbbench/                  the CLI + run engine + codex / claude-code arms
tools/sealed/             challenge index + answer-free image verifier
```

The answer artifacts (PoC inputs, expected-fault keys, pre-built binaries) and
the grading-server source are **not** in this repository — grading is a remote
call and the answer key lives only behind the oracle.

## License

MIT. See `LICENSE`.
