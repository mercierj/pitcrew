import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/pitcrew_preflight.py"


class PreflightTest(unittest.TestCase):
    def run_helper(self, *args, env):
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_allows_run_without_circuit_breaker(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            result = self.run_helper("check", "--project", "getbill", "--skill", "research-run", env=env)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("run", json.loads(result.stdout)["decision"])

    def test_check_blocks_active_provider_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            failed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            record = self.run_helper(
                "record-provider-failure",
                "--project", "getbill",
                "--reason", "GitLab authentication unavailable",
                "--at", failed_at,
                env=env,
            )
            self.assertEqual(0, record.returncode, record.stderr)
            result = self.run_helper("check", "--project", "getbill", "--skill", "reviewer-run", env=env)
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("noop", payload["decision"])
            self.assertIn("cooldown", payload["reason"])

    def test_expired_provider_cooldown_allows_run(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            failed_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            record = self.run_helper(
                "record-provider-failure",
                "--project", "getbill",
                "--reason", "GitLab authentication unavailable",
                "--at", failed_at,
                env=env,
            )
            self.assertEqual(0, record.returncode, record.stderr)
            result = self.run_helper("check", "--project", "getbill", "--skill", "reviewer-run", env=env)
            self.assertEqual("run", json.loads(result.stdout)["decision"])

    def test_noop_does_not_block_the_next_scheduled_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            record = self.run_helper(
                "record-noop", "--project", "getbill", "--skill", "implementer-run",
                "--reason", "no eligible item", env=env,
            )
            self.assertEqual(0, record.returncode, record.stderr)
            next_pass = self.run_helper("check", "--project", "getbill", "--skill", "implementer-run", env=env)
            self.assertEqual("run", json.loads(next_pass.stdout)["decision"])

    def test_record_gate_writes_no_model_history(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}

            result = self.run_helper(
                "record-gate",
                "--project", "getbill",
                "--skill", "reviewer-run",
                "--decision", "empty",
                "--reason", "no eligible item",
                "--fingerprint", "sha256:abc",
                env=env,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("noop", json.loads(result.stdout)["status"])
            history = Path(temp) / "pitcrew/getbill/history.jsonl"
            record = json.loads(
                history.read_text(encoding="utf-8").splitlines()[-1]
            )
            self.assertFalse(record["model_invoked"])
            self.assertEqual("empty", record["gate_decision"])
            self.assertEqual("sha256:abc", record["fingerprint"])


if __name__ == "__main__":
    unittest.main()
