import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_config import (
    ConfigError,
    _write_exclusive_config,
    load_profile,
    migrate_legacy,
    runtime_root,
    validate,
    write_project,
)


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

    def test_migration_copies_legacy_config_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            source_config = (
                ROOT / "tests/fixtures/legacy-config.json"
            ).read_text(encoding="utf-8")
            (source / "config.json").write_text(source_config, encoding="utf-8")
            codex_home = root / ".codex"
            codex_home.mkdir()
            env = {"HOME": str(root), "CODEX_HOME": str(codex_home)}

            destination = migrate_legacy("getbill", env)

            self.assertEqual(
                {
                    "project_name": "getbill",
                    "github": {"reviewer_login": "mercierj", "org": "mercierj"},
                    "linear": {"use": False},
                    "repos": [
                        {
                            "name": "getbill",
                            "path": "/Users/jo/Prog/getbill",
                            "default_branch": "main",
                            "lang": "php",
                        }
                    ],
                    "schema_version": 1,
                    "providers": {"forge": "github", "tracker": "none"},
                    "release": {"autonomy": "off"},
                    "safety": {
                        "allow_database_writes": False,
                        "allow_destructive_git": False,
                        "allow_secret_reads": False,
                    },
                },
                json.loads(destination.read_text(encoding="utf-8")),
            )
            original = destination.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                migrate_legacy("getbill", env)
            self.assertEqual(original, destination.read_text(encoding="utf-8"))

    def test_migration_preserves_existing_release_arming(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            legacy = json.loads(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8")
            )
            legacy["release"] = {"autonomy": "prepare"}
            (source / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

            codex_home = root / ".codex"
            codex_home.mkdir()
            destination = migrate_legacy(
                "getbill", {"HOME": str(root), "CODEX_HOME": str(codex_home)}
            )

            self.assertEqual(
                "prepare", json.loads(destination.read_text(encoding="utf-8"))["release"]["autonomy"]
            )

    def test_migration_creates_missing_nested_codex_home_securely(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_home = root / "missing" / "nested" / "codex"

            destination = migrate_legacy(
                "getbill", {"HOME": str(root), "CODEX_HOME": str(codex_home)}
            )

            self.assertEqual(codex_home / "pitcrew" / "getbill" / "config.json", destination)
            self.assertTrue(destination.is_file())

    def test_migration_rejects_symlinked_source_and_destination_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").symlink_to(outside)
            env = {"HOME": str(root), "CODEX_HOME": str(root / ".codex")}
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", env)

            (source / "config.json").unlink()
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runtime = root / ".codex" / "pitcrew"
            runtime.parent.mkdir()
            runtime.symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", env)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runtime = root / ".codex" / "pitcrew"
            runtime.mkdir(parents=True)
            (runtime / "getbill").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(root), "CODEX_HOME": str(root / ".codex")})

    def test_cli_migrate_validates_profile_and_reports_errors_concisely(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_home = root / ".codex"
            codex_home.mkdir()
            environment = {**os.environ, "HOME": str(root), "CODEX_HOME": str(codex_home)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "migrate", "--project", "getbill"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            destination = root / ".codex" / "pitcrew" / "getbill" / "config.json"
            self.assertTrue(destination.is_file())
            validate(json.loads(destination.read_text(encoding="utf-8")))
            source = root / ".claude" / "agent-loop" / "getbill" / "config.json"
            self.assertIn(str(source), result.stdout)
            self.assertIn(str(destination), result.stdout)
            self.assertIn("Migrated config to", result.stdout)
            self.assertLess(
                result.stdout.index(str(source)), result.stdout.index("Migrated config to")
            )

    def test_exclusive_writer_uses_open_directory_after_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            original.mkdir()
            outside = root / "outside"
            outside.mkdir()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open(original, flags)
            try:
                moved = root / "moved"
                original.rename(moved)
                original.symlink_to(outside, target_is_directory=True)

                _write_exclusive_config(directory_fd, "config.json", '{"safe": true}\n')
            finally:
                os.close(directory_fd)

            self.assertEqual('{"safe": true}\n', (moved / "config.json").read_text())
            self.assertFalse((outside / "config.json").exists())

    def test_cli_migrate_rejects_unreadable_legacy_config_without_destination(self):
        for payload in ("{", b"\xff"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                source = root / ".claude" / "agent-loop" / "getbill"
                source.mkdir(parents=True)
                config = source / "config.json"
                if isinstance(payload, bytes):
                    config.write_bytes(payload)
                else:
                    config.write_text(payload, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "migrate", "--project", "getbill"],
                    text=True,
                    capture_output=True,
                    check=False,
                    env={**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")},
                )
                self.assertEqual(1, result.returncode)
                self.assertIn("pitcrew-config:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(
                    (root / ".codex" / "pitcrew" / "getbill" / "config.json").exists()
                )

    def test_migration_rejects_symlinked_home_and_codex_home_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            outside = root / "outside"
            outside.mkdir()
            legacy = outside / ".claude" / "agent-loop" / "getbill"
            legacy.mkdir(parents=True)
            (legacy / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            home_link = root / "home-link"
            home_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(home_link)})
            self.assertFalse((outside / ".codex" / "pitcrew" / "getbill" / "config.json").exists())

            home = root / "home"
            source = home / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_link = root / "codex-link"
            codex_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(home), "CODEX_HOME": str(codex_link)})
            self.assertFalse((outside / "pitcrew" / "getbill" / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
