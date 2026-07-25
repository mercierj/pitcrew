from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


MODEL_CATALOG = {
    "gpt-5.6-sol": {
        "profile": "quality",
        "label": "Qualité",
        "pricing": {
            "input_tokens": Decimal("5"),
            "cached_input_tokens": Decimal("0.5"),
            "cache_write_tokens": Decimal("6.25"),
            "output_tokens": Decimal("30"),
        },
    },
    "gpt-5.6-terra": {
        "profile": "balance",
        "label": "Équilibre",
        "pricing": {
            "input_tokens": Decimal("2.5"),
            "cached_input_tokens": Decimal("0.25"),
            "cache_write_tokens": Decimal("3.125"),
            "output_tokens": Decimal("15"),
        },
    },
    "gpt-5.6-luna": {
        "profile": "speed_cost",
        "label": "Rapidité/coût",
        "pricing": {
            "input_tokens": Decimal("1"),
            "cached_input_tokens": Decimal("0.1"),
            "cache_write_tokens": Decimal("1.25"),
            "output_tokens": Decimal("6"),
        },
    },
}

DEFAULT_MODELS = {
    "research-run": "gpt-5.6-terra",
    "manager-run": "gpt-5.6-luna",
    "implementer-run": "gpt-5.6-sol",
    "reviewer-run": "gpt-5.6-sol",
    "validator-run": "gpt-5.6-terra",
    "investigate-run": "gpt-5.6-sol",
    "stale-sweep": "gpt-5.6-luna",
    "qa-run": "gpt-5.6-terra",
    "coverage-run": "gpt-5.6-terra",
    "dev-verify-run": "gpt-5.6-terra",
    "ops-run": "gpt-5.6-luna",
    "unblock": "gpt-5.6-sol",
    "releaser-run": "gpt-5.6-terra",
}

PRICING_EFFECTIVE_DATE = "2026-07-24"
PRICING_CURRENCY = "USD"
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)


def resolve_model(config: Mapping, skill: str) -> str:
    if skill not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {skill}")
    agents = config.get("agents", {})
    entry = agents.get(skill, {}) if isinstance(agents, Mapping) else {}
    return entry.get("model", DEFAULT_MODELS[skill])


def estimate_cost(model: str, usage: Mapping[str, int]) -> Decimal:
    prices = MODEL_CATALOG[model]["pricing"]
    return sum(
        Decimal(usage.get(field, 0)) * prices[field] / Decimal(1_000_000)
        for field in prices
    )
