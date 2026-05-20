"""OpenAI backend: neutral history <-> chat.completions tool calls.

Handles the gpt-5.x / o-series reasoning models, which require
`max_completion_tokens` (not `max_tokens`) and reject a custom temperature.
"""
from __future__ import annotations

import json
import os

import openai

from .base import Completion, ToolCall


def _is_reasoning(model: str) -> bool:
    m = model.lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


class OpenAIBackend:
    def __init__(self, model: str, api_key: str | None = None, seed: int = 0):
        self.model = model
        self.seed = seed
        self._client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("text") or None}
                tcs = m.get("tool_calls", [])
                if tcs:
                    msg["tool_calls"] = [{
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                    } for tc in tcs]
                out.append(msg)
            elif m["role"] == "tool":
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r.id, "content": r.content})
        return out

    def complete(self, system, messages, tools, max_tokens) -> Completion:
        api_tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["input_schema"]}} for t in tools]
        kwargs: dict = {
            "model": self.model,
            "messages": self._to_messages(system, messages),
            "tools": api_tools,
        }
        if _is_reasoning(self.model):
            # Reasoning tokens count against the completion budget; give room.
            kwargs["max_completion_tokens"] = max(max_tokens, 16384)
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = 1.0
        resp = self._client.chat.completions.create(**kwargs)

        msg = resp.choices[0].message
        c = Completion(text=msg.content or "",
                       stop_reason=resp.choices[0].finish_reason or "")
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            c.tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
        if resp.usage:
            c.input_tokens = resp.usage.prompt_tokens or 0
            c.output_tokens = resp.usage.completion_tokens or 0
        return c
