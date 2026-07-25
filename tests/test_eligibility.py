from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.pitcrew_eligibility import decide, default_provider_run, main


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pitcrew_eligibility.py"
EXPECTED_KEYS = {
    "decision",
    "project",
    "skill",
    "target_id",
    "fingerprint",
    "reason",
}


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self):
        return json.loads(
            (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def provider_json(value):
        def run(command):
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

        return run

    def test_provider_nonzero_is_unavailable_without_exposing_stderr(self):
        decision = decide(
            self.config(),
            "implementer-run",
            runtime_dir=self.runtime,
            provider_run=lambda command: subprocess.CompletedProcess(
                command, 1, "", "secret provider diagnostic"
            ),
        )

        self.assertEqual("unavailable", decision["decision"])
        self.assertIsNone(decision["target_id"])
        self.assertIsNone(decision["fingerprint"])
        self.assertNotIn("secret provider diagnostic", decision["reason"])
        self.assertLessEqual(len(decision["reason"]), 120)

    def test_provider_malformed_json_is_unavailable(self):
        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=lambda command: subprocess.CompletedProcess(
                command, 0, "{not-json", ""
            ),
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_provider_non_list_json_is_unavailable(self):
        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json({"iid": 5}),
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_provider_non_object_list_item_is_unavailable(self):
        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([{"iid": 5}, "malformed"]),
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_issue_with_invalid_iid_is_unavailable(self):
        decision = decide(
            self.config(),
            "implementer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(
                [
                    {
                        "iid": "5",
                        "labels": [
                            "pitcrew-agent",
                            "pitcrew-state::todo",
                        ],
                    }
                ]
            ),
        )

        self.assertEqual("unavailable", decision["decision"])
        self.assertIsNone(decision["target_id"])

    def test_issue_with_invalid_labels_is_unavailable(self):
        invalid_labels = (
            "pitcrew-agent",
            ["pitcrew-agent", 7],
        )
        for labels in invalid_labels:
            with self.subTest(labels=labels):
                decision = decide(
                    self.config(),
                    "implementer-run",
                    runtime_dir=self.runtime,
                    provider_run=self.provider_json(
                        [{"iid": 5, "labels": labels}]
                    ),
                )

                self.assertEqual("unavailable", decision["decision"])
                self.assertIsNone(decision["target_id"])

    def test_merge_request_with_invalid_iid_is_unavailable(self):
        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([{"iid": "12"}]),
        )

        self.assertEqual("unavailable", decision["decision"])
        self.assertIsNone(decision["target_id"])

    def test_provider_oserror_and_timeout_are_unavailable(self):
        for name, error in (
            ("oserror", OSError("missing executable")),
            (
                "timeout",
                subprocess.TimeoutExpired(cmd=["glab"], timeout=15),
            ),
        ):
            with self.subTest(name=name):
                decision = decide(
                    self.config(),
                    "implementer-run",
                    runtime_dir=self.runtime,
                    provider_run=lambda command, error=error: (_ for _ in ()).throw(
                        error
                    ),
                )
                self.assertEqual("unavailable", decision["decision"])

    def test_default_provider_run_uses_configured_glab_command_contract(self):
        completed = subprocess.CompletedProcess(["custom-glab"], 0, "[]", "")
        with mock.patch.dict("os.environ", {"GLAB_BIN": "custom-glab"}):
            with mock.patch(
                "scripts.pitcrew_eligibility.subprocess.run",
                return_value=completed,
            ) as run:
                result = default_provider_run(
                    [
                        "custom-glab",
                        "api",
                        "--hostname",
                        "gitlab.example",
                        "projects/7/issues",
                    ]
                )

        self.assertIs(completed, result)
        run.assert_called_once_with(
            [
                "custom-glab",
                "api",
                "--hostname",
                "gitlab.example",
                "projects/7/issues",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    def test_empty_agent_issues_are_empty_and_query_is_provider_filtered(self):
        commands = []

        def provider(command):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "[]", "")

        decision = decide(
            self.config(),
            "implementer-run",
            runtime_dir=self.runtime,
            provider_run=provider,
        )

        self.assertEqual("empty", decision["decision"])
        self.assertEqual(1, len(commands))
        self.assertEqual(
            ["glab", "api", "--hostname", "gitlab.com"],
            commands[0][:4],
        )
        endpoint = commands[0][4]
        self.assertTrue(endpoint.startswith("projects/59043683/issues?"))
        self.assertIn("state=opened", endpoint)
        self.assertIn("labels=pitcrew-agent", endpoint)
        self.assertIn("per_page=100", endpoint)
        self.assertIn("order_by=iid", endpoint)
        self.assertIn("sort=asc", endpoint)

    def test_implementer_accepts_todo_or_review_and_selects_ascending_iid(self):
        issues = [
            {
                "iid": 9,
                "labels": ["pitcrew-agent", "pitcrew-state::todo"],
            },
            {
                "iid": 4,
                "labels": ["pitcrew-agent", "pitcrew-state::review"],
            },
            {
                "iid": 2,
                "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            },
        ]

        decision = decide(
            self.config(),
            "implementer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(issues),
        )

        self.assertEqual("eligible", decision["decision"])
        self.assertEqual("getbill1/getbill#4", decision["target_id"])

    def test_validator_requires_agent_and_review_labels(self):
        issues = [
            {"iid": 1, "labels": ["pitcrew-state::review"]},
            {"iid": 2, "labels": ["pitcrew-agent", "pitcrew-state::todo"]},
            {
                "iid": 8,
                "labels": ["pitcrew-agent", "pitcrew-state::review"],
            },
        ]

        decision = decide(
            self.config(),
            "validator-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(issues),
        )

        self.assertEqual("eligible", decision["decision"])
        self.assertEqual("getbill1/getbill#8", decision["target_id"])

    def test_investigate_requires_agent_route_and_todo_labels(self):
        issues = [
            {
                "iid": 1,
                "labels": [
                    "pitcrew-agent",
                    "pitcrew-route::investigate",
                    "pitcrew-state::review",
                ],
            },
            {
                "iid": 2,
                "labels": ["pitcrew-agent", "pitcrew-state::todo"],
            },
            {
                "iid": 3,
                "labels": [
                    "pitcrew-agent",
                    "pitcrew-route::investigate",
                    "pitcrew-state::todo",
                ],
            },
        ]

        decision = decide(
            self.config(),
            "investigate-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(issues),
        )

        self.assertEqual("getbill1/getbill#3", decision["target_id"])

    def test_reviewer_successful_empty_response_is_empty(self):
        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([]),
        )

        self.assertEqual("empty", decision["decision"])
        self.assertIsNone(decision["target_id"])

    def test_reviewer_authored_open_merge_request_is_eligible(self):
        commands = []

        def provider(command):
            commands.append(command)
            return subprocess.CompletedProcess(
                command, 0, json.dumps([{"iid": 12}]), ""
            )

        decision = decide(
            self.config(),
            "reviewer-run",
            runtime_dir=self.runtime,
            provider_run=provider,
        )

        self.assertEqual("eligible", decision["decision"])
        self.assertEqual("getbill1/getbill!12", decision["target_id"])
        endpoint = commands[0][4]
        self.assertTrue(endpoint.startswith("projects/59043683/merge_requests?"))
        self.assertIn("state=opened", endpoint)
        self.assertIn("author_username=joachim28", endpoint)
        self.assertIn("per_page=100", endpoint)

    def test_pending_unblock_question_is_empty_without_provider_call(self):
        (self.runtime / "unblock-state.json").write_text(
            json.dumps(
                {
                    "asked": {},
                    "pending_question": {
                        "ticket_id": "getbill1/getbill#5",
                        "status": "blocked",
                        "question": "Choose a scope",
                        "choices": ["A", "B"],
                    },
                    "history": [],
                }
            ),
            encoding="utf-8",
        )

        decision = decide(
            self.config(),
            "unblock",
            runtime_dir=self.runtime,
            provider_run=lambda command: self.fail("provider must not run"),
        )

        self.assertEqual("empty", decision["decision"])
        self.assertIn("human decision", decision["reason"])

    def test_unblock_without_pending_question_selects_blocked_issue(self):
        decision = decide(
            self.config(),
            "unblock",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(
                [
                    {
                        "iid": 6,
                        "labels": [
                            "pitcrew-agent",
                            "pitcrew-state::blocked",
                        ],
                    }
                ]
            ),
        )

        self.assertEqual("eligible", decision["decision"])
        self.assertEqual("getbill1/getbill#6", decision["target_id"])

    def test_malformed_unblock_state_is_unavailable(self):
        (self.runtime / "unblock-state.json").write_text(
            "{not-json",
            encoding="utf-8",
        )

        decision = decide(
            self.config(),
            "unblock",
            runtime_dir=self.runtime,
            provider_run=lambda command: self.fail("provider must not run"),
        )

        self.assertEqual("unavailable", decision["decision"])

    def configured_manager(self, ledger: Path, source_format="research-v1"):
        config = self.config()
        config["manager"] = {
            "sources": [
                {
                    "name": "research",
                    "format": source_format,
                    "findings_json": str(ledger),
                }
            ]
        }
        return config

    def test_manager_selects_first_sorted_unfiled_key(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text(
            json.dumps(
                {
                    "source": "research-run",
                    "updated_at": "2026-07-26T10:00:00Z",
                    "findings": [
                        {"key": "getbill::z-last"},
                        {"key": "getbill::a-first"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        decision = decide(
            self.configured_manager(ledger),
            "manager-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([]),
        )

        self.assertEqual("eligible", decision["decision"])
        self.assertEqual("getbill::a-first", decision["target_id"])

    def test_manager_is_empty_when_every_finding_is_filed(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text(
            json.dumps({"findings": [{"key": "known"}]}),
            encoding="utf-8",
        )
        (self.runtime / "manager-state.json").write_text(
            json.dumps({"filed": {"known": {"ticket": "#1"}}, "history": []}),
            encoding="utf-8",
        )

        decision = decide(
            self.configured_manager(ledger),
            "manager-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([]),
        )

        self.assertEqual("empty", decision["decision"])

    def test_manager_missing_source_is_authoritatively_empty(self):
        decision = decide(
            self.configured_manager(self.runtime / "missing.json"),
            "manager-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json([]),
        )

        self.assertEqual("empty", decision["decision"])
        self.assertIsNotNone(decision["fingerprint"])

    def test_manager_malformed_state_is_unavailable(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text(json.dumps({"findings": []}), encoding="utf-8")
        (self.runtime / "manager-state.json").write_text(
            "{not-json",
            encoding="utf-8",
        )

        decision = decide(
            self.configured_manager(ledger),
            "manager-run",
            runtime_dir=self.runtime,
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_manager_malformed_ledger_is_unavailable(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text("{not-json", encoding="utf-8")

        decision = decide(
            self.configured_manager(ledger),
            "manager-run",
            runtime_dir=self.runtime,
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_manager_finding_without_nonempty_key_is_unavailable(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text(
            json.dumps({"findings": [{"title": "keyless"}]}),
            encoding="utf-8",
        )

        decision = decide(
            self.configured_manager(ledger),
            "manager-run",
            runtime_dir=self.runtime,
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_manager_unsupported_source_format_is_unavailable(self):
        ledger = self.runtime / "research-findings.json"
        ledger.write_text(json.dumps({"findings": []}), encoding="utf-8")

        decision = decide(
            self.configured_manager(ledger, source_format="audit-v1"),
            "manager-run",
            runtime_dir=self.runtime,
        )

        self.assertEqual("unavailable", decision["decision"])

    def test_unsupported_role_is_unavailable(self):
        decision = decide(
            self.config(),
            "research-run",
            runtime_dir=self.runtime,
            provider_run=lambda command: self.fail("provider must not run"),
        )

        self.assertEqual("unavailable", decision["decision"])
        self.assertIn("no Phase A deterministic", decision["reason"])

    def test_decision_has_exact_keys_and_stable_canonical_fingerprint(self):
        payload = [
            {
                "labels": ["pitcrew-agent", "pitcrew-state::review"],
                "iid": 8,
            }
        ]
        expected_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = "sha256:" + hashlib.sha256(expected_json.encode()).hexdigest()

        first = decide(
            self.config(),
            "validator-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(payload),
        )
        second = decide(
            self.config(),
            "validator-run",
            runtime_dir=self.runtime,
            provider_run=self.provider_json(
                [
                    {
                        "iid": 8,
                        "labels": [
                            "pitcrew-agent",
                            "pitcrew-state::review",
                        ],
                    }
                ]
            ),
        )

        self.assertEqual(EXPECTED_KEYS, set(first))
        self.assertEqual(expected, first["fingerprint"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_cli_prints_real_unavailable_decision_as_json_with_exit_zero(self):
        with mock.patch(
            "scripts.pitcrew_eligibility.load_runtime_config",
            return_value=self.config(),
        ), mock.patch(
            "scripts.pitcrew_eligibility.runtime_root",
            return_value=self.runtime.parent,
        ), mock.patch("builtins.print") as output:
            exit_code = main(
                ["check", "--project", "getbill", "--skill", "research-run"]
            )

        self.assertEqual(0, exit_code)
        printed = output.call_args.args[0]
        self.assertEqual("unavailable", json.loads(printed)["decision"])
        self.assertNotIn('": ', printed)
        self.assertNotIn(", ", printed)

    def test_cli_passes_runtime_project_directory_to_decide(self):
        config = self.config()
        runtime_base = self.runtime / "runtime-root"
        expected = {
            "decision": "unavailable",
            "project": "getbill",
            "skill": "research-run",
            "target_id": None,
            "fingerprint": None,
            "reason": "unsupported",
        }
        with mock.patch(
            "scripts.pitcrew_eligibility.load_runtime_config",
            return_value=config,
        ), mock.patch(
            "scripts.pitcrew_eligibility.runtime_root",
            return_value=runtime_base,
        ), mock.patch(
            "scripts.pitcrew_eligibility.decide",
            return_value=expected,
        ) as decide_mock, mock.patch("builtins.print"):
            exit_code = main(
                ["check", "--project", "getbill", "--skill", "research-run"]
            )

        self.assertEqual(0, exit_code)
        decide_mock.assert_called_once_with(
            config,
            "research-run",
            runtime_dir=runtime_base / "getbill",
        )

    def test_cli_configuration_exception_is_concise_stderr_and_exit_two(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch(
            "scripts.pitcrew_eligibility.load_runtime_config",
            side_effect=ValueError("invalid runtime configuration"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                ["check", "--project", "getbill", "--skill", "reviewer-run"]
            )

        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual(
            "pitcrew eligibility: invalid runtime configuration\n",
            stderr.getvalue(),
        )
        self.assertLessEqual(len(stderr.getvalue()), 280)

    def test_direct_script_uses_runtime_config_and_fake_glab(self):
        codex_home = (self.runtime / "codex-home").resolve()
        project_dir = codex_home / "pitcrew/getbill"
        project_dir.mkdir(parents=True)
        (project_dir / "config.json").write_text(
            json.dumps(self.config()),
            encoding="utf-8",
        )
        fake_glab = self.runtime / "fake-glab"
        fake_glab.write_text(
            "#!/usr/bin/env python3\nprint('[]')\n",
            encoding="utf-8",
        )
        fake_glab.chmod(0o700)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        env["GLAB_BIN"] = str(fake_glab)

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "check",
                "--project",
                "getbill",
                "--skill",
                "reviewer-run",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stderr)
        decision = json.loads(completed.stdout)
        self.assertEqual(EXPECTED_KEYS, set(decision))
        self.assertEqual("empty", decision["decision"])
        self.assertEqual("getbill", decision["project"])
        self.assertEqual("reviewer-run", decision["skill"])
        self.assertIsNotNone(decision["fingerprint"])


if __name__ == "__main__":
    unittest.main()
