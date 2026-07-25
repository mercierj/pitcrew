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
                "research-run",
                "manager-run",
                "implementer-run",
                "reviewer-run",
                "validator-run",
                "investigate-run",
                "stale-sweep",
                "qa-run",
                "coverage-run",
                "dev-verify-run",
                "ops-run",
                "unblock",
                "releaser-run",
            },
            set(DEFAULT_MODELS),
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

    def test_cost_uses_all_four_token_categories(self):
        usage = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "total_tokens": 4_000_000,
        }
        self.assertEqual(Decimal("20.875"), estimate_cost("gpt-5.6-terra", usage))
