"""Model -> provider routing + the default sweep set.

Backends accept any model id the provider exposes; this registry only routes
an id to its provider and lists the curated default lineup for a full sweep.
"""
from __future__ import annotations


def provider_for(model_id: str) -> str:
    """Route a model id to its provider by prefix."""
    m = model_id.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "gpt5", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith(("gemini", "gemma")):
        return "gemini"
    raise ValueError(
        f"cannot route model id {model_id!r} to a provider "
        "(expected claude*/gpt*/o3*/o4*/gemini*/gemma*)"
    )


# Curated default sweep — flagship / mid / fast per provider. Any other id the
# provider lists is still runnable by passing it to --model directly.
DEFAULT_SWEEP: dict[str, list[str]] = {
    "anthropic": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "openai":    ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "gemini":    ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-2.5-flash"],
}


def all_sweep_models() -> list[str]:
    return [m for models in DEFAULT_SWEEP.values() for m in models]
