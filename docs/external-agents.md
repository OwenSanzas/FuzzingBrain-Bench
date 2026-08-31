# Running an external agent

The bench can evaluate an agent it does not contain. The agent lives in its own
repository; the bench is handed a five-line manifest that says how to invoke it,
and nothing else about it enters this tree.

```bash
fb-bench run <bugs> --agent <name-or-path>
```

## The contract

Every agent plugs into the same waist, whatever it is inside:

1. The bench stages the challenge source into a working directory (copied out of
   the sealed challenge image; the answer is not in it).
2. The bench drops a `./submit <file>` command there. It runs a candidate on the
   sealed harness and returns the verdict — `crash: <signature>` or `clean`.
3. The agent is a command run in that directory. It reads the source, builds a
   candidate, calls `./submit`, and iterates.

The agent never touches Docker, never learns the image name, and never learns
which version it is looking at — the source it reads and the harness its
candidate runs on are the one sealed image, so they cannot disagree. Scoring is
the bench's own `grade_blob`, the same number every arm reports.

## The manifest

```yaml
name: fbagent

# How the bench invokes the agent, run in the staged directory.
# Template fields: {workspace} {timeout} {opening} {submit}
# @path inlines a file next to the manifest (e.g. a long system prompt).
command: >
  omp -p "{opening}" --tools read,glob,grep,bash
  --system-prompt @agent/fuzzing-brain.md --max-time {timeout}

# Optional: the env var the agent reads its shell from. The bench points it at
# a sandbox that masks the Docker socket and (unless network is allowed) runs in
# an empty net namespace, so a careless or adversarial agent cannot read the
# sealed answer out of the image or fetch a published PoC.
shell_env: OMP_SHELL_PATH

# blocked (default) or allowed.
network: blocked
```

A CLI agent is just as short:

```yaml
name: aider
command: "aider --yes --message 'Find a crashing input for the harness here, test it with ./submit' {workspace}"
network: blocked
```

## Registering a name

`--agent` takes a path or a bare name. A name is looked up as
`<name>.agent.yaml` on a search path, in order:

1. `$FBBENCH_AGENTS` (colon-separated directories)
2. `~/.config/fbbench/agents/`
3. `agents/` in this repository

Register by dropping the manifest — or a symlink to the one in the agent's own
repo — into any of them:

```bash
ln -s /path/to/agent-repo/aider.agent.yaml ~/.config/fbbench/agents/aider.agent.yaml
fb-bench run all --agent aider --jobs 4
```

Nothing about the agent is copied into the bench. The pointer is all it holds.

## What a run leaves

```
output/<run>/<bug>/<model>/seed-0/
├── score.json    unique_crashes, crash_signatures, duration, network, agent
├── best_blob     the first candidate that crashed
├── agent.log     the agent's transcript
└── pocs/{crashed,clean}/   every candidate it submitted, with its verdict
```

`score.json` records `agent` and `grading: in-image` — who ran (an external
agent) and who judged (the bench), kept apart so the number can be trusted.

## Reporting cost (optional)

The bench cannot see inside an external agent, so a run's cost is only knowable
if the agent reports it. Write `.fbbench/usage.json` in the working directory
before exiting:

```json
{
  "model": "claude-opus-5",
  "input_tokens": 35217,
  "output_tokens": 6376,
  "cache_read_tokens": 537296,
  "cache_write_tokens": 29186,
  "input_is_total": false
}
```

Set `input_is_total: true` if `input_tokens` includes the cached prefix (what
OpenAI-style clients report); the bench de-totals it so every arm prices the
same way. The cost is then computed with the bench's own price table and
recorded with `cost_basis: agent-reported`.

An agent that reports nothing is costed as **unknown**, not as zero — `total_usd`
stays null and the leaderboard prints `$ —`. A missing cost must never look like
a free run.
