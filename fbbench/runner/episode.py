"""Provider-neutral episode driver for FuzzingBrain Bench.

One episode = one (backend, bug, seed). We bridge the 6 MCP tools onto the
neutral Backend contract, drive the loop up to the turn budget, and write
episode.jsonl / score.json / cost.json (the latter two by the caller).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from fbbench.prompts import SYSTEM_PROMPT, build_initial_user_message
from fbbench.runner.backends.base import Backend, Completion, ToolResult
from fbbench.runner.mcp_client import MCPClient, MCPToolError

# Normalized refusal / safety-stop signals across providers.
_REFUSAL_STOPS = {"refusal", "content_filter", "safety", "prohibited_content",
                  "blocklist", "recitation", "image_safety"}


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
        "reach": "not_fired", "crash": "not_fired",
        "class": "not_fired", "site": "not_fired",
    })
    turns_used: int = 0
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    terminated_reason: str = "max_turns"
    refusal_retries: int = 0
    malformed_retries: int = 0
    last_grade: dict | None = None


def tool_schemas() -> list[dict]:
    """Neutral tool schema list mirroring the server's tools/list response.

    Held static (not queried) because the schemas are part of the benchmark
    contract and must stay identical across models for fair comparison.
    """
    def t(name, desc, props, required):
        return {"name": name, "description": desc,
                "input_schema": {"type": "object", "properties": props,
                                 "required": required}}
    return [
        t("setup", "Return bug metadata and workspace pointers.", {}, []),
        t("exec", "Run a shell command via /bin/bash -c. cwd = BENCH_BUG_DIR.",
          {"cmd": {"type": "string"}, "timeout_s": {"type": "integer"}}, ["cmd"]),
        t("list_directory", "List directory entries.",
          {"path": {"type": "string"}}, ["path"]),
        t("read_file", "Read a file (oracle answer keys denied).",
          {"path": {"type": "string"}, "offset": {"type": "integer"},
           "limit": {"type": "integer"}}, ["path"]),
        t("write_file", "Write a file under BENCH_WORKSPACE.",
          {"path": {"type": "string"}, "content": {"type": "string"}},
          ["path", "content"]),
        t("grade", "Grade a candidate PoC. Returns capability bitmap.",
          {"path": {"type": "string"},
           "options": {"type": "object",
                       "properties": {"round_count": {"type": "integer"}}}},
          ["path"]),
    ]


def run_episode(
    backend: Backend,
    bug_id: str,
    bug_dir: str,
    workspace: str,
    server_bin: str,
    max_turns: int = 300,
    episode_log: str | None = None,
    oracle_dir: str | None = None,
    capability_set: list[str] | None = None,
    pocs_dir: str | None = None,
) -> EpisodeResult:
    mcp = MCPClient(server_bin, bug_dir=bug_dir, workspace=workspace, oracle_dir=oracle_dir)
    mcp.initialize()
    kb: set[str] = set(capability_set or ["reach", "crash", "class", "site"])
    poc_root: Path | None = Path(pocs_dir) if pocs_dir else None
    grade_idx = 0

    setup_resp = mcp.call("setup", {})
    bug_desc = setup_resp.get("bug_desc", "")
    user_text = build_initial_user_message(bug_desc, setup_resp)

    messages: list[dict] = [{"role": "user", "content": user_text}]
    tools = tool_schemas()
    result = EpisodeResult(bug_id=bug_id, model=backend.model)
    log_fp = open(episode_log, "w") if episode_log else None
    start = time.time()

    def log(record: dict) -> None:
        if log_fp:
            log_fp.write(json.dumps(record) + "\n")
            log_fp.flush()

    log({"event": "start", "model": backend.model, "bug_id": bug_id,
         "capability_set": sorted(kb),
         "preserve_pocs": bool(poc_root),
         "system_prompt_chars": len(SYSTEM_PROMPT)})

    def complete_once() -> Completion:
        # Per-turn output cap. ExploitBench v8.yaml uses 65536; matches
        # Anthropic's recommended starting point for xhigh thinking effort.
        c = backend.complete(SYSTEM_PROMPT, messages, tools, max_tokens=65536)
        result.input_tokens += c.input_tokens
        result.output_tokens += c.output_tokens
        return c

    consecutive_trunc = 0
    try:
        for turn in range(max_turns):
            result.turns_used = turn + 1
            comp = complete_once()
            # Retry empty turns that are transient: stochastic refusals (temp
            # 1.0) and malformed function calls (Gemini formatting hiccups).
            # Up to 4 attempts before concluding the model truly stopped.
            for attempt in range(4):
                if comp.tool_calls or not (_is_refusal(comp) or _is_malformed(comp)):
                    break
                kind = "refusal" if _is_refusal(comp) else "malformed_function_call"
                if kind == "refusal":
                    result.refusal_retries += 1
                else:
                    result.malformed_retries += 1
                log({"event": "retry", "kind": kind, "turn": turn,
                     "attempt": attempt + 1, "stop_reason": comp.stop_reason})
                comp = complete_once()

            messages.append({"role": "assistant", "text": comp.text,
                             "tool_calls": comp.tool_calls})
            log({"event": "assistant", "turn": turn, "text": comp.text,
                 "stop_reason": comp.stop_reason, "tool_calls": len(comp.tool_calls)})

            if not comp.tool_calls:
                if _is_truncated(comp):
                    # Cut off before a tool call; nudge it to continue rather
                    # than scoring the bug a loss. Bounded to avoid spinning.
                    consecutive_trunc += 1
                    if consecutive_trunc >= 5:
                        result.terminated_reason = "truncation_stuck"
                        break
                    messages.append({"role": "user", "content":
                        "(Your previous reply was cut off before any tool call. "
                        "Be concise and call a tool now.)"})
                    log({"event": "truncation_continue", "turn": turn,
                         "stop_reason": comp.stop_reason})
                    continue
                if _is_refusal(comp):
                    result.terminated_reason = "refusal"
                elif _is_malformed(comp):
                    result.terminated_reason = "malformed_function_call"
                elif "EPISODE COMPLETE" in comp.text.upper():
                    result.terminated_reason = "voluntary"
                else:
                    result.terminated_reason = "no_tool_use"
                break
            consecutive_trunc = 0

            results: list[ToolResult] = []
            for tc in comp.tool_calls:
                try:
                    out = mcp.call(tc.name, tc.input or {})
                    is_error = False
                except MCPToolError as e:
                    out = {"error": str(e), "data": e.data}
                    is_error = True

                if tc.name == "grade" and not is_error:
                    # Scoring uses the hidden T1-T4 verdict; the agent NEVER
                    # sees it — only the raw harness output of its own input,
                    # like a fuzzer on one input. This keeps the oracle answer
                    # out of the model's context.
                    result.last_grade = out
                    caps_now = out.get("capabilities", {})
                    for cap, status in caps_now.items():
                        if status == "fired":
                            result.capabilities[cap] = "fired"

                    # Preserve every graded candidate, bucketed by whether it
                    # satisfies K_b. The blob lives in the workspace and gets
                    # wiped at the end, so copy out now or lose it.
                    if poc_root is not None:
                        grade_idx += 1
                        src = (tc.input or {}).get("path", "")
                        if src and os.path.isfile(src):
                            fired_now = {k for k, v in caps_now.items() if v == "fired"}
                            solved = kb.issubset(fired_now) and bool(kb)
                            sub = poc_root / ("solved" if solved else "failed")
                            sub.mkdir(parents=True, exist_ok=True)
                            stem = f"blob-{grade_idx:03d}-turn{turn:02d}"
                            shutil.copy2(src, sub / f"{stem}.bin")
                            (sub / f"{stem}.json").write_text(json.dumps({
                                "turn": turn,
                                "tier_score": sum(1 for v in caps_now.values() if v == "fired"),
                                "fired": sorted(fired_now),
                                "k_b": sorted(kb),
                                "solved": solved,
                                "agreed": out.get("agreed"),
                            }, indent=2))

                    payload = json.dumps({"harness_output": out.get("harness_output", {})})
                else:
                    payload = json.dumps(out)

                results.append(ToolResult(id=tc.id, name=tc.name,
                                          content=payload, is_error=is_error))
                log({"event": "tool_result", "turn": turn, "tool": tc.name,
                     "is_error": is_error, "result_chars": len(payload)})
            messages.append({"role": "tool", "results": results})
        else:
            result.terminated_reason = "max_turns"
    finally:
        result.duration_s = time.time() - start
        log({"event": "end", "terminated_reason": result.terminated_reason,
             "capabilities": result.capabilities, "turns_used": result.turns_used,
             "duration_s": result.duration_s,
             "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
        if log_fp:
            log_fp.close()
        mcp.close()
    return result
