"""Backend factory: model id -> a provider Backend instance."""
from __future__ import annotations

from fbbench.models import provider_for

from .base import Backend  # noqa: F401


def make_backend(model: str, api_key: str | None = None) -> Backend:
    provider = provider_for(model)
    if provider == "anthropic":
        from .anthropic_backend import AnthropicBackend
        return AnthropicBackend(model, api_key=api_key)
    if provider == "openai":
        from .openai_backend import OpenAIBackend
        return OpenAIBackend(model, api_key=api_key)
    if provider == "gemini":
        from .gemini_backend import GeminiBackend
        return GeminiBackend(model, api_key=api_key)
    raise ValueError(f"unknown provider for model {model!r}")
