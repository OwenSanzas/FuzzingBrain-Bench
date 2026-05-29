"""Per-model API pricing -> USD cost for an episode.

Rates are USD per 1,000,000 tokens (standard tier, <=200k context where
providers tier by context length), sourced from public list prices as of
May 2026. EDIT FREELY — prices change; verify against the provider before
quoting these numbers.

Caveat: cost is computed on total input/output tokens with flat rates. It
does NOT model prompt-caching discounts (cached input ~10-20% of rate) or
batch discounts (~50%), so it is a conservative upper bound for cache-heavy
runs. Reasoning/thinking tokens are billed as output and are included.
"""
from __future__ import annotations

# model_id -> (input_usd_per_mtok, output_usd_per_mtok)
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
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
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> dict:
    """Return a cost breakdown. total_usd is None when the model is unpriced."""
    rates = PRICES.get(model)
    if rates is None:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens,
                "pricing_known": False, "total_usd": None,
                "note": f"no price for {model!r} in pricing.py — edit to add"}
    in_rate, out_rate = rates
    in_usd = input_tokens / 1e6 * in_rate
    out_usd = output_tokens / 1e6 * out_rate
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "pricing_known": True,
        "input_usd_per_mtok": in_rate, "output_usd_per_mtok": out_rate,
        "input_usd": round(in_usd, 6), "output_usd": round(out_usd, 6),
        "total_usd": round(in_usd + out_usd, 6),
    }
