"""Model catalog + provider routing.

`provider_for` routes ANY model id to a provider by prefix, so you can pass a
model the catalog doesn't list. CATALOG is the curated, supported lineup for
this version (priced in pricing.py and smoke-tested through its backend).
"""
from __future__ import annotations

# (model_id, provider, tier). tier is a coarse cost/capability band used to
# build affordable default sweeps. Order is flagship -> fast within a provider.
CATALOG: list[tuple[str, str, str]] = [
    # Anthropic
    ("claude-opus-4-7",          "anthropic", "flagship"),
    ("claude-sonnet-4-6",        "anthropic", "mid"),
    ("claude-haiku-4-5",         "anthropic", "fast"),
    # OpenAI
    ("gpt-5.5",                  "openai",    "flagship"),
    ("gpt-5.4",                  "openai",    "mid"),
    ("gpt-5",                    "openai",    "mid"),
    ("gpt-5.4-mini",             "openai",    "fast"),
    # Gemini
    ("gemini-3.1-pro-preview",   "gemini",    "flagship"),
    ("gemini-3-pro-preview",     "gemini",    "flagship"),
    ("gemini-3.5-flash",         "gemini",    "mid"),
    ("gemini-2.5-pro",           "gemini",    "mid"),
    ("gemini-2.5-flash",         "gemini",    "fast"),
    ("gemini-2.5-flash-lite",    "gemini",    "fast"),
]

SUPPORTED_MODELS = [m for m, _, _ in CATALOG]
PROVIDERS = ("anthropic", "openai", "gemini")


def provider_for(model_id: str) -> str:
    """Route a model id to its provider by prefix (works for any id)."""
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


def default_sweep() -> list[str]:
    """One flagship + one fast per provider — an affordable spread."""
    out: list[str] = []
    for provider in PROVIDERS:
        for tier in ("flagship", "fast"):
            for m, p, t in CATALOG:
                if p == provider and t == tier:
                    out.append(m)
                    break
    return out
