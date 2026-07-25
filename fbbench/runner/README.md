# FuzzingBrain Bench runner

Drives one episode = one `(model, bug)` cell. It `docker run`s the public
challenge image (the mcp-server is baked in) and talks to it over stdio; uses
provider SDKs (Anthropic / OpenAI / Google) for the model loop.

> **You probably want `./fb-bench run <bugs>`** — it wraps this module, picks a
> default model from your `.env`, and handles the output layout. This README is
> for calling the runner directly, one cell at a time.

## Setup

```bash
pip install -e .                                           # needs Python >= 3.10 + Docker
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env                 # or OPENAI_/GEMINI_
```

## Run one episode

```bash
python -m fbbench.runner \
  --bug net-snmp-02 \
  --model claude-opus-4-7 \
  --max-turns 60 \
  --out-dir output/my-experiment/
```

Or the legacy nesting (`<output>/<bug>/<model>/`) — used by sweep scripts:

```bash
python -m fbbench.runner --bug X --model Y --output output/
# → output/X/Y/
```

Each run produces:

- `episode.jsonl` — turn-by-turn trace (assistant text + tool calls + tool results)
- `score.json` — final capability bitmap + tier score (0..4)
- `cost.json` — input/output token usage + USD estimate

Every blob the model graded is kept by default, bucketed by whether it
satisfied `K_b` (pass `--no-preserve-pocs` to drop them):

```
<out-dir>/pocs/
  ├─ solved/
  │   ├─ blob-001-turn03.bin   (the actual input bytes)
  │   └─ blob-001-turn03.json  (turn, fired flags, k_b, agreed)
  └─ failed/
      ├─ blob-002-turn05.bin
      └─ blob-002-turn05.json
```

## What the runner does NOT do (v1)

- No multi-process parallelism across bugs (use `python -m fbbench.sweep.orchestrator`
  or drive multiple `./fb-bench run` calls with `xargs -P`).
- No coaching / Stuck-nudges (v2 adaptive arm).
- No vendor-CLI shim (see the Codex CLI arm in `python -m fbbench.sweep.codex`).
- No Docker isolation — the MCP server is a host subprocess. This means
  agent `exec` commands run as the runner's UID. Run on a throwaway VM.

## On sampling determinism

Temperature is fixed at 1.0; the runner has **no `--seed`** because no
provider's API call currently wires one. Re-running the same `(model,
bug)` produces independently sampled trajectories. To collect multiple
samples per cell, use `python -m fbbench.sweep.orchestrator --samples 3` (N
repeats) — each sample gets its own directory (`output/<bug>/<model>/seed-N/`,
`N` running 0..samples-1, kept named `seed-N` for back-compat with the
518-row legacy dataset).
