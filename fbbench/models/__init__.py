"""Model knowledge: catalog, provider routing, and pricing — single source."""
from fbbench.models.catalog import (
    CATALOG,
    PROVIDER_DEFAULT,
    PROVIDER_KEY_ENV,
    PROVIDERS,
    SUPPORTED_MODELS,
    default_sweep,
    provider_for,
    route_provider,
)
from fbbench.models.pricing import PRICES, cost_usd

__all__ = [
    "CATALOG", "PROVIDERS", "SUPPORTED_MODELS",
    "PROVIDER_KEY_ENV", "PROVIDER_DEFAULT",
    "provider_for", "route_provider", "default_sweep",
    "PRICES", "cost_usd",
]
