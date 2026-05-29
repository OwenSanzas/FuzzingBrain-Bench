"""Gemini backend: neutral history <-> google-genai function calling.

Gemini uses user/model roles, function_call / function_response parts, and
matches results to calls by function NAME (no call ids), so we synthesize ids
purely for the loop's bookkeeping.
"""
from __future__ import annotations

import json
import os
import sys
import time

from google import genai
from google.genai import types

from .base import Completion, ToolCall

# Free-tier Gemini keys are tightly rate-limited (low RPM); a multi-turn
# episode fires many calls fast and hits 429. Retry transient errors with
# exponential backoff so the sweep doesn't drop cells to no-score.json.
_BACKOFF = [5, 12, 30, 60, 90, 120]
_TRANSIENT = ("429", "resource_exhausted", "rate", "503", "unavailable",
              "500", "internal", "deadline", "timeout")


def _with_backoff(fn):
    last = None
    for i in range(len(_BACKOFF) + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — classify by message
            msg = str(e).lower()
            if not any(t in msg for t in _TRANSIENT) or i == len(_BACKOFF):
                raise
            last = e
            wait = _BACKOFF[i]
            print(f"  [gemini] transient error, backoff {wait}s: {str(e)[:120]}",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
    raise last  # unreachable


def _params(input_schema: dict):
    # Gemini rejects an OBJECT schema with empty properties; pass None instead.
    props = input_schema.get("properties") or {}
    if not props:
        return None
    return input_schema


class GeminiBackend:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self._client = genai.Client(
            api_key=api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self._n = 0  # synthetic call-id counter

    def _to_contents(self, messages: list[dict]) -> list[types.Content]:
        contents: list[types.Content] = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(
                    role="user", parts=[types.Part(text=m["content"])]))
            elif m["role"] == "assistant":
                parts = []
                if m.get("text"):
                    parts.append(types.Part(text=m["text"]))
                for tc in m.get("tool_calls", []):
                    # Gemini 3 thinking models require the thought_signature
                    # captured on the function_call to be replayed verbatim.
                    sig = tc.meta.get("thought_signature")
                    parts.append(types.Part(
                        function_call=types.FunctionCall(name=tc.name, args=tc.input),
                        thought_signature=sig))
                if not parts:
                    parts.append(types.Part(text="(no output)"))
                contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool":
                parts = []
                for r in m["results"]:
                    try:
                        parsed = json.loads(r.content)
                        resp = parsed if isinstance(parsed, dict) else {"result": parsed}
                    except json.JSONDecodeError:
                        resp = {"result": r.content}
                    parts.append(types.Part(function_response=types.FunctionResponse(
                        name=r.name, response=resp)))
                contents.append(types.Content(role="user", parts=parts))
        return contents

    def complete(self, system, messages, tools, max_tokens) -> Completion:
        decls = [types.FunctionDeclaration(
            name=t["name"], description=t["description"],
            parameters=_params(t["input_schema"])) for t in tools]
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)],
            temperature=1.0,
            # Thinking tokens share this budget; a chatty/thinking model can
            # otherwise hit MAX_TOKENS before emitting its function call.
            max_output_tokens=max(max_tokens, 24576),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self._to_contents(messages)
        resp = _with_backoff(lambda: self._client.models.generate_content(
            model=self.model, contents=contents, config=cfg))

        c = Completion()
        cand = resp.candidates[0] if resp.candidates else None
        if cand:
            c.stop_reason = str(cand.finish_reason or "")
            for part in (cand.content.parts or []) if cand.content else []:
                # Skip thought parts (replaying them without signatures errors);
                # the function_call signature below is what must round-trip.
                if getattr(part, "text", None) and not getattr(part, "thought", False):
                    c.text += part.text
                fc = getattr(part, "function_call", None)
                if fc:
                    self._n += 1
                    sig = getattr(part, "thought_signature", None)
                    c.tool_calls.append(ToolCall(
                        id=f"{fc.name}-{self._n}", name=fc.name,
                        input=dict(fc.args or {}),
                        meta={"thought_signature": sig} if sig is not None else {}))
        if resp.usage_metadata:
            um = resp.usage_metadata
            c.input_tokens = um.prompt_token_count or 0
            # Thinking tokens are billed as output but reported separately.
            c.output_tokens = (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
        return c
