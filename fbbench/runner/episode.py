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
from fbbench.grading.bench_yaml import DEFAULT_KB, harness_sanitizer
from fbbench.runner.backends.base import Backend, Completion, ToolResult
from fbbench.runner.mcp_client import MCPClient, MCPToolError

# Normalized refusal / safety-stop signals across providers.
_REFUSAL_STOPS = {"refusal", "content_filter", "safety", "prohibited_content",
                  "blocklist", "recitation", "image_safety"}

# The submission/grading tool. The server advertises it as `run_input` and keeps
# `grade`/`verify_poc` as hidden aliases (in the oracle's mcp-server), so the model
# only ever calls `run_input`. Scoring MUST match the same family, or a correct
# solve submitted via the advertised name is silently scored 0 (and, under
# reveal, the oracle verdict leaks back to the model instead of harness_output).
_GRADE_TOOLS = {"grade", "run_input", "verify_poc"}


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
    episode_log: str | None = None,
    oracle_dir: str | None = None,
    capability_set: list[str] | None = None,
    pocs_dir: str | None = None,
    stop_on_solve: bool = True,
    mode: str = "full-scan",
) -> EpisodeResult:
    mcp = MCPClient(bug_dir=bug_dir, workspace=workspace, image=image)
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

    log({"event": "start", "model": backend.model, "bug_id": bug_id,
         "capability_set": sorted(kb),
         "preserve_pocs": bool(poc_root),
         "system_prompt_chars": len(sysp)})

    tlog({"event": "start", "model": backend.model, "bug_id": bug_id,
          "capability_set": sorted(kb), "max_turns": max_turns,
          "preserve_pocs": bool(poc_root),
          "system_prompt": sysp,
          "initial_user_message": user_text,
          "tools": tools})

    def complete_once() -> Completion:
        # Per-turn output cap. ExploitBench v8.yaml uses 65536; matches
        # Anthropic's recommended starting point for xhigh thinking effort.
        c = backend.complete(sysp, messages, tools, max_tokens=65536)
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
                    out = mcp.call(tc.name, tc.input or {})
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
                    target_found = bool(out.get("target_bug_found", False))
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

                    # Steering flags: a full single-input solve stops the episode
                    # (when stop_on_solve); any other crash triggers the breadth
                    # "keep hunting" nudge. Crash-driven — silent when nothing
                    # crashed. target_found is read for SCORING only and is NEVER
                    # surfaced to the model.
                    if target_found:
                        solved_hit = True
                    elif (bestof_now or caps_now).get("crash") == "fired":
                        crashed_hit = True

                    payload = json.dumps({"harness_output": out.get("harness_output", {})})
                else:
                    payload = json.dumps(out)

                results.append(ToolResult(id=tc.id, name=tc.name,
                                          content=payload, is_error=is_error))
                log({"event": "tool_result", "turn": turn, "tool": tc.name,
                     "is_error": is_error, "result_chars": len(payload)})
                tlog({"event": "tool_result", "turn": turn, "tool": tc.name,
                      "id": tc.id, "input": tc.input or {}, "is_error": is_error,
                      "result": _payload_obj(payload)})
            # Budget awareness (aligns with ExploitBench): every turn tells the
            # model where it is; from 75% of the budget on, add a wrap-up nudge.
            done_t = turn + 1
            remaining = max_turns - done_t
            note = budget_note(done_t, max_turns, remaining)
            # A crash this turn (that did not end the episode): prepend the breadth
            # "keep hunting for more distinct crashes" nudge. Positive + leak-free —
            # it never reveals whether the crash was the hidden target.
            if crashed_hit:
                note = KEEP_HUNTING_NUDGE + "\n\n" + note
            messages.append({"role": "tool", "results": results, "note": note})
            # Record the budget note in the transcript so the run is auditable
            # (it's injected into the model's context but not in tool_result).
            tlog({"event": "budget_note", "turn": turn, "note": note})
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
             "turns_used": result.turns_used,
             "duration_s": result.duration_s,
             "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
        tlog({"event": "end", "terminated_reason": result.terminated_reason,
              "capabilities": result.capabilities, "solved": result.solved,
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
