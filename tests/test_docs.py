import unittest
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsTest(unittest.TestCase):
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
