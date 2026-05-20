"""Backend factory: model id -> a provider Backend instance."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from registry import provider_for  # noqa: E402

from .base import Backend  # noqa: E402,F401


def make_backend(model: str, api_key: str | None = None, seed: int = 0) -> Backend:
    provider = provider_for(model)
    if provider == "anthropic":
        from .anthropic_backend import AnthropicBackend
        return AnthropicBackend(model, api_key=api_key, seed=seed)
    if provider == "openai":
        from .openai_backend import OpenAIBackend
        return OpenAIBackend(model, api_key=api_key, seed=seed)
    if provider == "gemini":
        from .gemini_backend import GeminiBackend
        return GeminiBackend(model, api_key=api_key, seed=seed)
    raise ValueError(f"unknown provider for model {model!r}")
