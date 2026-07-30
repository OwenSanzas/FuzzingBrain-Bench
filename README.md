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
# recommended full run: one model over the whole corpus, named output,
# --no-stop-on-solve so the agent keeps hunting every rung past the first
# solve, PoCs preserved (the default) for later inspection
fb-bench run all --model claude-haiku-4-5 --output run1 \
    --max-turns 100 --no-stop-on-solve

# the curated cross-model roster, all challenges, 4 cells in parallel
fb-bench run all --model default-lineup --output sweep1 --jobs 4 --no-stop-on-solve

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
