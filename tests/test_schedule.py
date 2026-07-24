import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "bin/pitcrew-schedule.py"


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
        ):
            self.assertTrue(schedule[skill]["enabled"], skill)

        for skill in (
            "qa-run",
            "coverage-run",
            "dev-verify-run",
            "ops-run",
            "unblock",
            "releaser-run",
        ):
            self.assertFalse(schedule[skill]["enabled"], skill)
            self.assertTrue(schedule[skill]["reason"], skill)

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
            self.assertEqual(7, len(plists))

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

    def test_unknown_or_disabled_skill_exits_two_without_launchctl(self):
        for skill in ("missing-run", "qa-run"):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                calls = root / "calls"
                env = self.fake_launchctl_env(
                    root,
                    f"touch {calls}\n",
                )

                result = self.run_scheduler(
                    "status",
                    "--project",
                    "getbill",
                    "--skill",
                    skill,
                    env=env,
                )

                self.assertEqual(2, result.returncode)
                self.assertIn("skill", result.stderr.lower())
                self.assertFalse(calls.exists())

    def test_stop_propagates_launchctl_failure_with_scrubbed_limited_stderr(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            env = self.fake_launchctl_env(
                root,
                "printf 'permission denied TOKEN=supersecret\\n%s' "
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
            self.assertIn("permission denied", result.stderr)
            self.assertNotIn("supersecret", result.stderr)
            self.assertLessEqual(len(result.stderr), 600)


if __name__ == "__main__":
    unittest.main()
