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
    # DeepSeek (OpenAI-compatible endpoint at https://api.deepseek.com).
    # V4 hybrid-reasoning lineup; both emit reasoning_content (billed as output).
    ("deepseek-v4-pro",          "deepseek",  "flagship"),
    ("deepseek-v4-flash",        "deepseek",  "fast"),
    # Qwen via Alibaba DashScope's OpenAI-compatible endpoint. IDs are the
    # DashScope model names; any qwen*/qwq* id routes here (verify the exact id
    # against dashscope.console.aliyun.com — these change).
    ("qwen3-max",                "dashscope", "flagship"),
    ("qwen3-coder-plus",         "dashscope", "flagship"),
    ("qwen-plus",                "dashscope", "mid"),
    ("qwen-turbo",               "dashscope", "fast"),
    # Kimi via Moonshot's OpenAI-compatible endpoint (api.moonshot.cn).
    ("kimi-k2-0711-preview",     "moonshot",  "flagship"),
    # GLM via Zhipu's OpenAI-compatible endpoint (open.bigmodel.cn).
    ("glm-4.6",                  "zhipu",     "flagship"),
    ("glm-4.5-air",              "zhipu",     "fast"),
    # OpenRouter: one key, many open models. IDs use the vendor/model form and
    # are routed here by the "/" in the id. Listed example; any vendor/model works.
    ("qwen/qwen3-coder",         "openrouter", "flagship"),
    # Local open models served by Ollama/vLLM over an OpenAI-compatible endpoint
    # (OLLAMA_BASE_URL, default http://localhost:11434/v1). No API key needed.
    # ollama tag ids carry a ":". qwen3:30b-a3b is a 30B/3B-active MoE — the best
    # CPU tradeoff (runs at ~3B speed, tool-calling capable). llama3.1:8b is a
    # small dense fallback. Both verified working over the local ollama endpoint.
    ("qwen3:30b-a3b",            "ollama",    "flagship"),
    ("llama3.1:8b",              "ollama",    "fast"),
]

SUPPORTED_MODELS = [m for m, _, _ in CATALOG]

# Input context window (max prompt tokens) per model, used by the runner's
# conversation-compaction trigger. Values are filled from the published specs
# (see tools/model_context_windows.csv). Any model NOT listed here — including
# ids passed that the catalog doesn't enumerate — falls back to a conservative
# DEFAULT_CONTEXT_WINDOW so the trigger still works (and never over-estimates).
DEFAULT_CONTEXT_WINDOW = 128_000
CONTEXT_WINDOWS: dict[str, int] = {
    # TODO(fill from tools/model_context_windows.csv): { "claude-opus-4-7": 200_000, ... }
}


def context_window(model: str) -> int:
    """Max input tokens for `model`; DEFAULT_CONTEXT_WINDOW if unknown."""
    return CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)
PROVIDERS = ("anthropic", "openai", "gemini", "deepseek",
             "dashscope", "moonshot", "zhipu", "openrouter", "ollama")

# Providers served over an OpenAI-compatible endpoint with a base_url override.
# provider -> (env var for base_url override, default base_url, env var for key).
OPENAI_COMPAT = {
    "deepseek":   ("DEEPSEEK_BASE_URL",   "https://api.deepseek.com",                         "DEEPSEEK_API_KEY"),
    "dashscope":  ("DASHSCOPE_BASE_URL",  "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "moonshot":   ("MOONSHOT_BASE_URL",   "https://api.moonshot.cn/v1",                        "MOONSHOT_API_KEY"),
    "zhipu":      ("ZHIPU_BASE_URL",      "https://open.bigmodel.cn/api/paas/v4",              "ZHIPU_API_KEY"),
    "openrouter": ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1",                      "OPENROUTER_API_KEY"),
}

# Local (self-hosted) providers need no cloud API key.
LOCAL_PROVIDERS = ("ollama",)

# Env var holding each provider's API key. Local providers point at their
# base-url knob instead of a secret (they are keyless).
PROVIDER_KEY_ENV = {
    "anthropic":  "ANTHROPIC_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "gemini":     "GEMINI_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "dashscope":  "DASHSCOPE_API_KEY",
    "moonshot":   "MOONSHOT_API_KEY",
    "zhipu":      "ZHIPU_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama":     "OLLAMA_BASE_URL",
}

# Default model per provider, chosen when the user did not pass --model:
# the cheapest flagship/mid tier per provider — a sane "just works" start.
PROVIDER_DEFAULT = {
    "anthropic":  "claude-opus-4-7",
    "openai":     "gpt-5.5",
    "gemini":     "gemini-3-pro-preview",
    "deepseek":   "deepseek-v4-flash",
    "dashscope":  "qwen3-coder-plus",
    "moonshot":   "kimi-k2-0711-preview",
    "zhipu":      "glm-4.6",
    "openrouter": "qwen/qwen3-coder",
    "ollama":     "llama3.1:8b",
}


def needs_key(provider: str) -> bool:
    """True if the provider requires a cloud API key (False for local)."""
    return provider not in LOCAL_PROVIDERS


def provider_for(model_id: str) -> str:
    """Route a model id to its provider by prefix (works for any id).

    Disambiguation order matters:
      * an id with a "/" is an OpenRouter vendor/model id (e.g. qwen/qwen3-coder)
      * an id with a ":" is an Ollama local tag (e.g. llama3.1:8b, qwen2.5:7b)
    Both are checked before the bare-name prefixes below, so a native Qwen id
    (`qwen3-max`) routes to DashScope while a local tag (`qwen2.5:7b`) stays local.
    """
    m = model_id.lower()
    # OpenRouter uses the vendor/model convention; a "/" is the tell.
    if "/" in m:
        return "openrouter"
    # Ollama local tags carry a ":" (name:tag). Route to the keyless local path.
    if ":" in m:
        return "ollama"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "gpt5", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith(("gemini", "gemma")):
        return "gemini"
    # Each of these is served via its own OpenAI-compatible endpoint, routed
    # through the OpenAI backend with a base_url + key override
    # (see OPENAI_COMPAT and runner/backends/__init__.py).
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("qwen", "qwq")):      # Alibaba DashScope
        return "dashscope"
    if m.startswith(("kimi", "moonshot")):  # Moonshot
        return "moonshot"
    if m.startswith("glm"):                # Zhipu
        return "zhipu"
    # Bare local open models served by Ollama/vLLM (no ":" tag, e.g. "llama3").
    if m.startswith(("llama", "codellama", "ollama")):
        return "ollama"
    raise ValueError(
        f"cannot route model id {model_id!r} to a provider (expected "
        "claude*/gpt*/o3*/o4*/gemini*/gemma*/deepseek*/qwen*/qwq*/kimi*/glm*/"
        "llama*, a vendor/model OpenRouter id, or a name:tag Ollama id)"
    )


def route_provider(model_id: str) -> str:
    """Like provider_for but returns 'unknown' instead of raising."""
    try:
        return provider_for(model_id)
    except ValueError:
        return "unknown"


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
