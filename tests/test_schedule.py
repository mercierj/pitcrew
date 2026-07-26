import json
import importlib.util
import os
import plistlib
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "bin/pitcrew-schedule.py"


def load_scheduler_module():
    spec = importlib.util.spec_from_file_location("pitcrew_schedule_test", SCHEDULER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ScheduleTest(unittest.TestCase):
    def run_scheduler(self, *args, env=None):
        return subprocess.run(
            ["python3", str(SCHEDULER), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def fake_launchctl_env(self, root, script):
        fake_bin = root / "bin"
        fake_bin.mkdir()
        launchctl = fake_bin / "launchctl"
        launchctl.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
        launchctl.chmod(0o755)
        return {
            **os.environ,
            "HOME": str(root),
            "CODEX_HOME": str(root / ".codex"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }

    def test_getbill_schedule_enables_core_and_gates_human_or_unconfigured_roles(self):
        result = self.run_scheduler("list", "--project", "getbill", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        schedule = {item["skill"]: item for item in json.loads(result.stdout)}

        for skill in (
            "research-run",
            "manager-run",
            "implementer-run",
            "reviewer-run",
            "validator-run",
            "investigate-run",
            "stale-sweep",
            "unblock",
        ):
            self.assertTrue(schedule[skill]["enabled"], skill)

        for skill in (
            "qa-run",
            "coverage-run",
            "dev-verify-run",
            "ops-run",
            "releaser-run",
        ):
            self.assertFalse(schedule[skill]["enabled"], skill)
            self.assertTrue(schedule[skill]["reason"], skill)

    def test_runtime_state_defaults_and_transitions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = {**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")}

            status = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "status", "--project", "getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertEqual("running", status.stdout.strip())

            stopped = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "stop", "--project", "getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, stopped.returncode, stopped.stderr)

            status = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "status", "--project", "getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual("stopped", status.stdout.strip())
            self.assertEqual(0o600, (root / ".codex/pitcrew/getbill/execution-state.json").stat().st_mode & 0o777)

            resumed = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "resume", "--project", "getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, resumed.returncode, resumed.stderr)

    def test_runtime_state_rejects_unsafe_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "HOME": temp, "CODEX_HOME": str(Path(temp) / ".codex")}
            result = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "status", "--project", "../getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)

    def test_runtime_state_fsyncs_directory_after_replacing_flag(self):
        from scripts import pitcrew_runtime_state

        with tempfile.TemporaryDirectory() as temp:
            env = {"HOME": temp, "CODEX_HOME": str(Path(temp) / ".codex")}
            events = []
            real_replace = pitcrew_runtime_state.os.replace
            with (
                mock.patch.object(
                    pitcrew_runtime_state.os,
                    "fsync",
                    side_effect=lambda descriptor: events.append(("fsync", descriptor)),
                ),
                mock.patch.object(
                    pitcrew_runtime_state.os,
                    "replace",
                    side_effect=lambda source, target: (
                        events.append(("replace", source, target)),
                        real_replace(source, target),
                    )[1],
                ),
            ):
                pitcrew_runtime_state.write_state("getbill", "stopped", env)
            self.assertEqual(["fsync", "replace", "fsync"], [event[0] for event in events])

    def test_render_writes_only_enabled_launch_agents_with_bounded_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "LaunchAgents"
            env = {**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")}
            result = self.run_scheduler(
                "render",
                "--project",
                "getbill",
                "--output-dir",
                str(output),
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            plists = sorted(output.glob("io.getbill.pitcrew.getbill.*.plist"))
            self.assertEqual(10, len(plists))

            research = output / "io.getbill.pitcrew.getbill.research-run.plist"
            with research.open("rb") as handle:
                payload = plistlib.load(handle)
            self.assertEqual(1800, payload["StartInterval"])
            self.assertEqual(
                [
                    str(ROOT / "bin/pitcrew-codex.sh"),
                    "research-run",
                    "getbill",
                    "--scheduled",
                ],
                payload["ProgramArguments"],
            )
            self.assertEqual(str(ROOT), payload["WorkingDirectory"])
            self.assertEqual("/dev/null", payload["StandardOutPath"])
            self.assertEqual("/dev/null", payload["StandardErrorPath"])
            self.assertEqual(
                str(root / ".codex"),
                payload["EnvironmentVariables"]["CODEX_HOME"],
            )

            releaser = output / "io.getbill.pitcrew.getbill.releaser-run.plist"
            self.assertFalse(releaser.exists())
            self.assertEqual(
                0o700,
                (root / ".codex/pitcrew/getbill/logs").stat().st_mode & 0o777,
            )

    def test_launchd_labels_reject_unsafe_project_names(self):
        result = self.run_scheduler("list", "--project", "../getbill", "--json")
        self.assertEqual(2, result.returncode)
        self.assertIn("project", result.stderr.lower())

    def test_install_fails_when_launchctl_enable_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "LaunchAgents"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            launchctl = fake_bin / "launchctl"
            launchctl.write_text(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = 'enable' ]; then\n"
                "  echo 'enable failed' >&2\n"
                "  exit 23\n"
                "fi\n",
                encoding="utf-8",
            )
            launchctl.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            }

            result = self.run_scheduler(
                "install",
                "--project",
                "getbill",
                "--output-dir",
                str(output),
                env=env,
            )

            self.assertEqual(23, result.returncode)
            self.assertIn("enable failed", result.stderr)
            self.assertNotIn("installed", result.stdout)

    def test_stop_all_stops_enabled_jobs_and_status_reports_global_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"printf '%s\\n' \"$*\" >> {calls}\n",
            )
            stopped = self.run_scheduler("stop-all", "--project", "getbill", env=env)
            self.assertEqual(0, stopped.returncode, stopped.stderr)
            status = self.run_scheduler("status", "--project", "getbill", env=env)
            self.assertEqual(0, status.returncode, status.stderr)
            payload = json.loads(status.stdout)
            self.assertTrue(payload)
            self.assertTrue(all(item["global_state"] == "stopped" for item in payload))
            bootouts = [line for line in calls.read_text().splitlines() if "bootout" in line]
            self.assertEqual(10, len(bootouts))

    def test_stop_all_closes_admission_before_cancelling_and_booting_out(self):
        scheduler = load_scheduler_module()
        calls = []

        class Store:
            def set_project_state(self, project, state):
                calls.append(("set_project_state", project, state))

        class Dispatcher:
            def cancel_running(self, project, error_code):
                calls.append(("cancel_running", project, error_code))

        scheduler.write_state = lambda project, state, env: calls.append(("write_state", project, state))
        scheduler.launchctl = lambda *args, **kwargs: (
            calls.append(("bootout_enabled_jobs", "getbill"))
            or subprocess.CompletedProcess(args, 0, "", "")
        )
        self.assertEqual(
            0,
            scheduler.stop_all(
                "getbill",
                {"HOME": self.id()},
                lambda project, env: (Store(), Dispatcher(), {}),
            ),
        )
        self.assertEqual(
            [
                ("set_project_state", "getbill", "stopped"),
                ("write_state", "getbill", "stopped"),
                ("cancel_running", "getbill", "global_stop"),
                ("bootout_enabled_jobs", "getbill"),
            ],
            calls[:4],
        )

    def test_stop_all_still_cancels_and_boots_out_when_flag_persistence_fails(self):
        scheduler = load_scheduler_module()
        calls = []

        class Store:
            def set_project_state(self, project, state):
                calls.append(("set_project_state", project, state))

        class Dispatcher:
            def cancel_running(self, project, error_code):
                calls.append(("cancel_running", project, error_code))

        def broken_write(project, state, env):
            calls.append(("write_state", project, state))
            raise OSError("fsync failed")

        scheduler.write_state = broken_write
        scheduler.launchctl = lambda *args, **kwargs: (
            calls.append(("bootout", args[1]))
            or subprocess.CompletedProcess(args, 0, "", "")
        )
        error = StringIO()
        with mock.patch("sys.stderr", error):
            self.assertEqual(
                2,
                scheduler.stop_all(
                    "getbill",
                    {"HOME": self.id()},
                    lambda project, env: (Store(), Dispatcher(), {}),
                ),
            )
        self.assertIn("could not be persisted", error.getvalue())
        self.assertEqual(
            [
                ("set_project_state", "getbill", "stopped"),
                ("write_state", "getbill", "stopped"),
                ("cancel_running", "getbill", "global_stop"),
            ],
            calls[:3],
        )
        self.assertEqual(10, len([call for call in calls if call[0] == "bootout"]))

    def test_resume_reopens_admission_and_drains_once_after_install(self):
        scheduler = load_scheduler_module()
        calls = []

        class Store:
            def set_project_state(self, project, state):
                calls.append(("set_project_state", project, state))

        class Dispatcher:
            def reconcile_and_drain(self, project, capacities):
                calls.append(("reconcile_and_drain", project, capacities))

        scheduler.write_state = lambda project, state, env: calls.append(("write_state", project, state))
        scheduler.install = lambda project, output, env: calls.append(("install", project)) or 0
        self.assertEqual(
            0,
            scheduler.resume_all(
                "getbill",
                Path("/tmp/agents"),
                {"HOME": self.id()},
                lambda project, env: (Store(), Dispatcher(), {"implementer-run": 3}),
            ),
        )
        self.assertEqual(
            [
                ("write_state", "getbill", "running"),
                ("install", "getbill"),
                ("set_project_state", "getbill", "running"),
                ("reconcile_and_drain", "getbill", {"implementer-run": 3}),
            ],
            calls,
        )

    def test_resume_restores_stopped_compatibility_flag_when_install_fails(self):
        scheduler = load_scheduler_module()
        calls = []
        scheduler.write_state = lambda project, state, env: calls.append((project, state))
        scheduler.install = lambda project, output, env: 23
        self.assertEqual(
            23,
            scheduler.resume_all("getbill", Path("/tmp/agents"), {"HOME": self.id()}),
        )
        self.assertEqual([("getbill", "running"), ("getbill", "stopped")], calls)

    def test_resume_rolls_back_when_coordinator_open_or_recovery_fails(self):
        scheduler = load_scheduler_module()
        calls = []
        scheduler.write_state = lambda project, state, env: calls.append(("write", state))
        scheduler.install = lambda project, output, env: 0

        with self.assertRaisesRegex(RuntimeError, "open failed"):
            scheduler.resume_all(
                "getbill",
                Path("/tmp/agents"),
                {"HOME": self.id()},
                lambda project, env: (_ for _ in ()).throw(RuntimeError("open failed")),
            )
        self.assertEqual([("write", "running"), ("write", "stopped")], calls)

        class Store:
            def set_project_state(self, project, state):
                calls.append(("store", state))

        class Dispatcher:
            def reconcile_and_drain(self, **kwargs):
                raise RuntimeError("recovery failed")

        calls.clear()
        with self.assertRaisesRegex(RuntimeError, "recovery failed"):
            scheduler.resume_all(
                "getbill",
                Path("/tmp/agents"),
                {"HOME": self.id()},
                lambda project, env: (Store(), Dispatcher(), {}),
            )
        self.assertEqual(
            [("write", "running"), ("store", "running"), ("store", "stopped"), ("write", "stopped")],
            calls,
        )

    def test_install_is_rejected_while_globally_stopped_and_resume_reinstalls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "LaunchAgents"
            env = self.fake_launchctl_env(root, "")
            self.assertEqual(0, self.run_scheduler("stop-all", "--project", "getbill", env=env).returncode)
            blocked = self.run_scheduler(
                "install", "--project", "getbill", "--output-dir", str(output), env=env
            )
            self.assertEqual(2, blocked.returncode)
            self.assertIn("stopped", blocked.stderr)
            resumed = self.run_scheduler(
                "resume-all", "--project", "getbill", "--output-dir", str(output), env=env
            )
            self.assertEqual(0, resumed.returncode, resumed.stderr)
            state = subprocess.run(
                ["python3", str(ROOT / "scripts/pitcrew_runtime_state.py"), "status", "--project", "getbill"],
                env=env, cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual("running", state.stdout.strip())

    def test_status_for_one_skill_reports_launchd_state_and_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"printf '%s\\n' \"$*\" >> {calls}\n"
                "if [ \"$1\" = 'print' ]; then\n"
                "  printf 'state = running\\npid = 1234\\nraw launchctl output\\n'\n"
                "fi\n",
            )

            result = self.run_scheduler(
                "status",
                "--project",
                "getbill",
                "--skill",
                "research-run",
                env=env,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(1, len(status))
            self.assertEqual("research-run", status[0]["skill"])
            self.assertEqual("io.getbill.pitcrew.getbill.research-run", status[0]["label"])
            self.assertEqual(1800, status[0]["interval_seconds"])
            self.assertTrue(status[0]["enabled"])
            self.assertEqual("", status[0]["reason"])
            self.assertTrue(status[0]["loaded"])
            self.assertTrue(status[0]["running"])
            self.assertEqual(1234, status[0]["pid"])
            self.assertNotIn("raw launchctl output", result.stdout)
            self.assertEqual(
                ["print gui/%d/io.getbill.pitcrew.getbill.research-run" % os.getuid()],
                calls.read_text(encoding="utf-8").splitlines(),
            )

    def test_install_for_one_skill_bootstraps_only_that_launch_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "LaunchAgents"
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"printf '%s\\n' \"$*\" >> {calls}\n",
            )

            result = self.run_scheduler(
                "install",
                "--project",
                "getbill",
                "--skill",
                "research-run",
                "--output-dir",
                str(output),
                env=env,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                ["io.getbill.pitcrew.getbill.research-run.plist"],
                [path.name for path in output.glob("*.plist")],
            )
            commands = calls.read_text(encoding="utf-8").splitlines()
            self.assertEqual(3, len(commands))
            self.assertTrue(all("research-run" in command for command in commands))
            self.assertEqual(
                1,
                sum(command.startswith("bootstrap ") for command in commands),
            )

    def test_stop_boots_out_only_the_selected_launch_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"printf '%s\\n' \"$*\" >> {calls}\n",
            )

            result = self.run_scheduler(
                "stop",
                "--project",
                "getbill",
                "--skill",
                "research-run",
                env=env,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [
                    "bootout gui/%d/io.getbill.pitcrew.getbill.research-run"
                    % os.getuid()
                ],
                calls.read_text(encoding="utf-8").splitlines(),
            )

    def test_stop_requires_skill_without_invoking_launchctl(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"touch {calls}\n",
            )

            result = self.run_scheduler(
                "stop",
                "--project",
                "getbill",
                env=env,
            )

            self.assertEqual(2, result.returncode)
            self.assertIn("--skill", result.stderr)
            self.assertFalse(calls.exists())

    def test_unknown_or_disabled_skill_exits_two_without_launchctl(self):
        for command in ("install", "status", "stop"):
            for skill in ("missing-run", "qa-run"):
                with (
                    self.subTest(command=command, skill=skill),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    root = Path(temp).resolve()
                    calls = root / "calls"
                    env = self.fake_launchctl_env(
                        root,
                        f"touch {calls}\n",
                    )
                    args = [
                        command,
                        "--project",
                        "getbill",
                        "--skill",
                        skill,
                    ]
                    if command == "install":
                        args.extend(["--output-dir", str(root / "LaunchAgents")])

                    result = self.run_scheduler(*args, env=env)

                    self.assertEqual(2, result.returncode)
                    self.assertIn("skill", result.stderr.lower())
                    self.assertFalse(calls.exists())

    def test_status_without_skill_returns_all_schedule_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            calls = root / "calls"
            env = self.fake_launchctl_env(
                root,
                f"printf '%s\\n' \"$*\" >> {calls}\n"
                "printf 'state = waiting\\n'\n",
            )

            result = self.run_scheduler(
                "status",
                "--project",
                "getbill",
                env=env,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(15, len(status))
            self.assertEqual(
                {entry["skill"] for entry in status},
                {
                    "research-run",
                    "security-run",
                    "product-discovery-run",
                    "manager-run",
                    "implementer-run",
                    "reviewer-run",
                    "validator-run",
                    "investigate-run",
                    "stale-sweep",
                    "qa-run",
                    "coverage-run",
                    "dev-verify-run",
                    "ops-run",
                    "unblock",
                    "releaser-run",
                },
            )
            self.assertEqual(15, len(calls.read_text(encoding="utf-8").splitlines()))

    def test_launchctl_failure_scrubs_credential_formats_and_limits_stderr(self):
        cases = (
            ("token=equals-token", "equals-token"),
            ("authorization=equals-authorization", "equals-authorization"),
            ("password=equals-password", "equals-password"),
            ("secret=equals-secret", "equals-secret"),
            ("token: colon-token", "colon-token"),
            ("Authorization: Bearer header-authorization", "header-authorization"),
            ("Authorization: Basic basic-secret", "basic-secret"),
            ("Authorization: Custom custom-secret", "custom-secret"),
            ("password: colon-password", "colon-password"),
            ("secret: colon-secret", "colon-secret"),
            ('"token":"json-token"', "json-token"),
            ('"authorization": "json-authorization"', "json-authorization"),
            ('"password":"json-password"', "json-password"),
            ('"secret": "json-secret"', "json-secret"),
        )
        for message, credential in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                env = self.fake_launchctl_env(
                    root,
                    f"printf '%s\\n%s' '{message}' "
                    "\"$(printf 'x%.0s' {1..1000})\" >&2\n"
                    "exit 41\n",
                )

                result = self.run_scheduler(
                    "stop",
                    "--project",
                    "getbill",
                    "--skill",
                    "research-run",
                    env=env,
                )

                self.assertEqual(41, result.returncode)
                self.assertIn("[REDACTED]", result.stderr)
                self.assertNotIn(credential, result.stderr)
                self.assertLessEqual(len(result.stderr), 501)
                if message.startswith("Authorization:"):
                    scheme = message.removeprefix("Authorization:").split()[0]
                    self.assertIn("Authorization: [REDACTED]", result.stderr)
                    self.assertNotIn(scheme, result.stderr)


if __name__ == "__main__":
    unittest.main()
