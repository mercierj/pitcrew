import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "architecture-run",
    "preprod-review-run",
    "product-discovery-run",
    "security-run",
    "coverage-run",
    "dev-verify-run",
    "implementer-run",
    "bugfixer-run",
    "investigate-run",
    "manager-run",
    "ops-run",
    "qa-run",
    "releaser-run",
    "research-run",
    "reviewer-run",
    "stale-sweep",
    "unblock",
    "validator-run",
}
INTERFACE = {
    "displayName": "Pitcrew",
    "shortDescription": "A Codex crew for continuous repository work",
    "longDescription": "Run focused research, implementation, review, validation, operations, and release workflows with explicit safety gates and project profiles.",
    "developerName": "Pitcrew contributors",
    "category": "Developer Tools",
    "capabilities": [
        "Repository research",
        "Issue and merge-request workflows",
        "Code review and validation",
        "Guarded release preparation",
    ],
    "defaultPrompt": [
        "Use Pitcrew to research one high-confidence improvement.",
        "Use Pitcrew to review the next eligible change.",
        "Use Pitcrew to validate the GetBill project configuration.",
    ],
}


class PluginContractTest(unittest.TestCase):
    def test_standard_installer_references_every_codex_skill(self):
        installer = (ROOT / "bin/install.sh").read_text(encoding="utf-8")
        match = re.search(r"SKILLS=\(\n(?P<skills>.*?)\n\)", installer, re.DOTALL)
        self.assertIsNotNone(match)
        installed = set(re.findall(r'^\s+"([a-z-]+)"$', match.group("skills"), re.MULTILINE))
        self.assertEqual(SKILLS, installed)

    def test_manifest_exposes_all_skills(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pitcrew", manifest["name"])
        self.assertRegex(
            manifest["version"],
            r"^0\.2\.0(?:\+codex\.[0-9]{14})?$",
        )
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("MIT", manifest["license"])
        self.assertEqual("https://github.com/mercierj/pitcrew", manifest["homepage"])
        self.assertEqual("https://github.com/mercierj/pitcrew", manifest["repository"])
        self.assertEqual(
            ["codex", "automation", "code-review", "validation", "delivery"],
            manifest["keywords"],
        )
        self.assertEqual(SKILLS, {
            path.parent.name
            for path in (ROOT / "skills").glob("*/SKILL.md")
        })

    def test_manifest_has_codex_interface_metadata(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"name": "Felix Carrard and contributors", "url": "https://github.com/fcarrar"},
            manifest["author"],
        )
        self.assertEqual(INTERFACE, manifest["interface"])
        self.assertEqual("Developer Tools", manifest["interface"]["category"])


if __name__ == "__main__":
    unittest.main()
