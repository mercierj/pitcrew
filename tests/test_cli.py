import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run(
            [str(ROOT / args[0]), *args[1:]],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_configure_getbill_writes_under_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": str(Path(temp).resolve())}
            result = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(Path(temp, "pitcrew/getbill/config.json").is_file())

    def test_runner_dry_run_uses_namespaced_skill_and_preserves_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": str(Path(temp).resolve())}
            init = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, init.returncode, init.stderr)
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Use $pitcrew:research-run", result.stdout)
            self.assertNotIn("danger-full-access", result.stdout)
            self.assertIn("/Users/jo/Prog/getbill", result.stdout)

    def test_getbill_dry_run_never_requests_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            config_path = root / ".codex/pitcrew/getbill/config.json"
            config = config_path.read_text(encoding="utf-8")
            self.assertIn('"autonomy": "off"', config)
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "releaser-run",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("danger-full-access", result.stdout)
            self.assertNotIn("deploy", result.stdout.lower().split("prompt=", 1)[0])

    def test_runner_rejects_malformed_repository_without_calling_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runtime = root / "pitcrew/getbill"
            runtime.mkdir(parents=True)
            config = json.loads((ROOT / "profiles/getbill.json").read_text(encoding="utf-8"))
            config["repos"] = [None]
            (runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "pitcrew/default.txt").write_text("getbill\n", encoding="utf-8")
            marker = root / "codex-called"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n", encoding="utf-8")
            fake_codex.chmod(0o755)

            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                env={**os.environ, "CODEX_HOME": str(root), "CODEX_BIN": str(fake_codex)},
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("pitcrew-config:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(marker.exists())

    def test_runner_rejects_control_characters_in_repository_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repository = root / "repository\ninjected-instruction"
            repository.mkdir()
            runtime = root / "pitcrew/getbill"
            runtime.mkdir(parents=True)
            config = json.loads((ROOT / "profiles/getbill.json").read_text(encoding="utf-8"))
            config["repos"][0]["path"] = str(repository)
            (runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")
            marker = root / "codex-called"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n", encoding="utf-8")
            fake_codex.chmod(0o755)

            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                env={**os.environ, "CODEX_HOME": str(root), "CODEX_BIN": str(fake_codex)},
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("control", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(marker.exists())

    def test_runner_rejects_symlinked_or_malformed_default_project(self):
        for default_kind in ("symlink", "malformed"):
            with self.subTest(default_kind=default_kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                runtime = root / "pitcrew"
                project = runtime / "getbill"
                project.mkdir(parents=True)
                (project / "config.json").write_text(
                    (ROOT / "profiles/getbill.json").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                default = runtime / "default.txt"
                if default_kind == "symlink":
                    outside = root / "outside-default.txt"
                    outside.write_text("getbill\n", encoding="utf-8")
                    default.symlink_to(outside)
                else:
                    default.write_text("getbill\ninjected", encoding="utf-8")
                marker = root / "codex-called"
                fake_codex = root / "fake-codex"
                fake_codex.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n", encoding="utf-8")
                fake_codex.chmod(0o755)

                result = self.run_cli(
                    "bin/pitcrew-codex.sh",
                    "research-run",
                    env={**os.environ, "CODEX_HOME": str(root), "CODEX_BIN": str(fake_codex)},
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("pitcrew-config:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(marker.exists())

    def test_runner_rejects_symlinked_runtime_config_without_calling_codex(self):
        for symlink in ("runtime", "project", "config"):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                runtime = root / "pitcrew"
                outside = root / "outside"
                outside.mkdir()
                config = (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
                if symlink == "runtime":
                    (outside / "getbill").mkdir()
                    (outside / "getbill/config.json").write_text(config, encoding="utf-8")
                    runtime.symlink_to(outside, target_is_directory=True)
                elif symlink == "project":
                    runtime.mkdir()
                    (outside / "config.json").write_text(config, encoding="utf-8")
                    (runtime / "getbill").symlink_to(outside, target_is_directory=True)
                else:
                    project = runtime / "getbill"
                    project.mkdir(parents=True)
                    outside_config = outside / "config.json"
                    outside_config.write_text(config, encoding="utf-8")
                    (project / "config.json").symlink_to(outside_config)
                marker = root / "codex-called"
                fake_codex = root / "fake-codex"
                fake_codex.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n", encoding="utf-8")
                fake_codex.chmod(0o755)

                result = self.run_cli(
                    "bin/pitcrew-codex.sh",
                    "research-run",
                    "getbill",
                    env={**os.environ, "CODEX_HOME": str(root), "CODEX_BIN": str(fake_codex)},
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("pitcrew-config:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(marker.exists())

    def test_installer_dry_run_targets_personal_marketplace(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "HOME": temp,
                "CODEX_HOME": str(Path(temp) / ".codex"),
            }
            result = self.run_cli(
                "bin/install-codex.sh",
                "getbill",
                "--profile",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(".agents/plugins/marketplace.json", result.stdout)
            self.assertIn("plugins/pitcrew", result.stdout)
            self.assertNotIn(".codex/prompts", result.stdout)
            self.assertNotIn(".claude", result.stdout)

            default_project = self.run_cli(
                "bin/install-codex.sh",
                "--dry-run",
                "--profile",
                "getbill",
                env=env,
            )
            self.assertEqual(0, default_project.returncode, default_project.stderr)
            self.assertIn("/pitcrew/example/config.json", default_project.stdout)

            extra_project = self.run_cli(
                "bin/install-codex.sh",
                "getbill",
                "unexpected",
                "--dry-run",
                env=env,
            )
            self.assertEqual(2, extra_project.returncode)
            self.assertIn("Unexpected project argument", extra_project.stderr)

    def test_installer_preserves_marketplace_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marketplace = root / ".agents/plugins/marketplace.json"
            marketplace.parent.mkdir(parents=True)
            marketplace.write_text(
                json.dumps(
                    {
                        "name": "personal",
                        "interface": {"displayName": "Mine"},
                        "plugins": [
                            {"name": "other", "source": {"source": "local", "path": "./plugins/other"}},
                            {"name": "pitcrew", "source": {"source": "local", "path": "./old"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "HOME": temp, "CODEX_HOME": str(root / ".codex")}
            for args in (("--profile", "getbill", "getbill"), ("getbill", "--profile", "getbill")):
                result = self.run_cli("bin/install-codex.sh", *args, env=env)
                self.assertEqual(0, result.returncode, result.stderr)

            payload = json.loads(marketplace.read_text(encoding="utf-8"))
            self.assertEqual("Mine", payload["interface"]["displayName"])
            self.assertEqual(1, len([item for item in payload["plugins"] if item["name"] == "pitcrew"]))
            self.assertIn({"name": "other", "source": {"source": "local", "path": "./plugins/other"}}, payload["plugins"])
            pitcrew = next(item for item in payload["plugins"] if item["name"] == "pitcrew")
            self.assertEqual("./plugins/pitcrew", pitcrew["source"]["path"])
            self.assertTrue((root / ".codex/pitcrew/getbill/config.json").is_file())

    def test_installer_refuses_unrelated_plugin_path_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin_path = root / "plugins/pitcrew"
            plugin_path.mkdir(parents=True)
            sentinel = plugin_path / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            env = {**os.environ, "HOME": temp, "CODEX_HOME": str(root / ".codex")}

            result = self.run_cli("bin/install-codex.sh", "getbill", env=env)

            self.assertEqual(2, result.returncode)
            self.assertIn("Refusing to replace existing plugin path", result.stderr)
            self.assertTrue(plugin_path.is_dir())
            self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
