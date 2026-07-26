from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext


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
    "architecture-run": "gpt-5.6-sol",
    "preprod-review-run": "gpt-5.6-sol",
    "security-run": "gpt-5.6-sol",
    "product-discovery-run": "gpt-5.6-terra",
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

REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
ROUTING_MODES = {"fixed", "observe"}

DEFAULT_REASONING_EFFORTS = {
    "architecture-run": "high",
    "preprod-review-run": "xhigh",
    "security-run": "high",
    "product-discovery-run": "medium",
    "research-run": "medium",
    "manager-run": "low",
    "implementer-run": "high",
    "reviewer-run": "high",
    "validator-run": "medium",
    "investigate-run": "high",
    "stale-sweep": "low",
    "qa-run": "medium",
    "coverage-run": "medium",
    "dev-verify-run": "medium",
    "ops-run": "low",
    "unblock": "medium",
    "releaser-run": "medium",
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


def _agent_entry(config: Mapping, skill: str) -> Mapping:
    if skill not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {skill}")
    agents = config.get("agents", {})
    if not isinstance(agents, Mapping):
        return {}
    entry = agents.get(skill, {})
    return entry if isinstance(entry, Mapping) else {}


def resolve_model(config: Mapping, skill: str) -> str:
    entry = _agent_entry(config, skill)
    return entry.get("model", DEFAULT_MODELS[skill])


def resolve_reasoning_effort(config: Mapping, skill: str) -> str:
    entry = _agent_entry(config, skill)
    return entry.get("reasoning_effort", DEFAULT_REASONING_EFFORTS[skill])


def resolve_routing_mode(config: Mapping, skill: str) -> str:
    entry = _agent_entry(config, skill)
    return entry.get("routing_mode", "observe")


def estimate_cost(model: str, usage: Mapping[str, int]) -> Decimal:
    prices = MODEL_CATALOG[model]["pricing"]
    with localcontext() as context:
        context.prec = _cost_precision((usage,))
        return sum(
            Decimal(usage.get(field, 0)) * prices[field] / Decimal(1_000_000)
            for field in prices
        )


def empty_usage() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def _measured_usage(record: object) -> tuple[str, Mapping[str, int]] | None:
    if not isinstance(record, Mapping):
        return None
    model = record.get("model")
    usage = record.get("usage")
    if (
        not isinstance(model, str)
        or model not in MODEL_CATALOG
        or not isinstance(usage, Mapping)
    ):
        return None
    if set(usage) != set(USAGE_FIELDS) or any(
        isinstance(usage[field], bool)
        or not isinstance(usage[field], int)
        or usage[field] < 0
        for field in USAGE_FIELDS
    ):
        return None
    return model, usage


def _cost_precision(usages: tuple[Mapping[str, int], ...]) -> int:
    token_digits = max(
        (
            1,
            *(
                Decimal(usage.get(field, 0)).adjusted() + 1
                for usage in usages
                for field in USAGE_FIELDS
            ),
        ),
    )
    price_digits = max(
        len(price.as_tuple().digits)
        for details in MODEL_CATALOG.values()
        for price in details["pricing"].values()
    )
    return max(
        28,
        token_digits + price_digits + Decimal(len(usages)).adjusted() + 13,
    )


def aggregate_usage(records: list[object]) -> dict:
    tokens = empty_usage()
    measured_runs = 0
    unmeasured_runs = 0
    measured_records = []
    for record in records:
        measured = _measured_usage(record)
        if measured is None:
            unmeasured_runs += 1
            continue
        model, usage = measured
        measured_records.append((model, usage))
        measured_runs += 1
        for field in USAGE_FIELDS:
            tokens[field] += usage[field]
    with localcontext() as context:
        context.prec = _cost_precision(tuple(usage for _, usage in measured_records))
        cost = sum(
            (estimate_cost(model, usage) for model, usage in measured_records),
            Decimal(0),
        )
        estimated_cost = str(cost.quantize(Decimal("0.000001")))
    return {
        "measured_runs": measured_runs,
        "unmeasured_runs": unmeasured_runs,
        "tokens": tokens,
        "estimated_cost_usd": estimated_cost,
    }


def public_catalog() -> dict[str, dict[str, str]]:
    return {
        model: {field: details[field] for field in ("profile", "label")}
        for model, details in MODEL_CATALOG.items()
    }
