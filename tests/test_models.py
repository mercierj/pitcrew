import unittest
from decimal import Decimal

from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    MODEL_CATALOG,
    estimate_cost,
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
