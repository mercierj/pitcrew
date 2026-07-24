import unittest
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
