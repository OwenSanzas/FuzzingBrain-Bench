# FuzzingBrain Bench — v1 model results (pilot, 1 seed)

A first pass running mainstream frontier models across all 37 bugs through the
uniform 6-tool MCP scaffold. **Single seed, turn budget 40.** Numbers are a
capability snapshot, not a final ranking — see *Caveats* (single-seed variance
is large).

## Leaderboard

`solved` = every flag in the bug's `K_b` (per `bench.yaml`) fired, 3-round
unanimous. R/C/Cl/S = bugs where reach / crash / class / site fired. `refus` =
episodes the model refused (recorded distinctly from failure). `blobs` =
candidate PoCs tested (grade() calls). Cost is USD at May-2026 list prices.

| # | model | provider | solved | R | C | Cl | S | refus | blobs | cost $ |
|--:|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | gpt-5.5 | OpenAI | **35/37** | 23 | 33 | 34 | 29 | 0 | 79 | 24.56 |
| 2 | claude-sonnet-4-6 | Anthropic | 31/37 | 23 | 28 | 30 | 28 | 0 | 109 | 28.74 |
| 3 | gemini-3.5-flash | Google | 27/37 | 19 | 28 | 27 | 24 | 0 | 38 | 16.54 |
| 4 | gpt-5.4 | OpenAI | 24/37 | 16 | 25 | 24 | 22 | 0 | 198 | **5.18** |
| 4 | gpt-5 | OpenAI | 24/37 | 16 | 23 | 23 | 22 | 0 | 296 | 16.25 |
| 6 | claude-haiku-4-5 | Anthropic | 22/37 | 15 | 21 | 21 | 20 | 0 | 226 | 10.67 |
| 7 | claude-opus-4-7 | Anthropic | 20/37 | 14 | 19 | 19 | 17 | **15** | 38 | 27.24 |
| 8 | gpt-5.4-mini | OpenAI | 14/37 | 10 | 15 | 14 | 14 | 0 | 115 | 1.60 |
| 8 | gemini-2.5-flash | Google | 14/37 | 12 | 16 | 13 | 16 | 0 | 150 | 2.76 |
| 10 | gemini-2.5-flash-lite | Google | 8/37 | 6 | 10 | 7 | 8 | 0 | 213 | 0.79 |

Total spend for the 10-model sweep: **~$134**. The 3 Gemini *pro* models are
pending (run on a separate key).

## Method

- Same scaffold for every model: 6 MCP tools (`setup`, `exec`,
  `list_directory`, `read_file`, `write_file`, `grade`), identical system
  prompt, max 40 turns/episode. The agent gets a sandboxed bug view
  (`description.txt`, `bench.yaml`, `harness/`) — no oracle key, no reference
  PoC, no prebuilt binary.
- Grading is the deterministic oracle: `grade()` runs the ground-truth
  sanitizer-instrumented harness over 3 randomized rounds and credits a flag
  only on unanimous fire. No LLM-as-judge.
- A bug counts as solved only when **all** flags in its declared `K_b` fire.

## Key findings

1. **Wide capability spread, cleanly separated.** 8/37 → 14 → 22 → 24 → 27 →
   31 → 35. The benchmark discriminates: cheap flash models reach a handful,
   gpt-5.5 nearly sweeps.

2. **Opus is sandbagged by its own refusals, not capability.**
   `claude-opus-4-7` refused 15/37 episodes (~40%) of this *authorized,
   already-disclosed-bug reproduction* task, scoring 20/37. Same-family
   `claude-sonnet-4-6` refused 0 and scored 31/37. Recording
   `terminated_reason=refusal` separately is what makes "won't" distinguishable
   from "can't" — without it opus would look incapable rather than unwilling.

3. **Blob efficiency varies ~10×.** gpt-5.5 solved 35 with only 79 PoCs tested
   (2.3 per solve); gpt-5 brute-forced 296 PoCs for 24 solves (12 per solve).
   More tries ≠ better. (Note: PoCs are created via `write_file` *or* `exec`
   `printf >`, so grade() count is the true blob proxy, not write_file count.)

4. **Two genuinely hard bugs.** `icu-translit-rule-uaf` (memory-leak, fires
   only at exit via LeakSanitizer) was solved by **no model**;
   `ndpi-hex-decode-sscanf` by almost none. Both reference PoCs grade PASS, so
   this is real difficulty, not a grader gap.

5. **Best value: gpt-5.4** — 24/37 at $5.18, a fraction of gpt-5/gpt-5.5 cost
   for the same tier.

## Caveats

- **Single seed.** Re-running gemini-3.5-flash flipped 13/37 cells (+8 solved,
  −5 regressed) at temperature 1.0 for a net +3. Treat each number as ±several;
  robust ranking needs multi-seed best-of-N.
- **Prompt-version mix.** Mid-run we clarified the system prompt ("no compiled
  harness; just write_file + grade()"). gpt-5.4 / sonnet / opus / gemini-3.5-flash
  ran under the new prompt; the rest under the old one. The change is near-neutral
  for models that already grade reliably (OpenAI/Anthropic) but lifted Gemini
  flash (which otherwise burned turns exploring instead of testing).
- **Gemini pro tier not included** here (separate key).

## Harness artifacts found & fixed during the run

Credibility note — four scaffold bugs were caught and fixed mid-run; results
above are post-fix. Each would have *understated* a model's score:

1. **`exec` oracle leak** — `exec` could `cat grader/expected.yaml` / copy
   `poc/poc.bin`, bypassing the `read_file` deny-list. Fixed: sandboxed bug
   view + separate oracle dir + (Docker) uid drop.
2. **Gemini `MALFORMED_FUNCTION_CALL` scored as a loss** — a malformed tool
   call returned no tool_calls and ended the episode. Fixed: retry.
3. **`MAX_TOKENS` truncation scored as a loss** — a chatty/thinking model hit
   the output cap before emitting its tool call. Fixed: bigger budget + nudge
   to continue.
4. **Gemini free-key 429 rate-limit crashed cells** — fixed with exponential
   backoff.
