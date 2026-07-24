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


if __name__ == "__main__":
    unittest.main()
