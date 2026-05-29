"""Anthropic backend: neutral history <-> messages.create() tool-use blocks."""
from __future__ import annotations

import os

import anthropic

from .base import Completion, ToolCall


class AnthropicBackend:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def _to_blocks(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id,
                                   "name": tc.name, "input": tc.input})
                # Anthropic requires non-empty content.
                if not blocks:
                    blocks.append({"type": "text", "text": "(no output)"})
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                content = [{"type": "tool_result", "tool_use_id": r.id,
                            "content": r.content, "is_error": r.is_error}
                           for r in m["results"]]
                out.append({"role": "user", "content": content})
        return out

    def complete(self, system, messages, tools, max_tokens) -> Completion:
        api_tools = [{"name": t["name"], "description": t["description"],
                      "input_schema": t["input_schema"]} for t in tools]
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=api_tools,
            messages=self._to_blocks(messages),
            temperature=1.0,
            metadata={"user_id": "fbbench"},
        ) as stream:
            resp = stream.get_final_message()
        c = Completion(stop_reason=resp.stop_reason or "")
        for block in resp.content:
            if block.type == "text":
                c.text += block.text
            elif block.type == "tool_use":
                c.tool_calls.append(ToolCall(id=block.id, name=block.name,
                                             input=block.input or {}))
        if resp.usage:
            c.input_tokens = resp.usage.input_tokens or 0
            c.output_tokens = resp.usage.output_tokens or 0
        return c
