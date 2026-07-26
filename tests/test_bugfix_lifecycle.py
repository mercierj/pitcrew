import json
import unittest
from pathlib import Path

from scripts.pitcrew_bugfix_lifecycle import LifecycleEvidenceError, decide


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/bugfixer"
REQUIRED_SCENARIOS = {
    "ordinary_red_to_green",
    "unreproducible",
    "sensitive_routing",
    "review_or_validator_rejection",
    "all_gates_current_head",
}


class BugfixLifecycleParityTest(unittest.TestCase):
    def load(self, provider: str) -> list[dict]:
        return json.loads((FIXTURES / f"{provider}.json").read_text())

    def test_required_scenarios_have_provider_parity(self):
        decisions = {}
        for provider in ("github", "gitlab"):
            cases = self.load(provider)
            names = [case["name"] for case in cases]
            self.assertEqual(REQUIRED_SCENARIOS, set(names))
            self.assertEqual(len(REQUIRED_SCENARIOS), len(names))
            decisions[provider] = {
                case["name"]: decide(case["snapshot"])
                for case in cases
            }
            self.assertEqual(
                {case["name"]: case["expected"] for case in cases},
                decisions[provider],
            )
        self.assertEqual(decisions["github"], decisions["gitlab"])

    def test_current_head_mismatch_never_merges(self):
        snapshot = self.load("github")[-1]["snapshot"]
        snapshot["review"]["validator_head_sha"] = "stale"
        self.assertEqual("hold_review", decide(snapshot))

    def test_incomplete_sensitive_approval_routes_to_investigation(self):
        snapshot = self.load("gitlab")[2]["snapshot"]
        snapshot["sensitive"]["approval_marker"] = False
        self.assertEqual("route_investigate", decide(snapshot))

    def test_forged_red_green_without_attempt_is_held(self):
        case = json.loads((FIXTURES / "negative.json").read_text())["attempted_false"]
        self.assertEqual(case["expected"], decide(case["snapshot"]))

    def test_prereproduction_decision_allows_the_bound_initial_claim(self):
        snapshot = self.load("github")[0]["snapshot"]
        snapshot["reproduction"] = {
            "attempted": False,
            "valid_red": False,
            "green": False,
            "same_command": False,
        }

        self.assertEqual("hold_fix", decide(snapshot))

    def test_malformed_nested_evidence_raises_typed_error(self):
        data = json.loads((FIXTURES / "negative.json").read_text())
        for case in data["malformed"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(LifecycleEvidenceError):
                    decide(case["snapshot"])
