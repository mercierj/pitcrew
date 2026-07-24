import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts.pitcrew_dashboard import DashboardError, DashboardService


ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
ENABLED_SKILLS = (
    "research-run",
    "manager-run",
    "implementer-run",
    "reviewer-run",
    "validator-run",
    "investigate-run",
    "stale-sweep",
)
DISABLED_SKILLS = (
    "qa-run",
    "coverage-run",
    "dev-verify-run",
    "ops-run",
    "unblock",
    "releaser-run",
)


def runtime_config():
    return {
        "schema_version": 1,
        "project_name": "getbill",
        "providers": {"forge": "gitlab", "tracker": "gitlab"},
        "gitlab": {
            "host": "gitlab.com",
            "user": "operator",
            "owner": "getbill1",
            "project_path": "getbill1/getbill",
            "project_id": 59043683,
            "tracker": {
                "ticket_prefix": "getbill1/getbill#",
                "labels": {
                    "agent": "pitcrew-agent",
                    "investigate": "pitcrew-route::investigate",
                    "quick_win": "pitcrew-size::quick-win",
                    "bug": "pitcrew-type::bug",
                    "improvement": "pitcrew-type::improvement",
                },
                "states": {
                    state: f"pitcrew-state::{state}"
                    for state in ("todo", "processing", "review", "blocked", "done")
                },
            },
        },
        "repos": [{"name": "getbill", "path": "/tmp/getbill"}],
        "release": {"autonomy": "off"},
        "safety": {
            "confirm_each_remote_action": [],
            "allow_database_writes": False,
            "allow_destructive_git": False,
            "allow_secret_reads": False,
        },
    }


def schedule_status():
    intervals = {
        "research-run": 1800,
        "manager-run": 3600,
        "implementer-run": 900,
        "reviewer-run": 900,
        "validator-run": 900,
        "investigate-run": 1800,
        "stale-sweep": 21600,
        "qa-run": 7200,
        "coverage-run": 43200,
        "dev-verify-run": 900,
        "ops-run": 600,
        "unblock": 1800,
        "releaser-run": 900,
    }
    result = []
    for skill in (*ENABLED_SKILLS, *DISABLED_SKILLS):
        enabled = skill in ENABLED_SKILLS
        loaded = enabled and skill != "implementer-run"
        result.append(
            {
                "skill": skill,
                "interval_seconds": intervals[skill],
                "enabled": enabled,
                "reason": "" if enabled else f"{skill} is not configured",
                "label": f"io.getbill.pitcrew.getbill.{skill}",
                "loaded": loaded,
                "running": skill == "manager-run",
                "pid": 4321 if skill == "manager-run" else None,
            }
        )
    return result


def gitlab_issues():
    states = ("todo", "processing", "review", "blocked", "done")
    return [
        {
            "iid": index,
            "title": f"{state.title()} work",
            "state": "opened" if state != "done" else "closed",
            "web_url": f"https://gitlab.com/getbill1/getbill/-/issues/{index}",
            "description": (
                "Handled by !11 and "
                "https://gitlab.com/getbill1/getbill/-/merge_requests/12"
                if state == "todo"
                else ""
            ),
            "references": {
                "short": f"#{index}",
                "relative": f"#{index}",
                "full": f"getbill1/getbill#{index}",
            },
            "labels": [
                "pitcrew-agent",
                f"pitcrew-state::{state}",
                "pitcrew-route::investigate",
                "pitcrew-source::research",
            ],
        }
        for index, state in enumerate(states, start=1)
    ]


def gitlab_merge_requests():
    return [
        {
            "iid": 11,
            "title": "First MR",
            "state": "opened",
            "web_url": "https://gitlab.com/getbill1/getbill/-/merge_requests/11",
            "description": "Closes #1",
            "references": {
                "short": "!11",
                "relative": "!11",
                "full": "getbill1/getbill!11",
            },
        },
        {
            "iid": 12,
            "title": "Second MR",
            "state": "merged",
            "web_url": "https://gitlab.com/getbill1/getbill/-/merge_requests/12",
            "description": "",
            "references": {
                "short": "!12",
                "relative": "!12",
                "full": "getbill1/getbill!12",
            },
        },
    ]


class FakeRunner:
    def __init__(self, *, fail_glab=False, unavailable=False):
        self.calls = []
        self.fail_glab = fail_glab
        self.unavailable = unavailable
        self.issues = gitlab_issues()
        self.merge_requests = gitlab_merge_requests()

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[0] == "glab":
            if self.unavailable:
                raise FileNotFoundError("glab")
            if self.fail_glab:
                return subprocess.CompletedProcess(
                    args,
                    17,
                    stdout="",
                    stderr='Authorization: Basic auth-secret\n' + "x" * 3000,
                )
            payload = (
                self.merge_requests
                if "/merge_requests?" in args[2]
                else self.issues
            )
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if "pitcrew-schedule.py" in args[1] and "status" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(schedule_status()),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class DashboardServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name).resolve()
        (self.runtime / "config.json").write_text(
            json.dumps(runtime_config()),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def service(self, runner, now=None):
        return DashboardService(
            project="getbill",
            runtime_dir=self.runtime,
            command_runner=runner,
            now=now or (lambda: FIXED_NOW),
        )

    def write_history(self):
        records = (
            {
                "project": "getbill",
                "skill": "research-run",
                "started_at": "2026-07-24T11:29:00+00:00",
                "finished_at": "2026-07-24T11:30:00+00:00",
                "outcome": "noop",
                "summary": json.dumps({"reason": "missing lessons.md"}),
                "exit_code": 0,
            },
            {
                "project": "getbill",
                "skill": "manager-run",
                "started_at": "2026-07-24T10:58:00+00:00",
                "finished_at": "2026-07-24T11:00:00+00:00",
                "outcome": "success",
                "summary": "queued work",
                "exit_code": 0,
            },
            {
                "project": "getbill",
                "skill": "implementer-run",
                "started_at": "2026-07-24T11:43:00+00:00",
                "finished_at": "2026-07-24T11:45:00+00:00",
                "outcome": "success",
                "summary": "implemented work",
                "exit_code": 0,
            },
        )
        (self.runtime / "history.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def test_snapshot_normalizes_schedule_history_health_and_next_pass(self):
        self.write_history()
        runner = FakeRunner()

        snapshot = self.service(runner).snapshot()

        self.assertEqual({"enabled": 7, "disabled": 6}, snapshot["counts"])
        self.assertEqual(7, len(snapshot["agents"]))
        self.assertEqual(6, len(snapshot["disabled_roles"]))
        roles = {
            role["skill"]: role
            for role in (*snapshot["agents"], *snapshot["disabled_roles"])
        }
        self.assertEqual(set((*ENABLED_SKILLS, *DISABLED_SKILLS)), set(roles))
        for role in roles.values():
            for field in (
                "loaded",
                "running",
                "interval_seconds",
                "latest_history",
                "health",
                "estimated_next_pass",
            ):
                self.assertIn(field, role)
        research = roles["research-run"]
        self.assertTrue(research["loaded"])
        self.assertFalse(research["running"])
        self.assertEqual(1800, research["interval_seconds"])
        self.assertEqual("noop", research["latest_history"]["outcome"])
        self.assertEqual("warning", research["health"])
        self.assertEqual(
            "2026-07-24T12:00:00+00:00",
            research["estimated_next_pass"],
        )
        self.assertEqual("healthy", roles["manager-run"]["health"])
        self.assertEqual("stopped", roles["implementer-run"]["health"])
        self.assertEqual(
            [
                "python3",
                str(ROOT / "bin/pitcrew-schedule.py"),
                "status",
                "--project",
                "getbill",
            ],
            runner.calls[0][0],
        )
        self.assertEqual(
            {"text": True, "capture_output": True, "check": False},
            runner.calls[0][1],
        )

    def test_history_filters_by_skill_and_outcome(self):
        self.write_history()
        service = self.service(FakeRunner())

        filtered = service.history("research-run", "noop")

        self.assertEqual(1, len(filtered))
        self.assertEqual("research-run", filtered[0]["skill"])
        self.assertEqual("noop", filtered[0]["outcome"])
        self.assertEqual([], service.history("manager-run", "noop"))

    def test_gitlab_groups_work_and_extracts_related_merge_requests(self):
        runner = FakeRunner()

        work = self.service(runner).gitlab_work(force_refresh=True)

        self.assertFalse(work["degraded"])
        self.assertIsNone(work["error"])
        self.assertEqual(FIXED_NOW.isoformat(), work["last_successful_refresh"])
        self.assertEqual(
            {"todo", "processing", "review", "blocked", "done"},
            set(work["groups"]),
        )
        self.assertTrue(all(len(group) == 1 for group in work["groups"].values()))
        todo = work["groups"]["todo"][0]
        self.assertEqual("todo", todo["lifecycle"])
        self.assertEqual("investigate", todo["route"])
        self.assertEqual("research", todo["source"])
        self.assertEqual(
            [
                "https://gitlab.com/getbill1/getbill/-/merge_requests/11",
                "https://gitlab.com/getbill1/getbill/-/merge_requests/12",
            ],
            todo["related_merge_requests"],
        )
        self.assertEqual(2, len(work["merge_requests"]))
        encoded = "getbill1%2Fgetbill"
        self.assertEqual(
            [
                "glab",
                "api",
                f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100",
            ],
            runner.calls[0][0],
        )
        self.assertEqual(
            [
                "glab",
                "api",
                f"projects/{encoded}/merge_requests?scope=all&per_page=100",
            ],
            runner.calls[1][0],
        )

    def test_gitlab_cache_lasts_sixty_seconds_and_force_refresh_bypasses_it(self):
        clock = [FIXED_NOW]
        runner = FakeRunner()
        service = self.service(runner, now=lambda: clock[0])

        first = service.gitlab_work()
        clock[0] += timedelta(seconds=59)
        cached = service.gitlab_work()
        clock[0] += timedelta(seconds=1)
        refreshed = service.gitlab_work()
        forced = service.gitlab_work(force_refresh=True)

        self.assertEqual(first, cached)
        self.assertEqual(6, len(runner.calls))
        self.assertEqual(FIXED_NOW.isoformat(), first["last_successful_refresh"])
        self.assertEqual(
            (FIXED_NOW + timedelta(seconds=60)).isoformat(),
            refreshed["last_successful_refresh"],
        )
        self.assertEqual(
            (FIXED_NOW + timedelta(seconds=60)).isoformat(),
            forced["last_successful_refresh"],
        )

    def test_gitlab_failures_degrade_without_breaking_local_snapshot(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                runner = FakeRunner(fail_glab=not unavailable, unavailable=unavailable)
                service = self.service(runner)

                work = service.gitlab_work(force_refresh=True)
                snapshot = service.snapshot()
                cached = service.gitlab_work()

                self.assertTrue(work["degraded"])
                self.assertIsNone(work["last_successful_refresh"])
                self.assertEqual(
                    {
                        "todo": [],
                        "processing": [],
                        "review": [],
                        "blocked": [],
                        "done": [],
                    },
                    work["groups"],
                )
                self.assertEqual([], work["merge_requests"])
                self.assertTrue(work["error"])
                self.assertNotIn("auth-secret", work["error"])
                self.assertLessEqual(len(work["error"]), 2048)
                self.assertEqual(7, snapshot["counts"]["enabled"])
                self.assertEqual(work, cached)

    def test_control_rejects_invalid_requests_before_invocation(self):
        runner = FakeRunner()
        service = self.service(runner)
        with mock.patch("scripts.pitcrew_dashboard.subprocess.Popen") as starter:
            for action, skill in (
                ("delete", "research-run"),
                ("trigger", "unknown-run"),
                ("stop", "qa-run"),
            ):
                with self.subTest(action=action, skill=skill):
                    with self.assertRaises(DashboardError):
                        service.control(action, skill)

        self.assertEqual([], runner.calls)
        starter.assert_not_called()

    def test_control_uses_exact_safe_argument_arrays(self):
        runner = FakeRunner()
        service = self.service(runner)
        process = mock.Mock(pid=2468)
        with mock.patch(
            "scripts.pitcrew_dashboard.subprocess.Popen",
            return_value=process,
        ) as starter:
            triggered = service.control("trigger", "research-run")
            stopped = service.control("stop", "research-run")
            restarted = service.control("restart", "research-run")

        starter.assert_called_once_with(
            [
                str(ROOT / "bin/pitcrew-codex.sh"),
                "research-run",
                "getbill",
                "--scheduled",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertEqual({"accepted": True, "pid": 2468}, triggered)
        self.assertEqual(
            [
                "python3",
                str(ROOT / "bin/pitcrew-schedule.py"),
                "stop",
                "--project",
                "getbill",
                "--skill",
                "research-run",
            ],
            runner.calls[0][0],
        )
        self.assertEqual(
            [
                "python3",
                str(ROOT / "bin/pitcrew-schedule.py"),
                "install",
                "--project",
                "getbill",
                "--skill",
                "research-run",
            ],
            runner.calls[1][0],
        )
        self.assertEqual({"accepted": True}, stopped)
        self.assertEqual({"accepted": True}, restarted)
        for _, kwargs in runner.calls:
            self.assertEqual(
                {"text": True, "capture_output": True, "check": False},
                kwargs,
            )


if __name__ == "__main__":
    unittest.main()
