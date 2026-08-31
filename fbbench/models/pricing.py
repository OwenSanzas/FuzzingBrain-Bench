"""Per-model API pricing -> USD cost for an episode.

Rates are USD per 1,000,000 tokens (standard tier, <=200k context where
providers tier by context length), sourced from public list prices as of
May 2026. EDIT FREELY — prices change; verify against the provider before
quoting these numbers.

cost_usd() prices prompt-caching correctly when the backend reports cache
buckets: fresh input at 1x, cache WRITE at the provider's write multiplier,
cache READ at the provider's (much cheaper) read multiplier. Reasoning/thinking
tokens are billed as output and are included. Batch discounts are not modeled.
"""
from __future__ import annotations

from fbbench.models.catalog import LOCAL_PROVIDERS, provider_for

# Per-provider prompt-cache multipliers, applied to the INPUT rate.
#   read  = cache hit  (re-reading an already-cached prefix)
#   write = cache create (the first turn a prefix is cached; a one-time surcharge
#           on the newly-written tokens; providers that auto-cache have none)
# Anthropic: documented 0.1x read / 1.25x write. OpenAI auto-caches with a ~0.1x
# read and no write surcharge. Gemini implicit cache ~0.25x read. EDIT to match
# the provider's current published cache pricing.
# DeepSeek auto-caches on disk: cache-hit input is ~0.25x the miss rate, no
# write surcharge. It reports hits under usage.prompt_cache_hit_tokens (the
# OpenAI backend maps that into the cache_read bucket).
# Open-model API providers auto-cache like DeepSeek/OpenAI (cheap read, no write
# surcharge); ollama is local (free) so its multipliers are irrelevant. Unlisted
# providers fall back to the 0.10 read / 1.0 write defaults in cost_usd().
CACHE_READ_MULT = {"anthropic": 0.10, "openai": 0.10, "gemini": 0.25,
                   "deepseek": 0.25, "dashscope": 0.25, "moonshot": 0.10,
                   "zhipu": 0.10, "openrouter": 0.10, "ollama": 0.0}
CACHE_WRITE_MULT = {"anthropic": 1.25, "openai": 1.0, "gemini": 1.0,
                    "deepseek": 1.0, "dashscope": 1.0, "moonshot": 1.0,
                    "zhipu": 1.0, "openrouter": 1.0, "ollama": 0.0}

# model_id -> (input_usd_per_mtok, output_usd_per_mtok)
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-8":   (5.0, 25.0),  # Opus tier (same as 4.7); verify list price
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    # OpenAI
    "gpt-5.5":      (5.0, 30.0),
    "gpt-5.4":      (2.5, 15.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5":        (1.25, 10.0),
    # Gemini  (3.1 Pro priced at the Gemini-3 Pro tier; estimate)
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3-pro-preview":   (2.0, 12.0),
    "gemini-3.5-flash":       (1.5, 9.0),
    "gemini-2.5-pro":         (1.25, 10.0),
    "gemini-2.5-flash":       (0.30, 2.5),
    "gemini-2.5-flash-lite":  (0.10, 0.40),
    # DeepSeek V4 (cache-MISS input rate; hits priced via CACHE_READ_MULT).
    # ESTIMATE — V4 list prices not yet pinned; verify against
    # api-docs.deepseek.com/quick_start/pricing before quoting.
    "deepseek-v4-pro":   (0.55, 2.19),
    "deepseek-v4-flash": (0.27, 1.10),
    # Qwen via DashScope. ESTIMATE — verify at help.aliyun.com/zh/model-studio
    # /models before quoting (per-1M-token, standard tier).
    "qwen3-max":         (1.20, 6.00),
    "qwen3-coder-plus":  (1.00, 5.00),
    "qwen-plus":         (0.40, 1.20),
    "qwen-turbo":        (0.05, 0.20),
    # Kimi via Moonshot. ESTIMATE — verify at platform.moonshot.cn/docs/pricing.
    "kimi-k2-0711-preview": (0.60, 2.50),
    # GLM via Zhipu. ESTIMATE — verify at open.bigmodel.cn/pricing.
    "glm-4.6":           (0.60, 2.20),
    "glm-4.5-air":       (0.20, 1.10),
    # OpenRouter prices per underlying model (+ a small routing fee); the id here
    # is illustrative. Left unpriced so cost_usd reports pricing_known=False
    # rather than a wrong number — add the exact vendor/model id to price it.
    # Local (Ollama/vLLM): no per-token cost.
    "llama3.1:8b":       (0.0, 0.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> dict:
    """Return a cost breakdown. total_usd is None when the model is unpriced.

    input_tokens is FRESH (uncached) input. cache_read/write_tokens are the
    cache-hit / cache-create buckets, priced at the provider's cache multipliers
    of the input rate. Backends that do not report caching pass 0 for both, so
    this reduces to the old flat-rate behavior.
    """
    # Local (self-hosted) models have no per-token cost regardless of the tag.
    if provider_for(model) in LOCAL_PROVIDERS:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "pricing_known": True, "local": True,
                "input_usd": 0.0, "output_usd": 0.0, "total_usd": 0.0}
    rates = PRICES.get(model)
    if rates is None:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "pricing_known": False, "total_usd": None,
                "note": f"no price for {model!r} in pricing.py — edit to add"}
    in_rate, out_rate = rates
    provider = provider_for(model)
    read_mult = CACHE_READ_MULT.get(provider, 0.10)
    write_mult = CACHE_WRITE_MULT.get(provider, 1.0)
    in_usd = input_tokens / 1e6 * in_rate
    read_usd = cache_read_tokens / 1e6 * in_rate * read_mult
    write_usd = cache_write_tokens / 1e6 * in_rate * write_mult
    out_usd = output_tokens / 1e6 * out_rate
    total = in_usd + read_usd + write_usd + out_usd
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "pricing_known": True,
        "input_usd_per_mtok": in_rate, "output_usd_per_mtok": out_rate,
        "cache_read_mult": read_mult, "cache_write_mult": write_mult,
        "input_usd": round(in_usd, 6),
        "cache_read_usd": round(read_usd, 6),
        "cache_write_usd": round(write_usd, 6),
        "output_usd": round(out_usd, 6),
        "total_usd": round(total, 6),
    }

def cost_report(model: str, *, input_tokens: int, output_tokens: int,
                cache_read_tokens: int = 0, cache_write_tokens: int = 0,
                input_is_total: bool = False, basis: str = "authoritative") -> dict:
    """The one cost record every arm writes. Same fields, same arithmetic.

    Arms disagreed on cost because each did its own accounting:
      * the CRS client reports input_tokens as the WHOLE prompt (cached
        included) -- billing that as fresh input on top of the cache-read rate
        overstated an episode ~4x. Pass input_is_total=True for such a backend.
      * Claude Code reports authoritative usage only in its `result` event; a
        session killed by the wall clock emits none, and the cell recorded
        $0.00. Pass what you have with basis="floor" so a lower bound is
        visible as a lower bound.

    `basis` travels with the number: "authoritative" | "floor" | "estimate".
    """
    fresh = input_tokens - cache_read_tokens if input_is_total else input_tokens
    fresh = max(0, fresh)
    out = cost_usd(model, fresh, output_tokens, cache_read_tokens, cache_write_tokens)
    out.update({"model": model, "basis": basis,
                "input_tokens": fresh, "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens})
    if input_is_total:
        out["input_tokens_total_reported"] = input_tokens
    return out
