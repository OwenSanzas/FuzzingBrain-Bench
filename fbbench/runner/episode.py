"""Provider-neutral episode driver for FuzzingBrain Bench.

One episode = one (backend, bug, seed). We bridge the 6 MCP tools onto the
neutral Backend contract, drive the loop up to the turn budget, and write
episode.jsonl / score.json / cost.json (the latter two by the caller).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from fbbench.prompts import (
    KEEP_HUNTING_NUDGE, TRUNCATION_NUDGE,
    budget_note, build_initial_user_message, system_prompt,
)
from dataclasses import replace
from fbbench.grading.bench_yaml import DEFAULT_KB, harness_sanitizer
from fbbench.grading.grader import solved as oracle_solved
from fbbench.grading.signature import signature as crash_signature
from fbbench.models.catalog import context_window
from fbbench.runner.backends.base import Backend, Completion, ToolResult
from fbbench.runner.mcp_client import MCPClient, MCPToolError

# Normalized refusal / safety-stop signals across providers.
_REFUSAL_STOPS = {"refusal", "content_filter", "safety", "prohibited_content",
                  "blocklist", "recitation", "image_safety"}

# The submission tool: the agent runs a candidate through the sanitizer harness
# via `run_poc_on_harness` (the sole name — no aliases). Scoring MUST match this
# name, or a correct solve is silently scored 0 (and, under reveal, the oracle
# verdict would leak back to the model instead of harness_output).
_GRADE_TOOLS = {"run_poc_on_harness"}


def _is_refusal(comp: Completion) -> bool:
    sr = (comp.stop_reason or "").lower()
    return any(tok in sr for tok in _REFUSAL_STOPS)


def _is_malformed(comp: Completion) -> bool:
    # Gemini (esp. flash) often emits FinishReason.MALFORMED_FUNCTION_CALL: the
    # model tried to call a tool but the call didn't parse, so no tool_calls
    # come back. That is a transient formatting failure, not "no tool use" —
    # retry the turn rather than ending the episode.
    return "malformed" in (comp.stop_reason or "").lower()


def _is_truncated(comp: Completion) -> bool:
    # Output token cap hit mid-reply (OpenAI "length", Gemini MAX_TOKENS,
    # Anthropic "max_tokens"). A chatty/thinking model can burn the budget
    # before emitting its tool call; that is truncation, not "no tool use".
    sr = (comp.stop_reason or "").lower()
    return sr == "length" or "max_tokens" in sr or "max_token" in sr


# --- Conversation compaction ------------------------------------------------
# The message history is append-only, so over a long episode it grows until it
# overflows the model's context window (API then rejects with
# context_length_exceeded). When the TRUE context size (input + cache tokens the
# provider counted) crosses a fraction of the model's window, we elide the big
# output BODIES of OLD tool results — keeping the tool call (name+args live in
# the assistant turn) plus a short placeholder, so the agent still knows what it
# did, just not the full dump. Pinned (never elided): system, the initial task,
# and the setup result. Only the api/model arm uses this loop; the codex/claude
# CLIs manage their own context.
# Trigger compaction at 85% of the TOTAL window — BUT never let the input eat
# into the room a full reply needs. The window is total (input+output); we request
# up to _COMPACT_OUTPUT_RESERVE output tokens, so we also floor the trigger at
# `window - reserve`. Whichever is smaller wins: big windows (1M) trigger at 85%;
# small windows (Haiku 200k) trigger at the reserve floor (~63%) so 170k input +
# 64k output can't overflow 200k.
_COMPACT_TRIGGER_FRAC = 0.85
# Per-turn output request. Default cap, but shrunk per model so input+output fit
# the window (a small-window model can't be asked for more output than its whole
# window); floored so the model always gets a usable reply budget.
_MAX_OUTPUT_TOKENS = 65_536       # desired cap (ExploitBench parity)
_MIN_OUTPUT_TOKENS = 4_096        # never request less than this
_OUTPUT_SAFETY_MARGIN = 2_048     # slack kept under the window
_COMPACT_OUTPUT_RESERVE = _MAX_OUTPUT_TOKENS + 8_192  # room the trigger reserves
_COMPACT_KEEP_RECENT_TURNS = 4  # most-recent tool-result turns kept in full
_COMPACT_LARGE_CHARS = 1500     # only elide a tool result whose content exceeds this
_ELIDED_PREFIX = "[elided:"     # marker so compaction is idempotent
_PINNED_TOOLS = {"setup"}       # small + critical results: never elide

# Budget note cadence: show the turn/time budget every N turns (plus the final
# turn and when time is low), not every turn — keeps per-turn context lean.
_BUDGET_EVERY = 30


def _compact_history(messages: list[dict], *, keep_recent_turns: int,
                     large_chars: int) -> int:
    """Elide the large output bodies of OLD tool results IN PLACE; return the
    number of characters reclaimed. Preserves id/name/is_error (so the
    tool_use<->tool_result pairing stays valid), keeps the most-recent
    `keep_recent_turns` tool messages and any pinned/small result untouched, and
    is idempotent (already-elided results are skipped)."""
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old_idxs = tool_idxs[:-keep_recent_turns] if keep_recent_turns > 0 else tool_idxs
    reclaimed = 0
    for i in old_idxs:
        new_results = []
        for r in messages[i].get("results") or []:
            if (r.name not in _PINNED_TOOLS
                    and not r.content.startswith(_ELIDED_PREFIX)
                    and len(r.content) > large_chars):
                reclaimed += len(r.content)
                placeholder = (f"{_ELIDED_PREFIX} {r.name} output was "
                               f"{len(r.content)} chars, removed to save context; "
                               f"re-run the tool if you need it again]")
                new_results.append(replace(r, content=placeholder))
            else:
                new_results.append(r)
        messages[i] = {**messages[i], "results": new_results}
    return reclaimed


# Cold-start tokens-per-char (chars/4) used only until we observe a real ratio.
_COLD_START_TOKENS_PER_CHAR = 0.25


def _measure_chars(sysp: str, messages: list[dict]) -> int:
    """Total characters a complete() call will send: the system prompt + every
    message's text / content / note / tool-result bodies / tool-call args."""
    chars = len(sysp or "")
    for m in messages:
        chars += len(m.get("text") or "") + len(m.get("content") or "") + len(m.get("note") or "")
        for r in m.get("results") or []:
            chars += len(r.content or "")
        for tc in m.get("tool_calls") or []:
            chars += len(json.dumps(getattr(tc, "input", None) or {}))
    return chars


def _estimate_tokens(sysp: str, messages: list[dict], tokens_per_char: float) -> int:
    """Pre-call token estimate = measured chars x a SELF-CALIBRATED tokens/char
    ratio (from the provider's real input_tokens on the previous call — so it
    tracks this model's actual tokenizer and even absorbs the tools-schema
    overhead). Cold start uses chars/4. Guard-only; provider usage is authoritative."""
    return int(_measure_chars(sysp, messages) * tokens_per_char)


def _output_budget(est_input_tokens: int, window: int) -> int:
    """Max output tokens to request: the default cap, shrunk so input+output fit
    the window (a small-window model can't produce more output than its whole
    window), floored so the model always gets a usable reply budget."""
    room = window - est_input_tokens - _OUTPUT_SAFETY_MARGIN
    return max(_MIN_OUTPUT_TOKENS, min(_MAX_OUTPUT_TOKENS, room))


def _compact_to_fit(sysp: str, messages: list[dict], window: int,
                    tokens_per_char: float) -> int:
    """Compact BEFORE sending so the about-to-send context fits with room for a
    reply. Escalates: first elide OLD large tool outputs (keep recent 4), then
    expose more (keep 1), then ALL turns (keep 0) — until the estimate fits or
    nothing is left to reclaim. setup stays pinned throughout. Returns chars
    reclaimed."""
    if window <= 0:
        return 0
    target = min(_COMPACT_TRIGGER_FRAC * window, window - _COMPACT_OUTPUT_RESERVE)
    reclaimed = 0
    for keep in (_COMPACT_KEEP_RECENT_TURNS, 1, 0):
        if _estimate_tokens(sysp, messages, tokens_per_char) <= target:
            break
        r = _compact_history(messages, keep_recent_turns=keep,
                             large_chars=_COMPACT_LARGE_CHARS)
        reclaimed += r
        if r == 0 and keep == 0:
            break  # nothing left to reclaim
    return reclaimed


# Substrings that mark a provider "prompt too long / context exceeded" error.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded", "context length", "maximum context",
    "prompt is too long", "too many tokens", "reduce the length",
    "input is too long", "exceeds the maximum",
)


def _is_context_overflow(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(mk in msg for mk in _CONTEXT_OVERFLOW_MARKERS)


@dataclass
class EpisodeResult:
    bug_id: str
    model: str
    capabilities: dict[str, str] = field(default_factory=lambda: {
        "reach": "not_fired", "crash": "not_fired", "differential": "not_fired",
        "class": "not_fired", "site": "not_fired",
    })
    # Best-of counterpart: a rung counts fired if it fired on ANY round (vs
    # `capabilities`, which is unanimity — fired only if every round fired).
    # Human/report facing only; never enters the model payload.
    capabilities_bestof: dict[str, str] = field(default_factory=lambda: {
        "reach": "not_fired", "crash": "not_fired", "differential": "not_fired",
        "class": "not_fired", "site": "not_fired",
    })
    # The authoritative solve: some SINGLE candidate reproduced the full target
    # defect (oracle target_bug_found). NOT a sticky union across candidates.
    solved: bool = False
    # Distinct crashes found this episode: the set of unique crash signatures
    # (crash-type + top application frames). unique_crashes = len(crash_signatures)
    # is the headline score; the capability ladder above is diagnostic only.
    crash_signatures: set[str] = field(default_factory=set)
    unique_crashes: int = 0
    # Which grader actually answered, observed rather than assumed: the image
    # decides, from whether a harness is baked into it, and the runner only finds
    # out by grading something. Empty until a candidate is graded — a run where
    # the model never submitted anything has no answer to give, and reporting a
    # guess there is how a config field stops being evidence.
    grading: str = ""
    turns_used: int = 0
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    terminated_reason: str = "max_turns"
    refusal_retries: int = 0
    malformed_retries: int = 0
    last_grade: dict | None = None
    error: str | None = None


def _crash_identity(out: dict) -> str:
    """Which crash this graded candidate produced, as a key for the run's set.

    Three sources, best first, because a run may be graded three different ways
    and the number has to mean the same thing in all of them:

      1. `crash_signature` — a self-contained image graded this locally and
         already named the crash with the shipped rules. Use its answer; naming
         it again here could only disagree with it.
      2. the harness output — an image that grades remotely returns the run's
         output but no signature, so name it with the SAME rules the image and
         the backend use. One implementation, three callers.
      3. a constant — the grader says a crash fired but nothing in the output
         names it. Every such crash then shares one identity, which UNDERcounts.
         That is the right way to be wrong: a run is never credited with a
         distinct find we cannot actually distinguish.
    """
    if out.get("crash_signature"):
        return str(out["crash_signature"])
    sig = crash_signature(out.get("harness_output") or {})
    return sig.canon_sig if sig else "crash|<unnamed>"


def neutral_tools(mcp: MCPClient) -> list[dict]:
    """Tool schemas straight from the MCP server's tools/list — the single source
    of truth for the tool surface (name + description + params).

    Previously the runner hand-mirrored these, which silently drifted from the
    server's own list (and from the Codex arm, which reads the server directly).
    Querying the one canonical source keeps the schemas identical across BOTH
    arms and every model. The server's tools/list is a static function over the
    mcp-server pinned in the challenge image, so this stays deterministic. The only
    transform is the inputSchema -> input_schema key the backends expect.
    """
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["inputSchema"]} for t in mcp.list_tools()]


def backfill_sanitizer(setup_resp: dict, bug_dir: str) -> None:
    """Ensure setup_resp carries the build's sanitizer token before the first user
    turn. The sanitizer the build is judged under is public setup info the model
    must have (a real auditor always knows their own instrumentation); the
    canonical answer-free challenge image's setup() omits it, so backfill it from
    the local bench.yaml. No-op if setup() already supplied it or it is unknown.
    """
    if setup_resp.get("sanitizer"):
        return
    san = harness_sanitizer(Path(bug_dir))
    if san:
        setup_resp["sanitizer"] = san


def run_episode(
    backend: Backend,
    bug_id: str,
    bug_dir: str,
    workspace: str,
    image: str,
    max_turns: int = 300,
    time_budget_s: float | None = None,
    episode_log: str | None = None,
    oracle_dir: str | None = None,
    capability_set: list[str] | None = None,
    pocs_dir: str | None = None,
    stop_on_solve: bool = False,
    mode: str = "full-scan",
    run: dict[str, str] | None = None,
) -> EpisodeResult:
    mcp = MCPClient(bug_dir=bug_dir, workspace=workspace, image=image, run=run)
    mcp.initialize()
    kb: set[str] = set(capability_set or DEFAULT_KB)
    poc_root: Path | None = Path(pocs_dir) if pocs_dir else None
    grade_idx = 0
    # Rank of the best SINGLE candidate seen so far — (best-of fired, unanimity
    # fired, is-target). result.capabilities holds that candidate's verdict, NOT
    # a union across different inputs.
    best_cand_key = (-1, -1, -1)

    setup_resp = mcp.call("setup", {})
    # Read the sanitizer from the LOCAL bundle: in the canonical path bug_dir is a
    # container path ("/src"); the host-side bug bundle is oracle_dir.
    backfill_sanitizer(setup_resp, oracle_dir or bug_dir)
    # The mode selects only the FIRST user turn; the system prompt is the same
    # (blind) text for every mode. full-scan is the one active public mode;
    # diffscan (delta-N) is a reserved extension point (see prompts.py).
    if mode == "full-scan":
        user_text = build_initial_user_message(setup_resp)
    elif mode == "diffscan":
        raise NotImplementedError("diff-scan (delta-N) mode is not implemented")
    else:
        raise ValueError(f"unknown mode: {mode!r} (expected full-scan | diffscan)")
    sysp = system_prompt()

    messages: list[dict] = [{"role": "user", "content": user_text}]
    tools = neutral_tools(mcp)
    result = EpisodeResult(bug_id=bug_id, model=backend.model)
    log_fp = open(episode_log, "w") if episode_log else None
    # Full-fidelity transcript alongside the compact episode.jsonl ledger:
    # every prompt, model output, tool-call argument, and tool return verbatim.
    # Kept in a separate file so episode.jsonl stays small for sweep/analysis,
    # while the complete record is always available for paper artifacts/debug.
    tlog_fp = (open(os.path.join(os.path.dirname(episode_log), "transcript.jsonl"), "w")
               if episode_log else None)
    start = time.time()
    # Wall-clock deadline the episode enforces on itself so it can write score.json
    # before the orchestrator's backstop SIGKILL. None => turn-bounded only.
    deadline = (start + time_budget_s) if time_budget_s else None

    def log(record: dict) -> None:
        if log_fp:
            log_fp.write(json.dumps(record) + "\n")
            log_fp.flush()

    def tlog(record: dict) -> None:
        if tlog_fp:
            tlog_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            tlog_fp.flush()

    def _payload_obj(payload: str):
        # Store tool returns as parsed objects when possible (readable), else raw.
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return payload

    # Which oracle graded this episode. It decides where every verdict came
    # from and is invisible in the results, so without it a run that silently
    # graded against the wrong backend is indistinguishable afterwards from one
    # that did not. "image default" means the endpoint baked into the challenge
    # image, which is the case for every run that does not override it.
    log({"event": "start", "model": backend.model, "bug_id": bug_id,
         "capability_set": sorted(kb),
         "preserve_pocs": bool(poc_root),
         "grade_url": os.environ.get("BENCH_GRADE_URL") or "image default",
         "system_prompt_chars": len(sysp)})

    tlog({"event": "start", "model": backend.model, "bug_id": bug_id,
          "capability_set": sorted(kb), "max_turns": max_turns,
          "preserve_pocs": bool(poc_root),
          "system_prompt": sysp,
          "initial_user_message": user_text,
          "tools": tools})

    # Self-calibrating tokens/char, updated from each call's real input_tokens so
    # the pre-call estimate tracks THIS model's tokenizer (cold start = chars/4).
    tpc = [_COLD_START_TOKENS_PER_CHAR]

    def complete_once() -> Completion:
        # Pre-call guard: compact BEFORE sending, so a turn that just appended a lot
        # (many/large tool results) cannot overflow the window on THIS call — the
        # post-call exact count would be one step behind. Acts on a self-calibrated
        # estimate of the about-to-send size and escalates until it fits.
        window = context_window(backend.model)
        reclaimed = _compact_to_fit(sysp, messages, window, tpc[0])
        est = _estimate_tokens(sysp, messages, tpc[0])
        if reclaimed:
            pct = round(100.0 * est / window, 1) if window else 0.0
            ev = {"event": "context_compaction", "turn": turn, "est_tokens": est,
                  "window": window, "pct_of_window": pct, "reclaimed_chars": reclaimed,
                  "tokens_per_char": round(tpc[0], 4),
                  "msg": (f"context compaction (pre-call) - est/limit = "
                          f"{est}/{window} = {pct}%; reclaimed {reclaimed} chars")}
            log(ev)
            tlog(ev)
        # Adaptive per-turn output cap: default 65536, but shrunk so input+output
        # fit the window (fixes small-window models where a fixed 65536 alone
        # exceeds the whole window, and shrinks output when the input is large).
        try:
            c = backend.complete(sysp, messages, tools,
                                 max_tokens=_output_budget(est, window))
        except Exception as e:  # noqa: BLE001
            if not _is_context_overflow(e):
                raise
            # Backstop: estimate was off and the provider rejected for length. Hard-
            # compact everything (no recent-turn protection, tiny threshold), keeping
            # only setup pinned, and retry ONCE so the cell degrades instead of dying.
            hard = _compact_history(messages, keep_recent_turns=0, large_chars=1)
            ev = {"event": "context_overflow_retry", "turn": turn, "window": window,
                  "reclaimed_chars": hard, "error": str(e)[:200]}
            log(ev)
            tlog(ev)
            est = _estimate_tokens(sysp, messages, tpc[0])
            c = backend.complete(sysp, messages, tools,
                                 max_tokens=_output_budget(est, window))
        # Calibrate: real prompt tokens / chars we actually sent (messages are
        # unchanged until the caller appends), so the ratio absorbs formatting +
        # tools-schema overhead the char count misses. Refresh only on real data.
        sent_chars = _measure_chars(sysp, messages)
        real_tokens = c.input_tokens + c.cache_read_tokens + c.cache_write_tokens
        if sent_chars > 0 and real_tokens > 0:
            tpc[0] = real_tokens / sent_chars
        result.input_tokens += c.input_tokens
        result.output_tokens += c.output_tokens
        result.cache_read_tokens += c.cache_read_tokens
        result.cache_write_tokens += c.cache_write_tokens
        return c

    consecutive_trunc = 0
    try:
        for turn in range(max_turns):
            result.turns_used = turn + 1
            comp = complete_once()
            # A refusal / malformed-function-call means we got NO usable reply
            # (an API-level safety refusal or a parse failure), not a task
            # outcome — so re-draw up to 3 attempts to obtain a valid completion.
            # (Task-level flaky knobs — truncation, grade rounds — stay at 1.)
            for attempt in range(3):
                if comp.tool_calls or not (_is_refusal(comp) or _is_malformed(comp)):
                    break
                kind = "refusal" if _is_refusal(comp) else "malformed_function_call"
                if kind == "refusal":
                    result.refusal_retries += 1
                else:
                    result.malformed_retries += 1
                log({"event": "retry", "kind": kind, "turn": turn,
                     "attempt": attempt + 1, "stop_reason": comp.stop_reason})
                tlog({"event": "retry", "kind": kind, "turn": turn,
                      "attempt": attempt + 1, "stop_reason": comp.stop_reason,
                      "text": comp.text})
                comp = complete_once()

            messages.append({"role": "assistant", "text": comp.text,
                             "tool_calls": comp.tool_calls})
            log({"event": "assistant", "turn": turn, "text": comp.text,
                 "stop_reason": comp.stop_reason, "tool_calls": len(comp.tool_calls)})
            tlog({"event": "assistant", "turn": turn, "text": comp.text,
                  "stop_reason": comp.stop_reason,
                  "input_tokens": comp.input_tokens, "output_tokens": comp.output_tokens,
                  "cache_read_tokens": comp.cache_read_tokens,
                  "cache_write_tokens": comp.cache_write_tokens,
                  "tool_calls": [{"id": tc.id, "name": tc.name, "input": tc.input}
                                 for tc in comp.tool_calls]})

            if not comp.tool_calls:
                if _is_truncated(comp):
                    # Flaky-retry budget = 1: a truncated (cut-off) reply ends the
                    # episode immediately rather than being nudged to continue.
                    # Standing experiment rule, no retries. (Was >= 5.)
                    consecutive_trunc += 1
                    if consecutive_trunc >= 1:
                        result.terminated_reason = "truncation_stuck"
                        break
                    messages.append({"role": "user", "content": TRUNCATION_NUDGE})
                    log({"event": "truncation_continue", "turn": turn,
                         "stop_reason": comp.stop_reason})
                    tlog({"event": "truncation_continue", "turn": turn,
                          "stop_reason": comp.stop_reason, "text": comp.text})
                    continue
                would_stop = ("refusal" if _is_refusal(comp)
                              else "malformed_function_call" if _is_malformed(comp)
                              else "voluntary" if ("ASSESSMENT COMPLETE" in comp.text.upper()
                                                   or "EPISODE COMPLETE" in comp.text.upper())
                              else "no_tool_use")
                result.terminated_reason = would_stop
                break
            consecutive_trunc = 0

            results: list[ToolResult] = []
            crashed_hit = False  # a crash fired this turn (not the target solve)
            solved_hit = False   # a candidate this turn that IS the target defect
            for tc in comp.tool_calls:
                try:
                    # Which turn this submission came from. The oracle records it
                    # so a find can be placed in the episode without replaying the
                    # transcript; the budget it is measured against is already on
                    # the batch. Turns are 1-based here to match the budget note.
                    out = mcp.call(tc.name, tc.input or {}, meta={"turn": turn + 1})
                    is_error = False
                except MCPToolError as e:
                    out = {"error": str(e), "data": e.data}
                    is_error = True

                if tc.name in _GRADE_TOOLS and not is_error:
                    # Scoring uses the hidden T1-T4 verdict; the agent NEVER
                    # sees it — only the raw harness output of its own input,
                    # like a fuzzer on one input. This keeps the oracle answer
                    # out of the model's context.
                    result.last_grade = out
                    # Adopt the oracle's full per-bug verdict (it knows the real
                    # capability_set incl. `differential` and any `n/a` rungs).
                    caps_now = out.get("capabilities", {})          # unanimity
                    bestof_now = out.get("capabilities_bestof") or {}  # best-of
                    target_found = oracle_solved(out)
                    if target_found:
                        result.solved = True

                    # Keep the BEST SINGLE candidate — NOT a sticky union across
                    # different inputs. A union lets inputs that each fire a
                    # different rung add up to a bogus "solve" no single PoC ever
                    # achieved; the honest signal is one input's own reach. Rank by
                    # (best-of fired, unanimity fired, is-target).
                    cand_key = (sum(1 for v in bestof_now.values() if v == "fired"),
                                sum(1 for v in caps_now.values() if v == "fired"),
                                int(target_found))
                    if cand_key > best_cand_key:
                        best_cand_key = cand_key
                        if caps_now:
                            result.capabilities = dict(caps_now)
                        if bestof_now:
                            result.capabilities_bestof = dict(bestof_now)

                    # Preserve every graded candidate, bucketed by whether it IS
                    # the target defect. The blob lives in the workspace and gets
                    # wiped at the end, so copy out now or lose it.
                    if poc_root is not None:
                        grade_idx += 1
                        src = (tc.input or {}).get("path", "")
                        sub = poc_root / ("solved" if target_found else "failed")
                        stem = f"blob-{grade_idx:03d}-turn{turn:02d}"
                        sub.mkdir(parents=True, exist_ok=True)
                        # In the docker path the candidate lives inside the
                        # container; copy_out uses `docker cp` to reach it.
                        if src and mcp.copy_out(src, sub / f"{stem}.bin"):
                            fired_now = {k for k, v in bestof_now.items() if v == "fired"}
                            (sub / f"{stem}.json").write_text(json.dumps({
                                "turn": turn,
                                "tier_score": sum(1 for v in bestof_now.values() if v == "fired"),
                                "fired": sorted(fired_now),
                                "k_b": sorted(kb),
                                "solved": target_found,
                                "agreed": out.get("agreed"),
                            }, indent=2))

                    # Steering flags. crashed_hit fires the breadth "keep hunting"
                    # nudge on ANY crash — INCLUDING the target — so the model can
                    # never infer target_found from the nudge's presence/absence
                    # (it would leak the sealed verdict under --no-stop-on-solve).
                    # solved_hit is separate: it only gates stop_on_solve, and
                    # target_found is otherwise read for SCORING, never surfaced.
                    if target_found:
                        solved_hit = True
                    # The in-image grader says so itself; anything else came off
                    # the wire. Recorded on every grade rather than only the
                    # first, so a run that somehow changed graders mid-episode
                    # reports the last one that actually answered instead of a
                    # stale first impression.
                    result.grading = ("in-image" if out.get("grading") == "local"
                                      else "remote-oracle")
                    if out.get("crashed") or (bestof_now or caps_now).get("crash") == "fired":
                        crashed_hit = True
                        sig = _crash_identity(out)
                        if sig not in result.crash_signatures:
                            result.crash_signatures.add(sig)
                            result.unique_crashes = len(result.crash_signatures)
                            log({"event": "unique_crash", "turn": turn,
                                 "signature": sig, "unique_crashes": result.unique_crashes})
                            tlog({"event": "unique_crash", "turn": turn,
                                  "signature": sig, "unique_crashes": result.unique_crashes})

                    # The runner runs the image with BENCH_GRADE_REVEAL=1, so the
                    # image's own seal is bypassed and `out` holds the full
                    # verdict. This rebuilds the seal for the model, and is the
                    # one that matters in a real run -- the image's version only
                    # ever applies to an external user driving the image directly.
                    # Keep it an allow-list, and check it against the ones in
                    # tools/mcp-server/gradeserver.go (remote) and gradelocal.go
                    # (in-image) whenever any of them moves: a field added there
                    # and not here is invisible to every benchmark run. The
                    # three are not identical on purpose --
                    # duration_ms is forwarded there and withheld here, because
                    # the model is not meant to tune against grading latency.
                    sealed = {"harness_output": out.get("harness_output", {})}
                    if out.get("crash_novelty"):
                        sealed["crash_novelty"] = out["crash_novelty"]
                    payload = json.dumps(sealed)
                else:
                    payload = json.dumps(out)

                results.append(ToolResult(id=tc.id, name=tc.name,
                                          content=payload, is_error=is_error))
                log({"event": "tool_result", "turn": turn, "tool": tc.name,
                     "is_error": is_error, "result_chars": len(payload)})
                tlog({"event": "tool_result", "turn": turn, "tool": tc.name,
                      "id": tc.id, "input": tc.input or {}, "is_error": is_error,
                      "result": _payload_obj(payload)})
            # Budget awareness: show the turn + wall-clock budget only at intervals
            # (every _BUDGET_EVERY turns, the final turn, or when time is low) so it
            # doesn't clutter every turn. The crash "keep hunting" nudge is separate
            # and fires on ANY crash turn.
            done_t = turn + 1
            remaining = max_turns - done_t
            now = time.time()
            elapsed = now - start
            rem_s = (deadline - now) if deadline is not None else None
            time_low = bool(time_budget_s) and rem_s is not None and rem_s <= 0.25 * time_budget_s
            show_budget = (done_t % _BUDGET_EVERY == 0) or (done_t == max_turns) or time_low
            note_parts: list[str] = []
            # A crash this turn (that did not end the episode): the breadth "keep
            # hunting for more distinct crashes" nudge. Positive + leak-free — it
            # never reveals whether the crash was the hidden target.
            if crashed_hit:
                note_parts.append(KEEP_HUNTING_NUDGE)
            if show_budget:
                note_parts.append(budget_note(done_t, max_turns, remaining,
                                              elapsed_s=elapsed, remaining_s=rem_s,
                                              time_budget_s=time_budget_s))
            tool_msg: dict = {"role": "tool", "results": results}
            if note_parts:
                tool_msg["note"] = "\n\n".join(note_parts)
                # Record the note in the transcript so the run is auditable (it's
                # injected into the model's context but not in tool_result).
                tlog({"event": "budget_note", "turn": turn, "note": tool_msg["note"]})
            messages.append(tool_msg)
            # (Context compaction now runs PRE-call in complete_once, so a turn's
            # freshly-appended tool results can't overflow the next call before we
            # get a chance to compact. Nothing to do here.)
            # Stop-on-solve (default; disable with --no-stop-on-solve): a single
            # candidate reproduced the full target defect (target_bug_found). The
            # model is blind to the verdict and cannot know it succeeded, so the
            # runner ends the episode here. With stop_on_solve off, the episode
            # instead runs until the agent stops (ASSESSMENT COMPLETE) or budget.
            if solved_hit and stop_on_solve:
                result.terminated_reason = "solved"
                log({"event": "solved", "turn": turn})
                tlog({"event": "solved", "turn": turn})
                break
            # Wall-clock budget: stop gracefully (between turns) so the finally
            # block still writes score.json — the orchestrator SIGKILL is only a
            # backstop past this deadline + margin.
            if deadline is not None and time.time() >= deadline:
                result.terminated_reason = "time_budget"
                log({"event": "time_budget", "turn": turn,
                     "elapsed_s": round(time.time() - start, 1)})
                tlog({"event": "time_budget", "turn": turn,
                      "elapsed_s": round(time.time() - start, 1)})
                break
        else:
            result.terminated_reason = "max_turns"
    except Exception as e:
        # A mid-run failure (LLM transport error, oracle/docker fault, etc.)
        # must NOT leave a half-written run dir. Record it on the result and
        # return normally so the caller still emits score.json/cost.json with
        # terminated_reason="error" — a crashed run stays distinguishable from
        # an honest "ran and scored 0", and a sweep never loses the row.
        # KeyboardInterrupt is a BaseException, not Exception, so Ctrl-C still
        # propagates and aborts the sweep as expected.
        result.terminated_reason = "error"
        result.error = f"{type(e).__name__}: {e}"
        log({"event": "error", "turn": result.turns_used, "error": result.error})
        tlog({"event": "error", "turn": result.turns_used, "error": result.error})
    finally:
        result.duration_s = time.time() - start
        log({"event": "end", "terminated_reason": result.terminated_reason,
             "capabilities": result.capabilities, "solved": result.solved,
             "unique_crashes": result.unique_crashes,
             "crash_signatures": sorted(result.crash_signatures),
             "turns_used": result.turns_used,
             "duration_s": result.duration_s,
             "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
        tlog({"event": "end", "terminated_reason": result.terminated_reason,
              "capabilities": result.capabilities, "solved": result.solved,
              "unique_crashes": result.unique_crashes,
              "crash_signatures": sorted(result.crash_signatures),
              "turns_used": result.turns_used,
              "duration_s": result.duration_s,
              "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
        if log_fp:
            log_fp.close()
        if tlog_fp:
            tlog_fp.close()
            # Distil the transcript into a readable trajectory chain (traj.jsonl +
            # traj.md). Best-effort: never let it break a completed episode.
            try:
                from fbbench.runner.traj import write_traj
                if episode_log:
                    d = os.path.dirname(episode_log)
                    write_traj(os.path.join(d, "transcript.jsonl"), d,
                               f"{bug_id} / {backend.model}")
            except Exception:
                pass
        mcp.close()
    return result
