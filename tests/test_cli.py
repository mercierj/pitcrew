import os
import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CoordinatedLockedExecTest(unittest.TestCase):
    def test_coordinated_spawn_failure_with_store_failure_is_safe(self):
        scripts_path = str(ROOT / "scripts")
        sys.path.insert(0, scripts_path)
        try:
            import pitcrew_locked_exec as helper
            class Store:
                def __init__(self, path): pass
                def get(self, run_id): return {"project": "demo", "skill": "qa-run", "state": "running", "pid": None}
                def mark_pid(self, *args): return {}
                def finish(self, *args, **kwargs): raise helper.RunStoreError("down")
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                history = mock.Mock()
                args = ["locked", "--lock-file", str(root / "lock"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "summary"), "--history-file", str(root / "history"), "--live-file", str(root / "live"), "--run-db", str(root / "runs.sqlite"), "--run-id", "id", "--", "missing"]
                stderr = __import__("io").StringIO()
                with mock.patch.object(sys, "argv", args), mock.patch.object(helper, "RunStore", Store), mock.patch.object(helper, "HistoryStore", return_value=history), mock.patch.object(helper.subprocess, "Popen", side_effect=OSError()), mock.patch("sys.stderr", stderr):
                    self.assertEqual(2, helper.main())
                self.assertIn("run store is unavailable", stderr.getvalue()); history.append.assert_called()
        finally:
            sys.path.remove(scripts_path)

    def test_coordinated_terminal_finish_store_failure_is_safe(self):
        scripts_path = str(ROOT / "scripts")
        sys.path.insert(0, scripts_path)
        try:
            import pitcrew_locked_exec as helper
            class Store:
                def __init__(self, path): pass
                def get(self, run_id): return {"project": "demo", "skill": "qa-run", "state": "running", "pid": None}
                def mark_pid(self, *args): return {}
                def finish(self, *args, **kwargs): raise helper.RunStoreError("down")
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                history = mock.Mock(); stderr = __import__("io").StringIO()
                args = ["locked", "--lock-file", str(root / "lock"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "summary"), "--history-file", str(root / "history"), "--live-file", str(root / "live"), "--run-db", str(root / "runs.sqlite"), "--run-id", "id", "--", sys.executable, "-c", "pass"]
                with mock.patch.object(sys, "argv", args), mock.patch.object(helper, "RunStore", Store), mock.patch.object(helper, "HistoryStore", return_value=history), mock.patch("sys.stderr", stderr):
                    self.assertEqual(2, helper.main())
                self.assertIn("run store is unavailable", stderr.getvalue()); history.append.assert_called()
        finally:
            sys.path.remove(scripts_path)
    def helper(self, root, run, command, *extra):
        return subprocess.run([
            sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "run.lock"),
            "--project", "demo", "--skill", "qa-run", "--model", "test", "--summary-file", str(root / "summary"),
            "--history-file", str(root / "history"), "--run-db", str(root / "private" / "runs.sqlite"),
            "--run-id", run["run_id"], *extra, "--", *command], cwd=ROOT, text=True, capture_output=True, check=False)

    def test_coordinated_success_finishes_run_and_clears_live(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            store = RunStore(root / "private" / "runs.sqlite")
            run = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="ABC-1")
            store.claim_ready(project="demo", capacities={"qa-run": 1})
            live = root / "live.json"
            result = self.helper(root, run, [sys.executable, "-c", "raise SystemExit(0)"], "--live-file", str(live))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("succeeded", store.get(run["run_id"])["state"])
            self.assertFalse(live.exists())

    def test_coordinated_failure_and_launch_error_finalize_run(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); store = RunStore(root / "private" / "runs.sqlite")
            failed = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one")
            store.claim_ready(project="demo", capacities={"qa-run": 1})
            self.assertEqual(7, self.helper(root, failed, [sys.executable, "-c", "raise SystemExit(7)"]).returncode)
            self.assertEqual(("failed", "command_failed"), (store.get(failed["run_id"])["state"], store.get(failed["run_id"])["error_code"]))
            missing = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="two")
            store.claim_ready(project="demo", capacities={"qa-run": 1})
            self.assertEqual(127, self.helper(root, missing, ["/missing-executable"]).returncode)
            self.assertEqual(("failed", "spawn_failed"), (store.get(missing["run_id"])["state"], store.get(missing["run_id"])["error_code"]))

    def test_coordinated_does_not_replace_dispatcher_pid(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); store = RunStore(root / "private" / "runs.sqlite")
            run = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one")
            store.claim_ready(project="demo", capacities={"qa-run": 1}); store.mark_pid(run["run_id"], 4321)
            self.assertEqual(0, self.helper(root, run, [sys.executable, "-c", "raise SystemExit(0)"]).returncode)
            self.assertEqual(4321, store.get(run["run_id"])["pid"])

    def test_coordinated_flag_pairing_and_heartbeat_are_validated(self):
        helper = ROOT / "scripts/pitcrew_locked_exec.py"
        base = [sys.executable, str(helper), "--lock-file", "/tmp/x", "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", "/tmp/a", "--history-file", "/tmp/b"]
        self.assertEqual(2, subprocess.run([*base, "--run-id", "x", "--", "true"], cwd=ROOT, capture_output=True).returncode)
        self.assertEqual(2, subprocess.run([*base, "--run-db", "/tmp/runs.sqlite", "--", "true"], cwd=ROOT, capture_output=True).returncode)
        self.assertEqual(2, subprocess.run([*base, "--heartbeat-seconds", "0", "--", "true"], cwd=ROOT, capture_output=True).returncode)

    def test_coordinated_silent_child_heartbeats_and_signal_is_interrupted(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); store = RunStore(root / "private" / "runs.sqlite")
            run = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one"); store.claim_ready(project="demo", capacities={"qa-run": 1})
            live = root / "live"; command = [sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "lock"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "summary"), "--history-file", str(root / "history"), "--live-file", str(live), "--run-db", str(root / "private" / "runs.sqlite"), "--run-id", run["run_id"], "--heartbeat-seconds", ".05", "--", sys.executable, "-c", "import time; time.sleep(.25)"]
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 2
            while not live.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual(run["run_id"], json.loads(live.read_text())["run_id"])
            initial = store.get(run["run_id"])["heartbeat_at"]; time.sleep(.12)
            self.assertGreater(store.get(run["run_id"])["heartbeat_at"], initial)
            _, _ = process.communicate(timeout=2); self.assertEqual(0, process.returncode); self.assertFalse(live.exists())

    def test_coordinated_terminal_race_and_interrupted_do_not_overwrite(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); store = RunStore(root / "private" / "runs.sqlite")
            run = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one"); store.claim_ready(project="demo", capacities={"qa-run": 1})
            self.assertNotEqual(0, self.helper(root, run, [sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"]).returncode)
            self.assertEqual("command_failed", store.get(run["run_id"])["error_code"])
            race = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="two"); store.claim_ready(project="demo", capacities={"qa-run": 1})
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "race"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "s2"), "--history-file", str(root / "h2"), "--run-db", str(root / "private" / "runs.sqlite"), "--run-id", race["run_id"], "--", sys.executable, "-c", "import time; time.sleep(.2)"], cwd=ROOT)
            time.sleep(.05); store.finish(race["run_id"], state="cancelled"); process.communicate(timeout=2)
            self.assertEqual("cancelled", store.get(race["run_id"])["state"])

    def test_same_run_lock_is_noop_while_distinct_locks_overlap(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); store = RunStore(root / "private" / "runs.sqlite")
            one = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one"); two = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="two")
            store.claim_ready(project="demo", capacities={"qa-run": 2})
            common = ["--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "s"), "--history-file", str(root / "h"), "--run-db", str(root / "private" / "runs.sqlite")]
            command = [sys.executable, "-c", "import time; time.sleep(.25)"]
            first = subprocess.Popen([sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "one.lock"), *common, "--run-id", one["run_id"], "--", *command], cwd=ROOT, stdout=subprocess.PIPE, text=True)
            time.sleep(.05)
            duplicate = subprocess.run([sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "one.lock"), *common, "--run-id", one["run_id"], "--", *command], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual("noop", json.loads(duplicate.stdout)["status"]); self.assertEqual("running", store.get(one["run_id"])["state"])
            second = subprocess.Popen([sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "two.lock"), *common, "--run-id", two["run_id"], "--", *command], cwd=ROOT)
            first.communicate(timeout=2); second.communicate(timeout=2)
            self.assertEqual(0, first.returncode); self.assertEqual(0, second.returncode)

    def test_coordinated_store_failure_during_heartbeat_kills_child(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); db = root / "private" / "runs.sqlite"
            store = RunStore(db)
            run = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="one")
            store.claim_ready(project="demo", capacities={"qa-run": 1})
            live, pidfile, backup = root / "live", root / "child.pid", db.with_name("runs.backup.sqlite")
            child = f"from pathlib import Path; import os,time; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(10)"
            command = [sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "lock"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "summary"), "--history-file", str(root / "history"), "--live-file", str(live), "--run-db", str(db), "--run-id", run["run_id"], "--heartbeat-seconds", ".05", "--", sys.executable, "-c", child]
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 2
                while (not pidfile.exists() or not live.exists()) and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(pidfile.exists()); self.assertTrue(live.exists())
                db.rename(backup); db.symlink_to(root / "missing.sqlite")
                stdout, stderr = process.communicate(timeout=3)
                self.assertEqual(2, process.returncode, stdout + stderr); self.assertIn("run store is unavailable", stderr); self.assertFalse(live.exists())
                child_pid = int(pidfile.read_text()); deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try: os.kill(child_pid, 0)
                    except ProcessLookupError: break
                    time.sleep(.02)
                else: self.fail("child remains alive after run store failure")
            finally:
                if process.poll() is None:
                    process.kill(); process.communicate(timeout=2)
                if db.is_symlink(): db.unlink()
                if backup.exists(): backup.rename(db)
            restored = RunStore(db, pid_alive=lambda _: False)
            self.assertEqual("failed", restored.reconcile("demo")[0]["state"])

    def test_helper_sigterm_kills_child_process_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); pids = root / "pids"
            code = f"import subprocess,sys,os,time; from pathlib import Path; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); Path({str(pids)!r}).write_text(f'{{os.getpid()}} {{c.pid}}'); time.sleep(10)"
            command = [sys.executable, str(ROOT / "scripts/pitcrew_locked_exec.py"), "--lock-file", str(root / "lock"), "--project", "demo", "--skill", "qa-run", "--model", "x", "--summary-file", str(root / "s"), "--history-file", str(root / "h"), "--", sys.executable, "-c", code]
            helper = subprocess.Popen(command, cwd=ROOT)
            try:
                deadline = time.monotonic() + 2
                while not pids.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(pids.exists()); child, grandchild = map(int, pids.read_text().split())
                helper.terminate(); helper.wait(timeout=3)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    alive = []
                    for pid in (child, grandchild):
                        try: os.kill(pid, 0); alive.append(pid)
                        except ProcessLookupError: pass
                    if not alive: break
                    time.sleep(.02)
                self.assertEqual([], alive)
            finally:
                if helper.poll() is None: helper.kill(); helper.wait(timeout=2)


class CliTest(unittest.TestCase):
    def test_scheduled_run_result_schema_has_strict_contract(self):
        schema = json.loads(
            (ROOT / "references/run-result.schema.json").read_text(encoding="utf-8")
        )

        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual("object", schema["type"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            [
                "status", "reason", "project", "skill", "target_id", "did_work",
                "work_kind", "quality_outcome", "next_action",
            ],
            schema["required"],
        )
        for field in ("reason", "project", "skill", "quality_outcome", "next_action"):
            with self.subTest(field=field):
                self.assertEqual(
                    {"type": "string", "minLength": 1}, schema["properties"][field]
                )
        self.assertEqual(
            ["success", "noop", "blocked", "failed"],
            schema["properties"]["status"]["enum"],
        )
        self.assertEqual(
            ["string", "null"], schema["properties"]["target_id"]["type"]
        )
        self.assertEqual("boolean", schema["properties"]["did_work"]["type"])
        self.assertEqual(
            [
                "none", "implementation", "review", "validation", "investigation",
                "triage", "research", "security", "product", "operations", "release",
                "cleanup",
            ],
            schema["properties"]["work_kind"]["enum"],
        )

    def test_runner_passes_schema_only_to_scheduled_codex_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args_path = root / "codex-args"
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$FAKE_CODEX_ARGS\"\n"
                "previous=''\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then\n"
                "    printf '%s\\n' '{\"status\":\"success\",\"reason\":\"completed\",\"project\":\"getbill\",\"skill\":\"research-run\",\"target_id\":null,\"did_work\":true,\"work_kind\":\"research\",\"quality_outcome\":\"validated\",\"next_action\":\"review results\"}' > \"$argument\"\n"
                "  fi\n"
                "  previous=\"$argument\"\n"
                "done\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = {
                **os.environ,
                "CODEX_HOME": str(root / ".codex"),
                "CODEX_BIN": str(fake_codex),
                "FAKE_CODEX_ARGS": str(args_path),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            from scripts.pitcrew_run_store import RunStore
            runtime_dir = root / ".codex/pitcrew/getbill"
            runtime_dir.chmod(0o700)
            run_store = RunStore(runtime_dir / "runs.sqlite3")
            run = run_store.enqueue(project="getbill", skill="research-run", source="scheduled")
            run_store.claim_ready(project="getbill", capacities={"research-run": 1})

            scheduled = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", "--scheduled",
                "--coordinated-run", run["run_id"], env=env
            )
            self.assertEqual(0, scheduled.returncode, scheduled.stderr)
            args = args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, args.count("--output-schema"))
            schema_index = args.index("--output-schema")
            self.assertEqual(str(ROOT / "references/run-result.schema.json"), args[schema_index + 1])

            unscheduled = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", env=env
            )
            self.assertEqual(0, unscheduled.returncode, unscheduled.stderr)
            self.assertNotIn("--output-schema", args_path.read_text(encoding="utf-8").splitlines())

            manual_preprod = self.run_cli(
                "bin/pitcrew-codex.sh", "preprod-review-run", "getbill", env=env
            )
            self.assertEqual(0, manual_preprod.returncode, manual_preprod.stderr)
            self.assertEqual(
                0,
                args_path.read_text(encoding="utf-8").splitlines().count("--output-schema"),
            )

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

    def test_runner_refuses_to_start_when_global_stop_is_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "FAKE_CODEX_MARKER": str(root / "codex-started"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            stopped = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "stop", "--project", "getbill"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, stopped.returncode, stopped.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/usr/bin/env bash\ntouch \"$FAKE_CODEX_MARKER\"\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)

            result = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", "--scheduled", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("noop", json.loads(result.stdout)["status"])
            self.assertIn("global stop", json.loads(result.stdout)["reason"])
            self.assertFalse(Path(env["FAKE_CODEX_MARKER"]).exists())

    def test_runner_refuses_to_start_during_provider_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "FAKE_CODEX_MARKER": str(root / "codex-started"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            recorded = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/pitcrew_preflight.py"),
                    "record-provider-failure",
                    "--project", "getbill",
                    "--reason", "provider authentication failure",
                ],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, recorded.returncode, recorded.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\ntouch \"$FAKE_CODEX_MARKER\"\n", encoding="utf-8"
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)
            result = self.run_cli(
                "bin/pitcrew-codex.sh", "reviewer-run", "getbill", "--scheduled", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("noop", payload["status"])
            self.assertIn("cooldown", payload["reason"])
            self.assertFalse(Path(env["FAKE_CODEX_MARKER"]).exists())
            self.assertFalse((root / ".codex/pitcrew/getbill/runs.sqlite3").exists())

    def test_authentication_failure_opens_provider_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "FAKE_CODEX_MARKER": str(root / "codex-started"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "previous=''\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then\n"
                "    printf '%s\\n' 'GitLab authentication unavailable: invalid_grant' > \"$argument\"\n"
                "  fi\n"
                "  previous=\"$argument\"\n"
                "done\n"
                "touch \"$FAKE_CODEX_MARKER\"\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)
            first = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", "--scheduled", env=env
            )
            self.assertEqual(0, first.returncode, first.stderr)
            circuit = root / ".codex/pitcrew/getbill/state/provider-circuit.json"
            deadline = time.monotonic() + 3
            while not circuit.is_file() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(circuit.is_file())
            Path(env["FAKE_CODEX_MARKER"]).unlink()
            second = self.run_cli(
                "bin/pitcrew-codex.sh", "research-run", "getbill", "--scheduled", env=env
            )
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual("noop", json.loads(second.stdout)["status"])
            self.assertFalse(Path(env["FAKE_CODEX_MARKER"]).exists())

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

    def test_runner_dry_run_forwards_directed_ticket_target(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": str(Path(temp).resolve())}
            init = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, init.returncode, init.stderr)
            target = "https://gitlab.com/getbill1/getbill/-/issues/1"
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "implementer-run",
                "getbill",
                "--target",
                target,
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(f"directed target: {target}", result.stdout)

    def test_scheduled_unblock_prompt_requires_persisted_dashboard_state(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": str(Path(temp).resolve())}
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "unblock",
                "getbill",
                "--scheduled",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("unblock-state.json", result.stdout)
            self.assertIn("dashboard reads that file", result.stdout)

    def test_implementer_uses_git_metadata_capable_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
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
                "previous=''\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then\n"
                "    printf '%s\\n' 'bounded summary' > \"$argument\"\n"
                "  fi\n"
                "  previous=\"$argument\"\n"
                "done\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)
            result = self.run_cli(
                "bin/pitcrew-codex.sh", "implementer-run", "getbill", "--scheduled", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            deadline = time.monotonic() + 3
            while not (root / "codex-args").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            args = (root / "codex-args").read_text(encoding="utf-8")
            self.assertIn("--sandbox\ndanger-full-access", args)

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
            first_payload = json.loads(stdout)
            payload = json.loads(overlapping.stdout)
            self.assertEqual(first_payload["run_id"], payload["run_id"])
            self.assertTrue(first_payload["created"])
            self.assertFalse(payload["created"])

            args = Path(env["FAKE_CODEX_ARGS"]).read_text(encoding="utf-8")
            self.assertIn("--ephemeral", args)
            self.assertIn("--model", args)
            self.assertIn("gpt-5.6-terra", args)
            self.assertIn("--json", args)
            self.assertIn("--add-dir\n/Users/jo/Prog/getbill", args)
            self.assertIn("--sandbox", args)
            self.assertIn("workspace-write", args)
            self.assertIn("sandbox_workspace_write.network_access=true", args)
            self.assertIn("--output-last-message", args)
            self.assertIn(
                ".codex/pitcrew/getbill/logs/runs/",
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
                next((codex_home / "pitcrew/getbill/logs/runs").glob("*.last.txt")).stat().st_mode & 0o777,
            )
            history_path = codex_home / "pitcrew/getbill/history.jsonl"
            deadline = time.monotonic() + 4
            while not history_path.is_file() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(history_path.is_file())
            history = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["success"], [record["outcome"] for record in history])
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

    def test_scheduled_runner_queues_three_distinct_targets_then_drains_fifo(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate, started = root / "gate", root / "started"
            gate.touch()
            started.mkdir()
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "FAKE_GATE": str(gate),
                "FAKE_STARTED": str(started),
            }
            configured = self.run_cli("bin/configure.sh", "getbill", "--profile", "getbill", env=env)
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "previous=''\nfor argument in \"$@\"; do\n"
                "  if [ \"$previous\" = '--output-last-message' ]; then printf 'done\\n' > \"$argument\"; touch \"$FAKE_STARTED/$(basename \"$argument\").started\"; fi\n"
                "  previous=\"$argument\"\ndone\n"
                "while [ -e \"$FAKE_GATE\" ]; do sleep .02; done\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)
            targets = [f"https://gitlab.com/getbill1/getbill/-/issues/{number}" for number in range(1, 5)]
            responses = [
                self.run_cli("bin/pitcrew-codex.sh", "implementer-run", "getbill", "--target", target, "--scheduled", env=env)
                for target in targets
            ]
            self.assertTrue(all(response.returncode == 0 for response in responses))
            runs = [json.loads(response.stdout) for response in responses]
            store = RunStore(root / ".codex/pitcrew/getbill/runs.sqlite3")
            deadline = time.monotonic() + 3
            while len(list(started.glob("*.started"))) < 3 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(3, len(list(started.glob("*.started"))))
            self.assertEqual(["running", "running", "running", "queued"], [store.get(run["run_id"])["state"] for run in runs])
            gate.unlink()
            deadline = time.monotonic() + 5
            while len(list(started.glob("*.started"))) < 4 and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(4, len(list(started.glob("*.started"))))
            self.assertIn(store.get(runs[3]["run_id"])["state"], {"running", "succeeded"})
            deadline = time.monotonic() + 5
            while any(store.get(run["run_id"])["state"] == "running" for run in runs) and time.monotonic() < deadline:
                time.sleep(.05)

    def test_scheduled_runner_is_idempotent_for_same_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            gate = root / "gate"
            gate.touch()
            env = {**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex"), "FAKE_GATE": str(gate)}
            configured = self.run_cli("bin/configure.sh", "getbill", "--profile", "getbill", env=env)
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/usr/bin/env bash\nwhile [ -e \"$FAKE_GATE\" ]; do sleep .02; done\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            env["CODEX_BIN"] = str(fake_codex)
            target = "https://gitlab.com/getbill1/getbill/-/issues/42"
            first = self.run_cli("bin/pitcrew-codex.sh", "implementer-run", "getbill", "--target", target, "--scheduled", env=env)
            second = self.run_cli("bin/pitcrew-codex.sh", "implementer-run", "getbill", "--target", target, "--scheduled", env=env)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            first_payload, second_payload = json.loads(first.stdout), json.loads(second.stdout)
            self.assertEqual(first_payload["run_id"], second_payload["run_id"])
            self.assertTrue(first_payload["created"])
            self.assertFalse(second_payload["created"])
            gate.unlink()

    def test_runner_rejects_untrusted_coordinated_run_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")}
            configured = self.run_cli("bin/configure.sh", "getbill", "--profile", "getbill", env=env)
            self.assertEqual(0, configured.returncode, configured.stderr)
            result = subprocess.run(
                [str(ROOT / "bin/pitcrew-codex.sh"), "implementer-run", "getbill", "--scheduled", "--coordinated-run", "not-a-uuid"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("coordinated run is invalid", result.stderr)

    def test_coordinated_runner_exports_only_its_run_id_to_codex(self):
        from scripts.pitcrew_run_store import RunStore
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")}
            configured = self.run_cli("bin/configure.sh", "getbill", "--profile", "getbill", env=env)
            self.assertEqual(0, configured.returncode, configured.stderr)
            fake_codex = root / "fake-codex"
            captured = root / "run-id"
            fake_codex.write_text(
                "#!/usr/bin/env bash\nprintf '%s' \"${PITCREW_RUN_ID-unset}\" > \"$FAKE_CODEX_ENV\"\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env.update({"CODEX_BIN": str(fake_codex), "FAKE_CODEX_ENV": str(captured)})
            runtime = root / ".codex/pitcrew/getbill"
            runtime.chmod(0o700)
            store = RunStore(runtime / "runs.sqlite3")
            target = "https://gitlab.com/getbill1/getbill/-/issues/42"
            run = store.enqueue(project="getbill", skill="implementer-run", source="scheduled", target=target)
            store.claim_ready(project="getbill", capacities={"implementer-run": 1})
            coordinated = subprocess.run(
                [str(ROOT / "bin/pitcrew-codex.sh"), "implementer-run", "getbill", "--target", target, "--scheduled", "--coordinated-run", run["run_id"]],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, coordinated.returncode, coordinated.stderr)
            self.assertEqual(run["run_id"], captured.read_text(encoding="utf-8"))

            legacy = subprocess.run(
                [str(ROOT / "bin/pitcrew-codex.sh"), "research-run", "getbill"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, legacy.returncode, legacy.stderr)
            self.assertEqual("unset", captured.read_text(encoding="utf-8"))

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

            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("run_id", payload)
            from scripts.pitcrew_run_store import RunStore
            store = RunStore(codex_home / "pitcrew/getbill/runs.sqlite3")
            deadline = time.monotonic() + 3
            while store.get(payload["run_id"])["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual("failed", store.get(payload["run_id"])["state"])
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

            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            from scripts.pitcrew_run_store import RunStore
            store = RunStore(codex_home / "pitcrew/getbill/runs.sqlite3")
            deadline = time.monotonic() + 3
            while store.get(payload["run_id"])["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual("failed", store.get(payload["run_id"])["state"])
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
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
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

    def test_locked_helper_writes_fallback_summary_when_child_omits_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            child = root / "child.py"
            child.write_text("raise SystemExit(7)\n", encoding="utf-8")
            summary = root / "summary.txt"
            live = root / "live.json"
            helper = ROOT / "scripts/pitcrew_locked_exec.py"

            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "--lock-file", str(root / "role.lock"),
                    "--project", "getbill",
                    "--skill", "implementer-run",
                    "--model", "gpt-5.6-sol",
                    "--summary-file", str(summary),
                    "--history-file", str(root / "history.jsonl"),
                    "--live-file", str(live),
                    "--", sys.executable, str(child),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=3,
            )

            self.assertEqual(7, result.returncode, result.stderr)
            self.assertTrue(summary.is_file())
            fallback = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual("failed", fallback["status"])
            self.assertEqual(7, fallback["exit_code"])
            self.assertFalse(live.exists())

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

    def test_locked_helper_ignores_bounded_json_with_oversized_integer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            child = root / "child.py"
            child.write_text(
                "print('{\\\"usage\\\":{\\\"input_tokens\\\":' + '9' * 5000 + '}}')\n"
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
