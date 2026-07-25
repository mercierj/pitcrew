import os
import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def test_model_command_prints_default_and_runtime_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {**os.environ, "CODEX_HOME": str(root)}
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)

            default = subprocess.run(
                [sys.executable, str(ROOT / "scripts/pitcrew_config.py"), "model", "--project", "getbill", "--skill", "research-run"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, default.returncode, default.stderr)
            self.assertEqual("gpt-5.6-terra\n", default.stdout)

            config_path = root / "pitcrew/getbill/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["agents"] = {"research-run": {"model": "gpt-5.6-luna"}}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            override = subprocess.run(
                [sys.executable, "-m", "scripts.pitcrew_config", "model", "--project", "getbill", "--skill", "research-run"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, override.returncode, override.stderr)
            self.assertEqual("gpt-5.6-luna\n", override.stdout)

    def test_runner_dry_run_prints_configured_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {**os.environ, "CODEX_HOME": str(root)}
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            config_path = root / "pitcrew/getbill/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["agents"] = {"research-run": {"model": "gpt-5.6-luna"}}
            config_path.write_text(json.dumps(config), encoding="utf-8")

            result = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", "--dry-run", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("model=gpt-5.6-luna", result.stdout)

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

    def test_scheduled_runner_is_ephemeral_networked_and_never_overlaps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            codex_home = root / ".codex"
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(codex_home),
                "PITCREW_LOCK_ROOT": str(root / "locks"),
                "FAKE_CODEX_MARKER": str(root / "codex-started"),
                "FAKE_CODEX_ARGS": str(root / "codex-args"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$FAKE_CODEX_ARGS\"\n"
                "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"thread_1\"}'\n"
                "printf '%s\\n' '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":120,\"cached_input_tokens\":40,\"cache_write_tokens\":10,\"output_tokens\":30,\"total_tokens\":200}}'\n"
                "previous=''\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then\n"
                "    printf '%s\\n' 'bounded summary' > \"$argument\"\n"
                "  fi\n"
                "  previous=\"$argument\"\n"
                "done\n"
                "touch \"$FAKE_CODEX_MARKER\"\n"
                "sleep 2\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)

            first = subprocess.Popen(
                [
                    str(ROOT / "bin/pitcrew-codex.sh"),
                    "research-run",
                    "getbill",
                    "--scheduled",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            marker = Path(env["FAKE_CODEX_MARKER"])
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "first scheduled run did not start")

            overlapping = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--scheduled",
                env=env,
            )
            stdout, stderr = first.communicate(timeout=5)
            self.assertEqual(0, first.returncode, stderr)
            self.assertEqual(0, overlapping.returncode, overlapping.stderr)
            payload = json.loads(overlapping.stdout)
            self.assertEqual("noop", payload["status"])
            self.assertIn("already running", payload["reason"])

            args = Path(env["FAKE_CODEX_ARGS"]).read_text(encoding="utf-8")
            self.assertIn("--ephemeral", args)
            self.assertIn("--model", args)
            self.assertIn("gpt-5.6-terra", args)
            self.assertIn("--json", args)
            self.assertIn("--sandbox", args)
            self.assertIn("workspace-write", args)
            self.assertIn("sandbox_workspace_write.network_access=true", args)
            self.assertIn("--output-last-message", args)
            self.assertIn(
                ".codex/pitcrew/getbill/logs/research-run.last.txt",
                args,
            )
            self.assertEqual(
                0o700,
                Path(env["PITCREW_LOCK_ROOT"]).stat().st_mode & 0o777,
            )
            self.assertEqual(
                0o700,
                (codex_home / "pitcrew/getbill/logs").stat().st_mode & 0o777,
            )
            self.assertEqual(
                0o600,
                (codex_home / "pitcrew/getbill/logs/research-run.last.txt").stat().st_mode
                & 0o777,
            )
            history_path = codex_home / "pitcrew/getbill/history.jsonl"
            self.assertTrue(history_path.is_file())
            history = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                ["noop", "success"],
                [record["outcome"] for record in history],
            )
            overlap = history[0]
            self.assertEqual(
                "research-run already running",
                json.loads(overlap["summary"])["reason"],
            )
            for record in history:
                self.assertTrue(record["started_at"].endswith("Z"))
                self.assertTrue(record["finished_at"].endswith("Z"))
                self.assertGreaterEqual(record["duration_ms"], 0)
                self.assertEqual("gpt-5.6-terra", record["model"])
            latest = history[-1]
            self.assertEqual("research-run", latest["skill"])
            self.assertEqual("success", latest["outcome"])
            self.assertEqual("bounded summary", latest["summary"])
            self.assertGreaterEqual(latest["duration_ms"], 0)
            self.assertEqual(
                {
                    "input_tokens": 120,
                    "cached_input_tokens": 40,
                    "cache_write_tokens": 10,
                    "output_tokens": 30,
                    "total_tokens": 200,
                },
                latest["usage"],
            )
            self.assertNotIn("thread_1", history_path.read_text(encoding="utf-8"))

    def test_scheduled_runner_records_failed_codex_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            codex_home = root / ".codex"
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(codex_home),
                "PITCREW_LOCK_ROOT": str(root / "locks"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' '{not-json'\n"
                "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"thread_1\"}'\n"
                "printf '%s\\n' '{\"usage\":{\"input_tokens\":1}}'\n"
                "printf '%s\\n' '{\"usage\":{\"input_tokens\":7,\"cached_input_tokens\":2,\"cache_write_tokens\":3,\"output_tokens\":4}}'\n"
                "previous=''\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then\n"
                "    printf '%s\\n' 'bounded failure summary' > \"$argument\"\n"
                "  fi\n"
                "  previous=\"$argument\"\n"
                "done\n"
                "exit 17\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)

            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--scheduled",
                env=env,
            )

            self.assertEqual(17, result.returncode, result.stderr)
            history_path = codex_home / "pitcrew/getbill/history.jsonl"
            self.assertTrue(history_path.is_file())
            history = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]
            latest = history[-1]
            self.assertEqual("failed", latest["outcome"])
            self.assertEqual(17, latest["exit_code"])
            self.assertEqual("gpt-5.6-terra", latest["model"])
            self.assertEqual(
                {
                    "input_tokens": 7,
                    "cached_input_tokens": 2,
                    "cache_write_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 16,
                },
                latest["usage"],
            )
            self.assertNotIn("thread_1", history_path.read_text(encoding="utf-8"))

    def test_scheduled_runner_records_command_launch_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            codex_home = root / ".codex"
            secret = "do-not-leak-this-command-path"
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(codex_home),
                "PITCREW_LOCK_ROOT": str(root / "locks"),
                "CODEX_BIN": str(root / secret),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)

            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--scheduled",
                env=env,
            )

            self.assertEqual(127, result.returncode)
            self.assertEqual("pitcrew lock: failed to launch command\n", result.stderr)
            self.assertNotIn(secret, result.stderr)
            history_path = codex_home / "pitcrew/getbill/history.jsonl"
            self.assertTrue(history_path.is_file())
            history = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]
            latest = history[-1]
            self.assertEqual("failed", latest["outcome"])
            self.assertIsNone(latest["exit_code"])
            self.assertEqual(
                "No bounded final summary was produced.",
                latest["summary"],
            )
            self.assertNotIn(secret, latest["summary"])
            self.assertEqual("gpt-5.6-terra", latest["model"])
            self.assertNotIn("usage", latest)

    def test_scheduled_runner_reuses_unlocked_file_after_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "PITCREW_LOCK_ROOT": str(root / "locks"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            lock = root / "locks/research-run.lock"
            lock.parent.mkdir(parents=True)
            lock.touch(mode=0o600)
            marker = root / "codex-called"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                f"#!/usr/bin/env bash\ntouch {marker}\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)

            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--scheduled",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(marker.exists())
            self.assertTrue(lock.is_file())
            self.assertEqual(0o600, lock.stat().st_mode & 0o777)

    def test_lock_survives_helper_crash_while_child_is_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            lock = root / "role.lock"
            first_marker = root / "first-started"
            first_pid = root / "first.pid"
            second_marker = root / "second-started"
            first_child = root / "first-child"
            first_child.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' \"$$\" > {first_pid}\n"
                f"touch {first_marker}\n"
                "sleep 10\n",
                encoding="utf-8",
            )
            first_child.chmod(0o755)
            second_child = root / "second-child"
            second_child.write_text(
                f"#!/usr/bin/env bash\ntouch {second_marker}\n",
                encoding="utf-8",
            )
            second_child.chmod(0o755)
            helper = ROOT / "scripts/pitcrew_locked_exec.py"

            first = subprocess.Popen(
                [
                    sys.executable,
                    str(helper),
                    "--lock-file",
                    str(lock),
                    "--project",
                    "getbill",
                    "--skill",
                    "research-run",
                    "--model",
                    "gpt-5.6-terra",
                    "--summary-file",
                    str(root / "first-summary.txt"),
                    "--history-file",
                    str(root / "history.jsonl"),
                    "--",
                    str(first_child),
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 3
            while not first_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(first_marker.exists(), "locked child did not start")

            first.kill()
            first.wait(timeout=3)
            try:
                second = subprocess.run(
                    [
                        sys.executable,
                        str(helper),
                        "--lock-file",
                        str(lock),
                        "--project",
                        "getbill",
                        "--skill",
                        "research-run",
                        "--model",
                        "gpt-5.6-terra",
                        "--summary-file",
                        str(root / "second-summary.txt"),
                        "--history-file",
                        str(root / "history.jsonl"),
                        "--",
                        str(second_child),
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=3,
                )
                self.assertEqual(0, second.returncode, second.stderr)
                self.assertEqual("noop", json.loads(second.stdout)["status"])
                self.assertFalse(second_marker.exists())
            finally:
                if first_pid.exists():
                    os.kill(int(first_pid.read_text(encoding="utf-8")), signal.SIGTERM)

    def test_locked_helper_finishes_when_descendant_keeps_stdout_open(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            descendant_pid = root / "descendant.pid"
            child = root / "child.py"
            child.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
                f"Path({str(descendant_pid)!r}).write_text(str(descendant.pid), encoding='utf-8')\n"
                "print('{\"usage\":{\"input_tokens\":1}}')\n",
                encoding="utf-8",
            )
            helper = ROOT / "scripts/pitcrew_locked_exec.py"
            try:
                result = subprocess.Popen(
                    [
                        sys.executable, str(helper), "--lock-file", str(root / "role.lock"),
                        "--project", "getbill", "--skill", "research-run",
                        "--model", "gpt-5.6-terra", "--summary-file", str(root / "summary.txt"),
                        "--history-file", str(root / "history.jsonl"), "--", sys.executable, str(child),
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result.wait(timeout=2)
            finally:
                if descendant_pid.exists():
                    os.kill(int(descendant_pid.read_text(encoding="utf-8")), signal.SIGTERM)

            self.assertEqual(0, result.returncode)
            history = [json.loads(line) for line in (root / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(history))
            self.assertEqual(1, history[0]["usage"]["input_tokens"])

    def test_locked_helper_discards_oversized_event_and_keeps_later_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            child = root / "child.py"
            child.write_text(
                "print('{\\\"type\\\":\\\"thread.started\\\",\\\"padding\\\":\\\"' + 'x' * (1024 * 1024 + 1) + '\\\"}')\n"
                "print('{\"usage\":{\"input_tokens\":9}}')\n",
                encoding="utf-8",
            )
            helper = ROOT / "scripts/pitcrew_locked_exec.py"
            result = subprocess.run(
                [
                    sys.executable, str(helper), "--lock-file", str(root / "role.lock"),
                    "--project", "getbill", "--skill", "research-run",
                    "--model", "gpt-5.6-terra", "--summary-file", str(root / "summary.txt"),
                    "--history-file", str(root / "history.jsonl"), "--", sys.executable, str(child),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=3,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            history = [json.loads(line) for line in (root / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(9, history[0]["usage"]["input_tokens"])

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
