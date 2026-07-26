import unittest
import subprocess
import sys
import re
from pathlib import Path

from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    MODEL_CATALOG,
    PRICING_CURRENCY,
    PRICING_EFFECTIVE_DATE,
)


ROOT = Path(__file__).resolve().parents[1]


class DocsTest(unittest.TestCase):
    def test_native_github_issue_setup_is_explicit(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for marker in (
            "Native GitHub Issues",
            "bin/configure.sh bind-github",
            "providers.forge",
            "providers.tracker",
            "does not infer",
            "bugfixer-run remains disabled",
        ):
            self.assertIn(marker, readme)

    def test_dashboard_operator_contract_is_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )

        for name, text in (("README.md", readme), ("SCHEDULED-TASKS.md", scheduled)):
            normalized = text.lower()
            for expected in (
                "./bin/pitcrew-dashboard",
                "http://127.0.0.1:8765",
                "seven days",
                "trigger",
                "stop",
                "restart",
                "ctrl-c",
                "gitlab",
                "degraded",
                "local",
                "release",
                "prod",
                "preprod",
            ):
                self.assertIn(expected, normalized, name)
            self.assertRegex(
                text,
                r"(?is)(release|prod|preprod).{0,180}"
                r"(not available|absent|excluded|no controls)",
                name,
            )

        for field in (
            "project",
            "skill",
            "started_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "exit_code",
            "summary",
        ):
            self.assertIn(f"`{field}`", scheduled)

        self.assertIn("0600", scheduled)
        self.assertIn("lock", scheduled)
        self.assertIn("retention", scheduled)
        self.assertRegex(
            scheduled,
            r"(?is)no eligible item.{0,120}(healthy|success)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)(missing configuration|configuration.{0,40}missing)"
            r".{0,160}(warning|warn)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)(authentication|auth).{0,160}(warning|warn)",
        )
    def test_primary_docs_are_codex_native(self):
        paths = [
            "README.md",
            "docs/CODEX.md",
            "references/SETUP.md",
            "references/TOPOLOGY.md",
        ]
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("$pitcrew:", text, relative)
            self.assertNotIn("~/.codex/prompts", text, relative)
            self.assertNotIn("CODEX_SANDBOX=danger-full-access", text, relative)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("GetBill profile", readme)
        self.assertIn("Codex scheduled", readme)
        for relative in ("README.md", "docs/CODEX.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("`Refresh with:`", text, relative)
            self.assertNotIn("codex plugin add pitcrew@personal", text, relative)

    def test_examples_parse(self):
        import json

        json.loads(
            (ROOT / "references/config.example.json").read_text(encoding="utf-8")
        )
        json.loads((ROOT / "profiles/getbill.json").read_text(encoding="utf-8"))

    def test_agent_model_usage_controls_are_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("agents.<role>.model", scheduled)
        self.assertIn("agents.<role>.fallback", scheduled)
        for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            self.assertIn(model, scheduled)
            self.assertIn(model, readme)
        for term in (
            "--model",
            "JSONL",
            "full transcript",
            "cache_write_tokens",
            "stop",
            "atomic",
            "install",
            "immediate",
            "seven-day",
            "2026-07-24",
            "USD",
            "API-equivalent",
            "subscription",
            "priority",
            "regional",
            "container",
        ):
            self.assertIn(term, scheduled)
        self.assertIn("API-equivalent", readme)
        self.assertIn("seven-day", readme)

    def test_model_catalog_documentation_tracks_runtime_constants(self):
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )
        section = re.search(
            r"(?ms)^## Agent model and usage controls$.*?(?=^## |\Z)", scheduled
        )
        self.assertIsNotNone(section)
        text = section.group(0)

        for role, model in DEFAULT_MODELS.items():
            self.assertRegex(
                text,
                rf"(?m)^\| `{re.escape(role)}` \| `{re.escape(model)}` \|$",
            )

        for model, details in MODEL_CATALOG.items():
            prices = details["pricing"]
            rates = " | ".join(
                format(prices[field], "f")
                for field in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_tokens",
                    "output_tokens",
                )
            )
            self.assertRegex(
                text,
                rf"(?m)^\| `{re.escape(model)}` \| {re.escape(rates)} \|.*\|$",
            )

        self.assertRegex(
            text,
            rf"(?m)^Pricing snapshot effective {re.escape(PRICING_EFFECTIVE_DATE)}, "
            rf"in {re.escape(PRICING_CURRENCY)} per million tokens\.$",
        )

    def test_documented_migration_options_are_supported(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitcrew_config.py"),
                "migrate",
                "--help",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--project", result.stdout)
        for relative in ("README.md", "docs/CODEX.md", "references/SETUP.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("migrate --project getbill --dry-run", text, relative)

    def test_architecture_agent_contract_is_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        topology = (ROOT / "references/TOPOLOGY.md").read_text(encoding="utf-8")
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )
        codex = (ROOT / "docs/CODEX.md").read_text(encoding="utf-8")

        for name, text in (
            ("README.md", readme),
            ("TOPOLOGY.md", topology),
            ("SCHEDULED-TASKS.md", scheduled),
            ("CODEX.md", codex),
        ):
            self.assertIn("$pitcrew:architecture-run", text, name)
            self.assertRegex(text, r"(?is)architecture.{0,180}human")

        self.assertRegex(
            readme,
            r"(?is)architecture-run.{0,240}(read-only|read only).{0,240}proposal",
        )
        self.assertRegex(
            topology,
            r"(?is)architecture-run\s*→\s*local proposal\s*→\s*human.*?dashboard"
            r".{0,100}→\s*manager\s*→\s*tracker",
        )
        self.assertRegex(topology, r"(?is)dismissal.{0,100}local")
        self.assertRegex(topology, r"(?is)manager.{0,160}(pace|dedup)")
        for marker in (
            "604800",
            "gpt-5.6-sol",
            "reasoning effort `high`",
            "architecture-state.json",
            "zero findings",
            "does not advance",
            "maximum 3",
            "architecture category",
            "manual",
        ):
            self.assertIn(marker, scheduled.lower(), marker)

        self.assertIn("writes no code", scheduled.lower())
        for forbidden_remote_action in ("merge", "deploy", "tracker record"):
            self.assertIn(forbidden_remote_action, scheduled.lower())
        for name, text in (
            ("README.md", readme),
            ("SCHEDULED-TASKS.md", scheduled),
            ("CODEX.md", codex),
        ):
            normalized = text.lower()
            self.assertIn("proposal ledger", normalized, name)
            self.assertIn("architecture-state.json", normalized, name)
            self.assertNotRegex(
                normalized,
                r"(?:only|seulement|uniquement)\s+(?:its\s+)?(?:local\s+)?"
                r"(?:proposal\s+)?ledger",
                name,
            )
        self.assertIn("./bin/pitcrew-codex.sh architecture-run getbill --dry-run", codex)

    def test_preprod_review_is_documented_as_manual_read_only_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        topology = (ROOT / "references/TOPOLOGY.md").read_text(encoding="utf-8")
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )
        codex = (ROOT / "docs/CODEX.md").read_text(encoding="utf-8")

        for name, text in (
            ("README.md", readme),
            ("TOPOLOGY.md", topology),
            ("SCHEDULED-TASKS.md", scheduled),
            ("CODEX.md", codex),
        ):
            self.assertIn("$pitcrew:preprod-review-run", text, name)

        for marker in (
            "origin/preprod...origin/develop",
            "manual-only",
            "gpt-5.6-sol",
            "xhigh",
            "working tree",
            "untracked",
            "ignored",
            "ready",
            "changes_required",
            "incomplete",
        ):
            self.assertIn(marker, readme.lower(), marker)

        self.assertRegex(
            readme,
            r"(?is)dashboard.{0,160}(button|bouton|trigger).{0,160}preprod-review-run",
        )
        self.assertRegex(
            topology,
            r"(?is)preprod-review-run.{0,200}(local|private).{0,200}report",
        )
        self.assertRegex(codex, r"(?is)manual-only.{0,240}preprod-review-run")
        self.assertRegex(codex, r"(?is)preprod-review-run.{0,480}read-only")
        self.assertRegex(
            scheduled,
            r"(?is)preprod-review-run.{0,240}(never scheduled|not scheduled).{0,240}launchagent",
        )
        self.assertRegex(
            scheduled,
            r"(?is)(?:no|never).{0,120}(?:restart|interval|automatic|auto invocation)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)(?:no|never).{0,160}(?:gitlab issue|comment|merge request|merge|deploy|database)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)exactly once.{0,200}(?:deletion|rename|copy|typechange|binary|generated)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)global stop.{0,200}(sigterm|interrupt)",
        )
        self.assertRegex(
            scheduled,
            r"(?is)(?:history|historique).{0,100}(?:10|ten).{0,160}(?:sha|pair)",
        )

        self.assertNotIn("preprod-review-run", (ROOT / "bin/pitcrew-schedule.py").read_text(encoding="utf-8"))
        skill = (ROOT / "skills/preprod-review-run/SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(skill, r"(?m)^\s*(?:glab|git)\s+.*\b(?:merge|deploy)\b")
        for line in skill.splitlines():
            if re.search(r"\b(?:glab|merge(?!-)|deploy|database)\b", line, re.I):
                self.assertRegex(line, r"(?i)(?:never|do not|read-only|review)")

    def test_preprod_review_docs_do_not_contradict_crew_count_or_dashboard_boundary(self):
        topology = (ROOT / "references/TOPOLOGY.md").read_text(encoding="utf-8")
        scheduled = (ROOT / "references/SCHEDULED-TASKS.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("seventeen Codex skills", topology)
        self.assertEqual(17, len(re.findall(r"^\| `\$pitcrew:[^`]+` \|", topology, re.M)))
        self.assertRegex(scheduled, r"(?is)manual preprod review.{0,180}(?:never scheduled|manual-only)")
        self.assertRegex(scheduled, r"(?is)preprod review.{0,300}(?:local|read-only)")
        self.assertRegex(scheduled, r"(?is)preprod.{0,80}controls are absent")
        self.assertRegex(
            scheduled,
            r"(?is)exception is the local, read-only,.{0,100}manual-only preprod review",
        )

    def test_preprod_review_documents_both_manual_launch_paths(self):
        docs = {
            relative: (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "README.md",
                "docs/CODEX.md",
                "references/SCHEDULED-TASKS.md",
                "references/TOPOLOGY.md",
            )
        }
        command = "./bin/pitcrew-codex.sh preprod-review-run getbill"

        for relative, text in docs.items():
            self.assertIn("dashboard", text.lower(), relative)
            self.assertIn(command, text, relative)
            self.assertRegex(text, r"(?is)(?:locked|lock).{0,120}ephemeral")
            self.assertRegex(
                text,
                r"(?is)--scheduled.{0,120}--coordinated-run.{0,120}--target.{0,160}(?:refused|forbidden|not allowed)",
            )
            self.assertNotRegex(text, r"(?is)only supported launch path.{0,120}dashboard")


if __name__ == "__main__":
    unittest.main()
