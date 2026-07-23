import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_config import ConfigError, load_profile, runtime_root, validate, write_project


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pitcrew_config.py"


class ConfigTest(unittest.TestCase):
    def test_runtime_root_uses_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(Path(temp) / "pitcrew", runtime_root({"CODEX_HOME": temp}))

    def test_runtime_root_defaults_to_dot_codex(self):
        self.assertEqual(
            Path("/tmp/example-home/.codex/pitcrew"),
            runtime_root({"HOME": "/tmp/example-home"}),
        )

    def test_getbill_profile_fails_closed(self):
        profile = load_profile(ROOT / "profiles/getbill.json")
        self.assertEqual("gitlab", profile["providers"]["forge"])
        self.assertEqual("off", profile["release"]["autonomy"])
        self.assertEqual(
            ["prod", "preprod"],
            profile["safety"]["confirm_each_remote_action"],
        )
        self.assertFalse(profile["safety"]["allow_database_writes"])
        self.assertFalse(profile["safety"]["allow_destructive_git"])

    def test_invalid_provider_is_rejected(self):
        fixture = json.loads(
            (ROOT / "tests/fixtures/invalid-config.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ConfigError, "providers.forge"):
            validate(fixture)

    def test_invalid_project_does_not_create_runtime_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ConfigError, "project_name"):
                write_project(
                    ROOT / "profiles/generic.json", "../escape", {"CODEX_HOME": temp}
                )
            self.assertEqual([], list(Path(temp).iterdir()))

    def test_write_project_does_not_overwrite_existing_content_or_symlink_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {"CODEX_HOME": temp}
            destination = root / "pitcrew" / "example" / "config.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                write_project(ROOT / "profiles/generic.json", "example", env)
            self.assertEqual("existing", destination.read_text(encoding="utf-8"))

            destination.unlink()
            target = root / "outside.json"
            destination.symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                write_project(ROOT / "profiles/generic.json", "example", env)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(target.exists())

    def test_write_project_rejects_symlinked_project_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            project_directory = root / "pitcrew" / "getbill"
            project_directory.parent.mkdir()
            project_directory.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                write_project(
                    ROOT / "profiles/getbill.json", "getbill", {"CODEX_HOME": temp}
                )
            self.assertFalse((outside / "config.json").exists())

    def test_write_project_rejects_symlinked_default_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "pitcrew"
            runtime.mkdir()
            target = root / "outside-default.txt"
            target.write_text("unchanged", encoding="utf-8")
            (runtime / "default.txt").symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "default.txt"):
                write_project(ROOT / "profiles/generic.json", "example", {"CODEX_HOME": temp})
            self.assertEqual("unchanged", target.read_text(encoding="utf-8"))

    def test_write_project_updates_regular_default_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": temp}
            write_project(ROOT / "profiles/generic.json", "first", env)
            write_project(ROOT / "profiles/generic.json", "second", env)
            self.assertEqual(
                "second\n",
                (Path(temp) / "pitcrew" / "default.txt").read_text(encoding="utf-8"),
            )

    def test_malformed_config_containers_are_rejected(self):
        valid = {
            "schema_version": 1,
            "project_name": "example",
            "providers": {"forge": "github", "tracker": "linear"},
            "repos": [],
            "release": {"autonomy": "off"},
            "safety": {
                "allow_database_writes": False,
                "allow_destructive_git": False,
                "allow_secret_reads": False,
            },
        }
        for key, value in (("release", []), ("safety", [])):
            with self.subTest(key=key):
                malformed = {**valid, key: value}
                with self.assertRaisesRegex(ConfigError, f"{key} must be an object"):
                    validate(malformed)
        with self.assertRaisesRegex(ConfigError, "config must be an object"):
            validate([])

    def test_cli_reports_malformed_json_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "broken.json"
            config.write_text("{", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "validate", str(config)],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pitcrew-config:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_reports_non_utf8_config_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "broken.json"
            config.write_bytes(b"\xff")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "validate", str(config)],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pitcrew-config:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
