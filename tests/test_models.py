"""Model catalog, routing, and pricing coherence."""
from fbbench.models import (
    CATALOG, PRICES, PROVIDER_DEFAULT, PROVIDER_KEY_ENV, SUPPORTED_MODELS,
    default_sweep, provider_for, route_provider,
)


def test_routing():
    assert provider_for("claude-opus-4-7") == "anthropic"
    assert provider_for("gpt-5.5") == "openai"
    assert provider_for("gemini-3-pro-preview") == "gemini"
    assert route_provider("nonsense-model") == "unknown"


def test_every_catalog_model_is_priced():
    for m, _, _ in CATALOG:
        assert m in PRICES, f"{m} missing from PRICES"


def test_provider_defaults_route_back():
    for prov, model in PROVIDER_DEFAULT.items():
        assert model in SUPPORTED_MODELS
        assert provider_for(model) == prov


def test_default_sweep_routes_to_known_providers():
    for m in default_sweep():
        assert provider_for(m) in PROVIDER_KEY_ENV
