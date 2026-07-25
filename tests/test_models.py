import unittest
from decimal import Decimal

from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    MODEL_CATALOG,
    aggregate_usage,
    empty_usage,
    estimate_cost,
    public_catalog,
    resolve_model,
)


class ModelCatalogTest(unittest.TestCase):
    def test_every_role_has_a_supported_default(self):
        self.assertEqual(
            {
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
            },
            DEFAULT_MODELS,
        )
        self.assertTrue(set(DEFAULT_MODELS.values()) <= set(MODEL_CATALOG))

    def test_override_wins_and_missing_override_uses_default(self):
        self.assertEqual(
            "gpt-5.6-luna",
            resolve_model(
                {"agents": {"research-run": {"model": "gpt-5.6-luna"}}},
                "research-run",
            ),
        )
        self.assertEqual("gpt-5.6-sol", resolve_model({}, "implementer-run"))

    def test_unknown_role_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown role: unknown-run"):
            resolve_model({}, "unknown-run")

    def test_cost_uses_all_four_token_categories(self):
        usage = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "total_tokens": 4_000_000,
        }
        self.assertEqual(Decimal("20.875"), estimate_cost("gpt-5.6-terra", usage))

    def test_aggregate_usage_keeps_legacy_records_unmeasured(self):
        aggregated = aggregate_usage(
            [
                {
                    "model": "gpt-5.6-luna",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "cache_write_tokens": 0,
                        "output_tokens": 10,
                        "total_tokens": 130,
                    },
                },
                {"skill": "legacy-run"},
            ]
        )

        self.assertEqual(1, aggregated["measured_runs"])
        self.assertEqual(1, aggregated["unmeasured_runs"])
        self.assertEqual(100, aggregated["tokens"]["input_tokens"])
        self.assertEqual(130, aggregated["tokens"]["total_tokens"])
        self.assertEqual("0.000162", aggregated["estimated_cost_usd"])

    def test_aggregate_usage_prices_each_record_by_its_own_model(self):
        aggregated = aggregate_usage(
            [
                {
                    "model": "gpt-5.6-luna",
                    "usage": {
                        "input_tokens": 1,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 1,
                    },
                },
                {
                    "model": "gpt-5.6-sol",
                    "usage": {
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 1,
                        "total_tokens": 1,
                    },
                },
                {"model": "unknown", "usage": {}},
            ]
        )

        self.assertEqual(2, aggregated["measured_runs"])
        self.assertEqual(1, aggregated["unmeasured_runs"])
        self.assertEqual("0.000031", aggregated["estimated_cost_usd"])

    def test_aggregate_usage_treats_unhashable_models_as_unmeasured(self):
        aggregated = aggregate_usage(
            [
                {"model": [], "usage": {}},
                {"model": {}, "usage": {}},
            ]
        )

        self.assertEqual(0, aggregated["measured_runs"])
        self.assertEqual(2, aggregated["unmeasured_runs"])

    def test_aggregate_usage_handles_very_large_valid_token_counts(self):
        tokens = 10**100
        aggregated = aggregate_usage(
            [
                {
                    "model": "gpt-5.6-luna",
                    "usage": {
                        "input_tokens": tokens,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": tokens,
                    },
                }
            ]
        )

        self.assertEqual(1, aggregated["measured_runs"])
        self.assertEqual(tokens, aggregated["tokens"]["total_tokens"])
        self.assertEqual(f"{10**94}.000000", aggregated["estimated_cost_usd"])

    def test_empty_aggregate_has_fixed_zeroes_and_safe_catalog(self):
        self.assertEqual(
            {
                "measured_runs": 0,
                "unmeasured_runs": 0,
                "tokens": empty_usage(),
                "estimated_cost_usd": "0.000000",
            },
            aggregate_usage([]),
        )
        self.assertEqual(
            set(MODEL_CATALOG),
            set(public_catalog()),
        )
        self.assertTrue(
            all(set(entry) == {"profile", "label"} for entry in public_catalog().values())
        )
