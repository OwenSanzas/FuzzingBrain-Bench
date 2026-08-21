"""Anthropic backend: neutral history <-> messages.create() tool-use blocks."""
from __future__ import annotations

import os
import re

import anthropic

from .base import Completion, ToolCall

# Anthropic rejects max_tokens above a model's per-model output ceiling with a
# 400 (e.g. Haiku caps at 64000, below episode.py's generous 65536 default).
# Rather than hardcode every model's limit, learn it from the error the first
# time we trip it and cache it for the rest of the episode.
_MAX_TOKENS_LIMIT_RE = re.compile(r"max_tokens:\s*\d+\s*>\s*(\d+)")


class AnthropicBackend:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        # max_retries higher than the SDK default (2) so rate-limit (429/529)
        # waves recover inside the episode instead of failing it.
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            max_retries=8)
        # Discovered per-model output ceiling (None until a 400 reveals it).
        self._max_tokens_cap: int | None = None

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
                # Budget note rides in the SAME user message as the tool results
                # (keeps tool_use<->tool_result adjacency intact).
                if m.get("note"):
                    content.append({"type": "text", "text": m["note"]})
                out.append({"role": "user", "content": content})
        return out

    @staticmethod
    def _with_cache(system: str, api_tools: list[dict], blocks: list[dict]):
        """Attach prompt-cache breakpoints to maximize prefix reuse.

        Three ephemeral breakpoints (within Anthropic's limit of 4): the tools
        array, the system prompt, and the LAST content block of the LAST message.
        History is append-only, so each turn the previous prefix (up to the prior
        last message) is a cache READ and only the new turn is written. system +
        tools are static, so after turn 1 they are always read-hits.
        """
        cc = {"type": "ephemeral"}
        # system: turn the plain string into a cacheable text block.
        sys_param = ([{"type": "text", "text": system, "cache_control": cc}]
                     if system else system)
        # tools: mark the last tool (caches the whole tools array before it).
        tools_param = list(api_tools)
        if tools_param:
            tools_param[-1] = {**tools_param[-1], "cache_control": cc}
        # messages: mark the last block of the last message.
        if blocks:
            last = blocks[-1]
            content = last["content"]
            if isinstance(content, str):
                last["content"] = [{"type": "text", "text": content,
                                    "cache_control": cc}]
            elif content:
                content[-1] = {**content[-1], "cache_control": cc}
        return sys_param, tools_param, blocks

    def _stream_once(self, system, api_tools, blocks, max_tokens):
        # No `temperature`. It used to be passed as 1.0, which is the API's own
        # default, so nothing about sampling changes by dropping it -- but the
        # parameter itself is gone. Anthropic removed the sampling knobs
        # (temperature / top_p / top_k) on the current models, and the SDK
        # followed by dropping them from the method signature, so a client new
        # enough to have that signature raised TypeError before a request was
        # ever sent: "Messages.stream() got an unexpected keyword argument
        # 'temperature'". Every cell died the same way, on any model.
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=api_tools,
            messages=blocks,
            metadata={"user_id": "fbbench"},
        ) as stream:
            return stream.get_final_message()

    def complete(self, system, messages, tools, max_tokens) -> Completion:
        api_tools = [{"name": t["name"], "description": t["description"],
                      "input_schema": t["input_schema"]} for t in tools]
        blocks = self._to_blocks(messages)
        system, api_tools, blocks = self._with_cache(system, api_tools, blocks)
        if self._max_tokens_cap is not None:
            max_tokens = min(max_tokens, self._max_tokens_cap)
        try:
            resp = self._stream_once(system, api_tools, blocks, max_tokens)
        except anthropic.BadRequestError as e:
            m = _MAX_TOKENS_LIMIT_RE.search(str(getattr(e, "message", "") or e))
            cap = int(m.group(1)) if m else None
            if cap is None or cap >= max_tokens:
                raise  # not a max_tokens-ceiling error, or no headroom to gain
            self._max_tokens_cap = cap
            resp = self._stream_once(system, api_tools, blocks, cap)
        c = Completion(stop_reason=resp.stop_reason or "")
        for block in resp.content:
            if block.type == "text":
                c.text += block.text
            elif block.type == "tool_use":
                c.tool_calls.append(ToolCall(id=block.id, name=block.name,
                                             input=block.input or {}))
        if resp.usage:
            # input_tokens already EXCLUDES cached tokens on Anthropic.
            c.input_tokens = resp.usage.input_tokens or 0
            c.output_tokens = resp.usage.output_tokens or 0
            c.cache_write_tokens = getattr(
                resp.usage, "cache_creation_input_tokens", 0) or 0
            c.cache_read_tokens = getattr(
                resp.usage, "cache_read_input_tokens", 0) or 0
        return c
