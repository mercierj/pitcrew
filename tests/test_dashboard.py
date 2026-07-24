import http.client
import importlib.util
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

from scripts.pitcrew_dashboard import DashboardError, DashboardService


ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
SERVER_PATH = ROOT / "bin/pitcrew-dashboard"
SERVER_LOADER = SourceFileLoader(
    "pitcrew_dashboard_server",
    str(SERVER_PATH),
)
SERVER_SPEC = importlib.util.spec_from_loader(
    SERVER_LOADER.name,
    SERVER_LOADER,
)
SERVER = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(SERVER)
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
    def __init__(
        self,
        *,
        fail_glab=False,
        unavailable=False,
        issue_pages=None,
        merge_request_pages=None,
        schedule=None,
    ):
        self.calls = []
        self.fail_glab = fail_glab
        self.unavailable = unavailable
        self.issue_pages = issue_pages or [gitlab_issues()]
        self.merge_request_pages = merge_request_pages or [gitlab_merge_requests()]
        self.schedule = schedule or schedule_status()

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
            pages = (
                self.merge_request_pages
                if "/merge_requests?" in args[2]
                else self.issue_pages
            )
            page = int(args[2].rsplit("page=", 1)[1])
            payload = pages[page - 1] if page <= len(pages) else []
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
                stdout=json.dumps(self.schedule),
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
        self.assertIsNone(roles["implementer-run"]["estimated_next_pass"])
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
        runner.issue_pages[0][0]["description"] += (
            " https://attacker.invalid/other/project/-/merge_requests/999"
        )

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
        self.assertNotIn(
            "https://attacker.invalid/other/project/-/merge_requests/999",
            todo["related_merge_requests"],
        )
        self.assertEqual(2, len(work["merge_requests"]))
        encoded = "getbill1%2Fgetbill"
        self.assertEqual(
            [
                "glab",
                "api",
                f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100&page=1",
            ],
            runner.calls[0][0],
        )
        self.assertEqual(
            [
                "glab",
                "api",
                f"projects/{encoded}/merge_requests?scope=all&per_page=100&page=1",
            ],
            runner.calls[1][0],
        )

    def test_gitlab_collects_all_issue_and_merge_request_pages(self):
        issues = []
        for iid in range(1, 102):
            issue = dict(gitlab_issues()[0])
            issue.update(
                {
                    "iid": iid,
                    "web_url": f"https://gitlab.com/getbill1/getbill/-/issues/{iid}",
                    "description": "",
                    "references": {
                        "short": f"#{iid}",
                        "relative": f"#{iid}",
                        "full": f"getbill1/getbill#{iid}",
                    },
                }
            )
            issues.append(issue)
        merge_requests = []
        for iid in range(1, 102):
            merge_request = dict(gitlab_merge_requests()[0])
            merge_request.update(
                {
                    "iid": iid,
                    "web_url": (
                        "https://gitlab.com/getbill1/getbill/-/merge_requests/"
                        f"{iid}"
                    ),
                    "description": "",
                    "references": {
                        "short": f"!{iid}",
                        "relative": f"!{iid}",
                        "full": f"getbill1/getbill!{iid}",
                    },
                }
            )
            merge_requests.append(merge_request)
        runner = FakeRunner(
            issue_pages=[issues[:100], issues[100:]],
            merge_request_pages=[
                merge_requests[:100],
                merge_requests[100:],
            ],
        )

        work = self.service(runner).gitlab_work(force_refresh=True)

        self.assertEqual(101, len(work["groups"]["todo"]))
        self.assertEqual(101, len(work["merge_requests"]))
        encoded = "getbill1%2Fgetbill"
        self.assertEqual(
            [
                [
                    "glab",
                    "api",
                    f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100&page=1",
                ],
                [
                    "glab",
                    "api",
                    f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100&page=2",
                ],
                [
                    "glab",
                    "api",
                    f"projects/{encoded}/merge_requests?scope=all&per_page=100&page=1",
                ],
                [
                    "glab",
                    "api",
                    f"projects/{encoded}/merge_requests?scope=all&per_page=100&page=2",
                ],
            ],
            [call[0] for call in runner.calls],
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

        self.assertEqual(2, len(runner.calls))
        self.assertTrue(all("status" in call[0] for call in runner.calls))
        starter.assert_not_called()

    def test_control_refreshes_cached_scheduler_eligibility_before_action(self):
        runner = FakeRunner()
        service = self.service(runner)
        service.snapshot()
        runner.schedule = [
            {
                **entry,
                "enabled": False,
                "reason": "disabled after snapshot",
            }
            if entry["skill"] == "research-run"
            else entry
            for entry in runner.schedule
        ]

        with mock.patch("scripts.pitcrew_dashboard.subprocess.Popen") as starter:
            with self.assertRaisesRegex(DashboardError, "disabled role"):
                service.control("trigger", "research-run")

        self.assertEqual(2, len(runner.calls))
        self.assertTrue(all("status" in call[0] for call in runner.calls))
        starter.assert_not_called()

    def test_control_eligibility_comes_from_scheduler_status(self):
        dynamic_entry = {
            "skill": "dynamic-run",
            "interval_seconds": 60,
            "enabled": True,
            "reason": "",
            "label": "io.getbill.pitcrew.getbill.dynamic-run",
            "loaded": True,
            "running": False,
            "pid": None,
        }
        runner = FakeRunner(schedule=[dynamic_entry])
        service = self.service(runner)
        process = mock.Mock(pid=1357)

        with mock.patch(
            "scripts.pitcrew_dashboard.subprocess.Popen",
            return_value=process,
        ) as starter:
            result = service.control("trigger", "dynamic-run")

        self.assertEqual({"accepted": True, "pid": 1357}, result)
        self.assertEqual(1, len(runner.calls))
        self.assertIn("status", runner.calls[0][0])
        self.assertEqual("dynamic-run", starter.call_args.args[0][1])

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
            runner.calls[2][0],
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
            runner.calls[4][0],
        )
        self.assertEqual({"accepted": True}, stopped)
        self.assertEqual({"accepted": True}, restarted)
        self.assertEqual(
            ["status", "status", "stop", "status", "install"],
            [
                next(
                    action
                    for action in ("status", "stop", "install")
                    if action in args
                )
                for args, _ in runner.calls
            ],
        )
        for _, kwargs in runner.calls:
            self.assertEqual(
                {"text": True, "capture_output": True, "check": False},
                kwargs,
            )

    def test_local_command_oserrors_are_scrubbed_and_bounded(self):
        raw_error = "Authorization: Basic local-secret\n" + "x" * 3000

        def failing_runner(args, **kwargs):
            raise OSError(raw_error)

        service = self.service(failing_runner)
        for operation in (
            service.snapshot,
            lambda: service.control("stop", "research-run"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(DashboardError) as raised:
                    operation()
                self.assertIn("Authorization: [REDACTED]", str(raised.exception))
                self.assertNotIn("local-secret", str(raised.exception))
                self.assertLessEqual(len(str(raised.exception)), 2048)

        with mock.patch(
            "scripts.pitcrew_dashboard.subprocess.Popen",
            side_effect=OSError(raw_error),
        ):
            with self.assertRaises(DashboardError) as raised:
                service.control("trigger", "research-run")
        self.assertIn("Authorization: [REDACTED]", str(raised.exception))
        self.assertNotIn("local-secret", str(raised.exception))
        self.assertLessEqual(len(str(raised.exception)), 2048)


class FakeDashboardService:
    def __init__(self):
        self.calls = []
        self.accepted_controls = []

    def snapshot(self):
        self.calls.append(("snapshot",))
        return {"project": "getbill", "counts": {"enabled": 7, "disabled": 6}}

    def history(self, skill, outcome):
        self.calls.append(("history", skill, outcome))
        return [{"skill": skill, "outcome": outcome}]

    def gitlab_work(self, force_refresh=False):
        self.calls.append(("gitlab", force_refresh))
        return {"degraded": False, "groups": {}}

    def control(self, action, skill):
        self.calls.append(("control", action, skill))
        if action not in {"trigger", "stop", "restart"}:
            raise DashboardError(f"unknown action: {action}")
        if skill not in ENABLED_SKILLS:
            raise DashboardError(f"unknown role: {skill}")
        if skill == "qa-run":
            raise DashboardError("disabled role: qa-run")
        self.accepted_controls.append((action, skill))
        return {"accepted": True, "pid": 9876}


class DashboardEntryPointTest(unittest.TestCase):
    def test_executable_imports_project_modules_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ["python3", str(SERVER_PATH), "--help"],
                cwd=temp,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--project", result.stdout)


class DashboardHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.asset_root = Path(self.temp.name).resolve()
        (self.asset_root / "assets").mkdir()
        (self.asset_root / "index.html").write_text(
            "<html><script>window.token='__PITCREW_SESSION_TOKEN__'</script></html>",
            encoding="utf-8",
        )
        (self.asset_root / "assets/app.js").write_text(
            "window.pitcrew = true;",
            encoding="utf-8",
        )
        (self.asset_root / "assets/styles.css").write_text(
            "body { color: black; }",
            encoding="utf-8",
        )
        self.service = FakeDashboardService()
        self.token = "local-session-token"
        self.assets_patch = mock.patch.object(
            SERVER,
            "ASSET_ROOT",
            self.asset_root,
        )
        self.assets_patch.start()
        self.server = SERVER.create_server(
            "127.0.0.1",
            0,
            self.service,
            self.token,
        )
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.assets_patch.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        request_headers = {"Host": self.host}
        if headers:
            request_headers.update(headers)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            payload,
        )
        connection.close()
        return result

    def assert_security_headers(self, headers):
        self.assertEqual("default-src 'self'", headers["content-security-policy"])
        self.assertEqual("nosniff", headers["x-content-type-options"])
        self.assertEqual("no-store", headers["cache-control"])

    def test_api_get_routes_return_json_and_forward_filters(self):
        status, headers, payload = self.request("GET", "/api/status")
        self.assertEqual(200, status)
        self.assertEqual("getbill", json.loads(payload)["project"])
        self.assert_security_headers(headers)

        status, _, payload = self.request(
            "GET",
            "/api/history?skill=research-run&outcome=noop",
        )
        self.assertEqual(200, status)
        self.assertEqual("research-run", json.loads(payload)[0]["skill"])

        status, _, payload = self.request("GET", "/api/gitlab?refresh=1")
        self.assertEqual(200, status)
        self.assertFalse(json.loads(payload)["degraded"])
        self.assertEqual(
            [
                ("snapshot",),
                ("history", "research-run", "noop"),
                ("gitlab", True),
            ],
            self.service.calls,
        )
        self.assertNotIn(self.token.encode(), payload)

    def test_fixed_assets_replace_only_index_token_and_reject_other_paths(self):
        status, headers, index = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn(self.token.encode(), index)
        self.assertNotIn(b"__PITCREW_SESSION_TOKEN__", index)
        self.assert_security_headers(headers)

        for path, expected in (
            ("/assets/app.js", b"window.pitcrew"),
            ("/assets/styles.css", b"body"),
        ):
            with self.subTest(path=path):
                status, _, payload = self.request("GET", path)
                self.assertEqual(200, status)
                self.assertIn(expected, payload)
                self.assertNotIn(self.token.encode(), payload)

        (self.asset_root / "assets/app.js").unlink()
        status, headers, payload = self.request("GET", "/assets/app.js")
        self.assertEqual(404, status)
        self.assertNotIn(self.token.encode(), payload)
        self.assert_security_headers(headers)

        for path in (
            "/missing",
            "/../index.html",
            "/assets/../index.html",
            "/assets/%2e%2e/index.html",
        ):
            with self.subTest(path=path):
                status, headers, payload = self.request("GET", path)
                self.assertEqual(404, status)
                self.assertNotIn(self.token.encode(), payload)
                self.assert_security_headers(headers)

    def test_fixed_asset_symlink_cannot_escape_asset_root(self):
        with tempfile.TemporaryDirectory() as outside_temp:
            outside = Path(outside_temp) / "outside.js"
            outside.write_text("window.stolen = true;", encoding="utf-8")
            app = self.asset_root / "assets/app.js"
            app.unlink()
            try:
                app.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            status, headers, payload = self.request("GET", "/assets/app.js")

        self.assertIn(status, {403, 404})
        self.assertNotIn(b"window.stolen", payload)
        self.assertNotIn(self.token.encode(), payload)
        self.assert_security_headers(headers)

    def test_post_action_requires_session_and_accepts_valid_request(self):
        body = json.dumps(
            {"action": "trigger", "skill": "research-run"}
        ).encode()
        status, headers, _ = self.request(
            "POST",
            "/api/actions",
            body,
            {"Content-Type": "application/json"},
        )
        self.assertEqual(403, status)
        self.assert_security_headers(headers)
        self.assertEqual([], self.service.calls)

        status, _, payload = self.request(
            "POST",
            "/api/actions",
            body,
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )
        self.assertEqual(202, status)
        self.assertTrue(json.loads(payload)["accepted"])
        self.assertEqual(
            [("control", "trigger", "research-run")],
            self.service.calls,
        )
        self.assertEqual(
            [("trigger", "research-run")],
            self.service.accepted_controls,
        )

    def test_host_and_origin_boundary(self):
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(200, status)
        status, _, _ = self.request(
            "GET",
            "/api/status",
            headers={"Origin": f"http://{self.host}"},
        )
        self.assertEqual(200, status)

        for headers in (
            {"Host": ""},
            {"Host": "localhost:8765"},
            {"Origin": "https://evil.example"},
            {"Origin": f"https://{self.host}"},
        ):
            with self.subTest(headers=headers):
                status, response_headers, _ = self.request(
                    "GET",
                    "/api/status",
                    headers=headers,
                )
                self.assertEqual(403, status)
                self.assert_security_headers(response_headers)

    def test_invalid_action_payloads_never_reach_controls(self):
        valid_headers = {
            "Content-Type": "application/json",
            "X-Pitcrew-Session": self.token,
        }
        cases = (
            (b"{", valid_headers, 400),
            (
                json.dumps({"action": "trigger"}).encode(),
                valid_headers,
                400,
            ),
            (
                json.dumps(
                    {
                        "action": "trigger",
                        "skill": "research-run",
                        "extra": True,
                    }
                ).encode(),
                valid_headers,
                400,
            ),
            (
                json.dumps({"action": 1, "skill": "research-run"}).encode(),
                valid_headers,
                400,
            ),
            (
                json.dumps({"action": "trigger", "skill": "research-run"}).encode(),
                {"X-Pitcrew-Session": self.token},
                400,
            ),
            (b"x" * 8193, valid_headers, 413),
        )
        for body, headers, expected in cases:
            with self.subTest(expected=expected, body=body[:20]):
                status, response_headers, _ = self.request(
                    "POST",
                    "/api/actions",
                    body,
                    headers,
                )
                self.assertEqual(expected, status)
                self.assert_security_headers(response_headers)
        self.assertEqual([], self.service.calls)

    def test_partial_action_body_times_out_safely_and_shutdown_is_prompt(self):
        request = (
            "POST /api/actions HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Origin: http://{self.host}\r\n"
            "Content-Type: application/json\r\n"
            f"X-Pitcrew-Session: {self.token}\r\n"
            "Content-Length: 100\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        ).encode()
        with (
            mock.patch.object(SERVER, "READ_TIMEOUT_SECONDS", 0.1),
            socket.create_connection(("127.0.0.1", self.port), timeout=1) as client,
        ):
            client.settimeout(1)
            client.sendall(request)
            response = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk

        self.assertIn(b"HTTP/1.0 408", response)
        self.assertIn(b"Content-Security-Policy: default-src 'self'", response)
        self.assertIn(b"X-Content-Type-Options: nosniff", response)
        self.assertIn(b"Cache-Control: no-store", response)
        self.assertNotIn(self.token.encode(), response)
        self.assertEqual([], self.service.calls)

        started = time.monotonic()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        elapsed = time.monotonic() - started
        self.assertFalse(self.thread.is_alive())
        self.assertLess(elapsed, 1)
        self.server = None

    def test_dashboard_error_is_safe_and_does_not_expose_session(self):
        status, headers, payload = self.request(
            "POST",
            "/api/actions",
            json.dumps({"action": "trigger", "skill": "qa-run"}).encode(),
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )
        self.assertEqual(403, status)
        self.assertEqual({"error": "action rejected"}, json.loads(payload))
        self.assertNotIn(self.token.encode(), payload)
        self.assert_security_headers(headers)

    def test_unknown_action_or_skill_is_safely_rejected_without_acceptance(self):
        headers = {
            "Content-Type": "application/json",
            "X-Pitcrew-Session": self.token,
            "Origin": f"http://{self.host}",
        }
        for request in (
            {"action": "delete", "skill": "research-run"},
            {"action": "trigger", "skill": "unknown-run"},
        ):
            with self.subTest(request=request):
                status, response_headers, payload = self.request(
                    "POST",
                    "/api/actions",
                    json.dumps(request).encode(),
                    headers,
                )

                self.assertEqual(403, status)
                self.assertEqual({"error": "action rejected"}, json.loads(payload))
                self.assertNotIn(self.token.encode(), payload)
                self.assert_security_headers(response_headers)

        self.assertEqual(
            [
                ("control", "delete", "research-run"),
                ("control", "trigger", "unknown-run"),
            ],
            self.service.calls,
        )
        self.assertEqual([], self.service.accepted_controls)

    def test_create_server_rejects_non_loopback_host(self):
        with self.assertRaises(ValueError):
            SERVER.create_server(
                "0.0.0.0",
                0,
                self.service,
                self.token,
            )


if __name__ == "__main__":
    unittest.main()
