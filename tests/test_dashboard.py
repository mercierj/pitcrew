import http.client
import importlib.util
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

from scripts.pitcrew_dashboard import DashboardError, DashboardService
from scripts.pitcrew_run_store import RunStore, RunStoreError


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
        "preprod_review": {
            "base_ref": "origin/preprod",
            "compare_ref": "origin/develop",
            "history_limit": 10,
        },
        "agents": {"research-run": {"model": "gpt-5.6-luna"}},
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
            "source_branch": "fix/payment-summary",
            "target_branch": "develop",
            "author": {"username": "agent-sol"},
            "head_pipeline": {"status": "failed"},
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
            "source_branch": "fix/old-work",
            "target_branch": "develop",
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
        self.mutation_responses = {}

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
            path = args[-1]
            if "-X" in args:
                path = args[2]
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=json.dumps(self.mutation_responses.get(path, {})),
                    stderr="",
                )
            if "&page=" not in path:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=json.dumps(self.mutation_responses.get(args[2], {})),
                    stderr="",
                )
            pages = (
                self.merge_request_pages
                if "/merge_requests?" in path
                else self.issue_pages
            )
            page = int(path.rsplit("page=", 1)[1])
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

    def service(self, runner, now=None, run_store=None, run_dispatcher=None, forge_work_factory=None):
        return DashboardService(
            project="getbill",
            runtime_dir=self.runtime,
            command_runner=runner,
            now=now or (lambda: FIXED_NOW),
            run_store=run_store,
            run_dispatcher=run_dispatcher,
            forge_work_factory=forge_work_factory,
        )

    def test_forge_work_delegates_to_injected_adapter_and_caches(self):
        calls = []

        class Adapter:
            def collect(self):
                calls.append("collect")
                return {"provider": "gitlab", "degraded": False, "groups": {}, "changes": []}

        service = self.service(FakeRunner(), forge_work_factory=lambda config, runner: Adapter())
        self.assertEqual("gitlab", service.forge_work()["provider"])
        self.assertEqual("gitlab", service.forge_work()["provider"])
        self.assertEqual(["collect"], calls)

    def test_forge_work_selects_github_adapter_without_factory_injection(self):
        config = runtime_config()
        config["providers"] = {"forge": "github", "tracker": "github"}
        config["github"] = {
            "host": "github.com",
            "user": "operator",
            "owner": "acme",
            "repository": "acme/payments",
            "tracker": {
                "ticket_prefix": "acme/payments#",
                "labels": {
                    "agent": "pitcrew-agent",
                    "bug": "bug",
                    "investigate": "investigate",
                    "quick_win": "quick-win",
                    "improvement": "improvement",
                },
                "states": {
                    state: f"pitcrew-state::{state}"
                    for state in ("todo", "processing", "review", "blocked", "done")
                },
            },
        }
        (self.runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")
        adapter = mock.Mock()
        adapter.collect.return_value = {
            "provider": "github",
            "degraded": False,
            "error": None,
            "groups": {},
            "changes": [],
        }

        with mock.patch(
            "scripts.pitcrew_dashboard.GitHubForgeWork",
            return_value=adapter,
        ) as github_adapter:
            work = self.service(FakeRunner()).forge_work(force_refresh=True)

        self.assertEqual("github", work["provider"])
        github_adapter.assert_called_once()
        adapter.collect.assert_called_once_with()

    def test_launch_ticket_agent_targets_validated_issue(self):
        store, dispatcher = self.coordinator()
        service = self.service(FakeRunner(), run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"

        result = service.launch_ticket_agent("implementer-run", target)

        self.assertEqual(
            {
                "accepted": True,
                "run_id": result["run_id"],
                "state": result["state"],
                "skill": "implementer-run",
                "target": target,
            },
            result,
        )
        with self.assertRaisesRegex(DashboardError, "ticket is not eligible"):
            service.launch_ticket_agent("unblock", target)

    def test_launch_ticket_agent_validates_github_provider_target(self):
        config = runtime_config()
        config["providers"] = {"forge": "github", "tracker": "github"}
        config["github"] = {
            "host": "github.com",
            "user": "operator",
            "owner": "acme",
            "repository": "acme/payments",
            "tracker": {
                "ticket_prefix": "acme/payments#",
                "labels": {
                    "agent": "pitcrew-agent",
                    "bug": "bug",
                    "investigate": "investigate",
                    "quick_win": "quick-win",
                    "improvement": "improvement",
                },
                "states": {
                    state: f"pitcrew-state::{state}"
                    for state in ("todo", "processing", "review", "blocked", "done")
                },
            },
        }
        (self.runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")
        target = "https://github.com/acme/payments/issues/12"

        class Adapter:
            def collect(self):
                return {
                    "provider": "github", "degraded": False, "error": None,
                    "changes": [], "groups": {"todo": [{
                        "labels": ["pitcrew-agent"], "lifecycle": "todo",
                        "canonical_url": target, "agent_action": None,
                    }]},
                }

        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(), run_store=store, run_dispatcher=dispatcher,
            forge_work_factory=lambda config, runner: Adapter(),
        )

        result = service.launch_ticket_agent("implementer-run", target)

        self.assertTrue(result["accepted"])
        self.assertEqual(target, result["target"])
        with self.assertRaisesRegex(DashboardError, "invalid ticket target"):
            service.launch_ticket_agent("implementer-run", "https://github.com/acme/payments/pull/12")

    def coordinator(self, first_drain_failure=None):
        class FakeDispatcher:
            def __init__(self, store):
                self.store = store
                self.drain_calls = []
                self.spawned_run_ids = []

            def reconcile_and_drain(self, *, project, capacities):
                self.drain_calls.append((project, dict(capacities)))
                if (
                    len(self.drain_calls) == 1
                    and first_drain_failure == "before_claim"
                ):
                    raise RunStoreError("drain failed before claim")
                claimed = self.store.claim_ready(project=project, capacities=capacities)
                self.spawned_run_ids.extend(run["run_id"] for run in claimed)
                if (
                    len(self.drain_calls) == 1
                    and first_drain_failure == "after_claim"
                ):
                    raise RunStoreError("drain failed after claim")
                return {"spawned": claimed}

        store = RunStore(self.runtime / "runs.sqlite3", now=lambda: FIXED_NOW)
        return store, FakeDispatcher(store)

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
                "model": "gpt-5.6-terra",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_tokens": 0,
                    "output_tokens": 10,
                    "total_tokens": 130,
                },
            },
            {
                "project": "getbill",
                "skill": "research-run",
                "started_at": "2026-07-24T10:58:00+00:00",
                "finished_at": "2026-07-24T10:59:00+00:00",
                "outcome": "success",
                "summary": "older work",
                "exit_code": 0,
                "model": "gpt-5.6-luna",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 10,
                    "cache_write_tokens": 10,
                    "output_tokens": 10,
                    "total_tokens": 40,
                },
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
                "model": "gpt-5.6-sol",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 10,
                },
            },
            {
                "project": "getbill",
                "skill": "qa-run",
                "started_at": "2026-07-24T10:43:00+00:00",
                "finished_at": "2026-07-24T10:45:00+00:00",
                "outcome": "success",
                "summary": "tested work",
                "exit_code": 0,
                "model": "gpt-5.6-luna",
                "usage": {
                    "input_tokens": 5,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 5,
                },
            },
        )
        (self.runtime / "history.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def write_pending_decision(self):
        state_dir = self.runtime / "state"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "unblock-state.json").write_text(
            json.dumps(
                {
                    "asked": {},
                    "pending_question": {
                        "ticket_id": "getbill1/getbill#1",
                        "asked_at": "2026-07-25T12:00:00Z",
                        "status": "blocked",
                        "question": "What should happen next?",
                        "choices": ["Ship it", "Investigate more"],
                        "shape": "generic",
                        "context": {"ticket_id": "getbill1/getbill#1"},
                    },
                    "history": [],
                }
            ),
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
        self.assertEqual(
            "Use when scanning one configured repository for high-confidence drift or hardening findings.",
            research["role_description"],
        )
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
        self.assertEqual("gpt-5.6-luna", research["configured_model"])
        self.assertEqual("medium", research["configured_reasoning_effort"])
        self.assertEqual("gpt-5.6-terra", research["latest_model"])
        self.assertEqual(1, research["latest_usage"]["measured_runs"])
        self.assertEqual(0, research["latest_usage"]["unmeasured_runs"])
        self.assertEqual(2, research["usage_7d"]["measured_runs"])
        self.assertEqual("110", research["usage_7d"]["tokens"]["input_tokens"])
        self.assertEqual("0.000488", research["usage_7d"]["estimated_cost_usd"])
        self.assertEqual(0, roles["manager-run"]["latest_usage"]["measured_runs"])
        self.assertEqual(0, roles["manager-run"]["latest_usage"]["unmeasured_runs"])
        self.assertIsNone(roles["manager-run"]["latest_model"])
        self.assertEqual("gpt-5.6-terra", roles["qa-run"]["configured_model"])
        self.assertEqual("gpt-5.6-luna", roles["qa-run"]["latest_model"])
        self.assertEqual(1, roles["qa-run"]["usage_7d"]["measured_runs"])
        self.assertIsNone(roles["coverage-run"]["latest_model"])
        self.assertEqual(0, roles["coverage-run"]["latest_usage"]["measured_runs"])
        self.assertEqual(0, roles["coverage-run"]["latest_usage"]["unmeasured_runs"])
        self.assertEqual(
            {"currency": "USD", "effective_date": "2026-07-24", "basis": "API standard token pricing estimate"},
            snapshot["pricing"],
        )
        self.assertTrue(
            all(set(entry) == {"profile", "label"} for entry in snapshot["model_catalog"].values())
        )

        self.assertEqual(4, snapshot["usage_7d"]["measured_runs"])
        self.assertEqual(1, snapshot["usage_7d"]["unmeasured_runs"])
        self.assertEqual("125", snapshot["usage_7d"]["tokens"]["input_tokens"])
        self.assertEqual("0.000544", snapshot["usage_7d"]["estimated_cost_usd"])
        self.assertEqual(4, snapshot["usage_total"]["measured_runs"])
        self.assertEqual(1, snapshot["usage_total"]["unmeasured_runs"])
        self.assertEqual("125", snapshot["usage_total"]["tokens"]["input_tokens"])
        self.assertEqual("0.000544", snapshot["usage_total"]["estimated_cost_usd"])
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

    def test_snapshot_marks_delivery_roles_as_event_driven_without_a_schedule(self):
        self.write_history()

        snapshot = self.service(FakeRunner()).snapshot()
        roles = {
            role["skill"]
            for role in (*snapshot["agents"], *snapshot["disabled_roles"])
        }
        by_skill = {
            role["skill"]: role
            for role in (*snapshot["agents"], *snapshot["disabled_roles"])
        }

        for skill in (
            "manager-run", "implementer-run", "reviewer-run", "validator-run",
            "investigate-run", "unblock",
        ):
            with self.subTest(skill=skill):
                self.assertIn(skill, roles)
                role = by_skill[skill]
                self.assertEqual("event", role["trigger_mode"])
                self.assertIsNone(role["interval_seconds"])
                self.assertIsNone(role["estimated_next_pass"])
                self.assertFalse(role["restartable"])

        self.assertEqual("schedule", by_skill["research-run"]["trigger_mode"])
        self.assertEqual(1800, by_skill["research-run"]["interval_seconds"])
        self.assertTrue(by_skill["research-run"]["restartable"])

    def test_decisions_returns_pending_question_with_context(self):
        self.write_pending_decision()
        service = self.service(FakeRunner())

        with mock.patch.object(
            service,
            "_gitlab_document",
            side_effect=[
                {
                    "title": "Stripe webhook validation",
                    "description": "Investigate the validation path.",
                    "web_url": "https://gitlab.com/getbill1/getbill/-/issues/1",
                },
                [{"body": "## Findings\nRoot cause is in webhook handling."}],
            ],
        ):
            decision = service.decisions()

        pending = decision["pending"]
        self.assertEqual("getbill1/getbill#1", pending["ticket_id"])
        self.assertEqual("What should happen next?", pending["question"])
        self.assertEqual(["Ship it", "Investigate more"], pending["choices"])
        self.assertEqual("Stripe webhook validation", pending["ticket"]["title"])
        self.assertEqual("issue", pending["ticket"]["resource_type"])
        self.assertEqual(
            "https://gitlab.com/getbill1/getbill/-/issues/1",
            pending["ticket"]["canonical_url"],
        )
        self.assertIn("Root cause", pending["findings"])

    def test_decisions_clears_pending_question_when_ticket_is_done(self):
        self.write_pending_decision()
        service = self.service(FakeRunner())

        with mock.patch.object(
            service,
            "_gitlab_document",
            return_value={
                "title": "Already completed",
                "state": "opened",
                "labels": ["pitcrew-agent", "pitcrew-state::done"],
            },
        ):
            decision = service.decisions()

        self.assertIsNone(decision["pending"])
        state = json.loads((self.runtime / "state" / "unblock-state.json").read_text())
        self.assertIsNone(state["pending_question"])

    def test_proposals_are_listed_and_rejection_is_persisted(self):
        proposal_path = self.runtime / "proposals.json"
        proposal_path.write_text(json.dumps([{
            "id": "feature-1", "category": "feature", "severity": "medium",
            "title": "Payment summary", "summary": "Users need the total first.",
            "evidence": ["templates/payment/show.html.twig:42"],
            "recommendation": "Show the total above the detail.",
            "status": "suggested", "source": "product-discovery-run",
        }]), encoding="utf-8")
        service = self.service(FakeRunner())
        self.assertEqual(["feature-1"], [item["id"] for item in service.proposals_snapshot()["proposals"]])
        result = service.decide_proposal("feature-1", "reject", "Not in current scope")
        self.assertEqual("dismissed", result["proposal"]["status"])
        self.assertEqual([], service.proposals_snapshot()["proposals"])

    def test_architecture_proposal_approval_is_local_and_queues_manager(self):
        proposal_path = self.runtime / "proposals.json"
        proposal_path.write_text(json.dumps([{
            "id": "architecture-1", "category": "architecture", "severity": "medium",
            "architecture_category": "couplage framework/persistence", "title": "Tight coupling",
            "summary": "Module A imports Module B internals.", "where": ["src/A.py:1"],
            "evidence": ["src/A.py:1"], "recommendation": "Use an interface.",
            "status": "suggested", "source": "architecture-run",
        }]), encoding="utf-8")
        runner = FakeRunner()
        result = self.service(runner).decide_proposal("architecture-1", "approve")
        self.assertTrue(result["manager_queued"])
        self.assertNotIn("tracker", result["proposal"])
        self.assertFalse(any(call[0][0] == "glab" and "-X" in call[0] for call in runner.calls))

    def test_feature_proposal_approval_still_creates_tracker_issue(self):
        proposal_path = self.runtime / "proposals.json"
        proposal_path.write_text(json.dumps([{
            "id": "feature-remote", "category": "feature", "severity": "low",
            "title": "Feature", "summary": "Summary", "evidence": ["a:1"],
            "recommendation": "Recommendation", "status": "suggested", "source": "test",
        }]), encoding="utf-8")
        runner = FakeRunner()
        runner.mutation_responses["projects/getbill1%2Fgetbill/issues"] = {"iid": 9, "web_url": "https://gitlab.com/a/9"}
        result = self.service(runner).decide_proposal("feature-remote", "approve")
        self.assertFalse(result["manager_queued"])
        self.assertEqual(9, result["proposal"]["tracker"]["iid"])

    def test_submit_decision_persists_answer_and_triggers_unblock(self):
        self.write_pending_decision()
        service = self.service(FakeRunner())
        process = mock.Mock(pid=2468)

        with mock.patch(
            "scripts.pitcrew_dashboard.subprocess.Popen",
            return_value=process,
        ) as starter:
            result = service.submit_decision(
                "getbill1/getbill#1",
                "Ship it",
                "Approved from dashboard",
            )

        self.assertEqual({"accepted": True, "pid": 2468}, result)
        stored_state = json.loads(
            (self.runtime / "state" / "unblock-state.json").read_text()
        )
        pending = stored_state["pending_question"]
        self.assertEqual("answered", pending["status"])
        self.assertEqual("Ship it", pending["answer"])
        self.assertEqual("Approved from dashboard", pending["notes"])
        self.assertEqual("unblock", starter.call_args.args[0][1])

    def test_snapshot_exposes_live_status_for_running_agents(self):
        live_dir = self.runtime / "live"
        live_dir.mkdir()
        (live_dir / "manager-run.json").write_text(
            json.dumps(
                {
                    "project": "getbill",
                    "skill": "manager-run",
                    "model": "gpt-5.6-sol",
                    "started_at": "2026-07-24T11:58:00+00:00",
                    "pid": os.getpid(),
                    "phase": "Exécution du passage courant",
                }
            ),
            encoding="utf-8",
        )

        schedule = [
            {
                **entry,
                "running": False,
            }
            if entry["skill"] == "manager-run"
            else entry
            for entry in schedule_status()
        ]
        snapshot = self.service(FakeRunner(schedule=schedule)).snapshot()
        roles = {role["skill"]: role for role in snapshot["agents"]}

        self.assertEqual(
            {
                "project": "getbill",
                "skill": "manager-run",
                "model": "gpt-5.6-sol",
                "started_at": "2026-07-24T11:58:00+00:00",
                "pid": os.getpid(),
                "phase": "Exécution du passage courant",
            },
            roles["manager-run"]["live_status"],
        )
        self.assertTrue(roles["manager-run"]["running"])

    def test_snapshot_keeps_latest_history_but_uses_latest_measured_usage(self):
        records = (
            {
                "project": "getbill", "skill": "research-run",
                "started_at": "2026-07-24T11:00:00+00:00",
                "finished_at": "2026-07-24T11:01:00+00:00",
                "outcome": "success", "summary": "measured", "exit_code": 0,
                "model": "gpt-5.6-luna",
                "usage": {"input_tokens": 12, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 3, "total_tokens": 15},
            },
            {
                "project": "getbill", "skill": "research-run",
                "started_at": "2026-07-24T11:30:00+00:00",
                "finished_at": "2026-07-24T11:31:00+00:00",
                "outcome": "noop", "summary": "legacy", "exit_code": 0,
            },
            {
                "project": "getbill", "skill": "manager-run",
                "started_at": "2026-07-24T11:30:00+00:00",
                "finished_at": "2026-07-24T11:31:00+00:00",
                "outcome": "success", "summary": "legacy", "exit_code": 0,
            },
        )
        (self.runtime / "history.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )

        snapshot = self.service(FakeRunner()).snapshot()
        roles = {role["skill"]: role for role in (*snapshot["agents"], *snapshot["disabled_roles"])}

        self.assertEqual("legacy", roles["research-run"]["latest_history"]["summary"])
        self.assertEqual("gpt-5.6-luna", roles["research-run"]["latest_model"])
        self.assertEqual("15", roles["research-run"]["latest_usage"]["tokens"]["total_tokens"])
        self.assertIsNone(roles["manager-run"]["latest_model"])
        self.assertEqual(0, roles["manager-run"]["latest_usage"]["measured_runs"])
        self.assertEqual(0, roles["manager-run"]["latest_usage"]["unmeasured_runs"])

    def test_snapshot_serializes_usage_tokens_as_exact_decimal_strings(self):
        huge = 10**100
        record = {
            "project": "getbill",
            "skill": "research-run",
            "started_at": "2026-07-24T11:29:00+00:00",
            "finished_at": "2026-07-24T11:30:00+00:00",
            "outcome": "success",
            "summary": "large usage",
            "exit_code": 0,
            "model": "gpt-5.6-terra",
            "usage": {
                "input_tokens": huge,
                "cached_input_tokens": huge,
                "cache_write_tokens": huge,
                "output_tokens": huge,
                "total_tokens": huge * 4,
            },
        }
        (self.runtime / "history.jsonl").write_text(
            json.dumps(record) + "\n",
            encoding="utf-8",
        )

        snapshot = self.service(FakeRunner()).snapshot()

        expected = str(huge)
        for usage in (
            snapshot["usage_7d"],
            snapshot["agents"][0]["latest_usage"],
            snapshot["agents"][0]["usage_7d"],
            snapshot["disabled_roles"][0]["latest_usage"],
            snapshot["disabled_roles"][0]["usage_7d"],
        ):
            self.assertEqual(
                expected if usage["measured_runs"] else "0",
                usage["tokens"]["input_tokens"],
            )
            self.assertTrue(all(isinstance(value, str) for value in usage["tokens"].values()))

    def test_snapshot_uses_default_models_when_runtime_agents_are_absent(self):
        config = runtime_config()
        config.pop("agents")
        (self.runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")

        snapshot = self.service(FakeRunner()).snapshot()

        roles = {role["skill"]: role for role in snapshot["agents"]}
        self.assertEqual("gpt-5.6-terra", roles["research-run"]["configured_model"])

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
        self.assertEqual("issue", todo["resource_type"])
        self.assertEqual(todo["web_url"], todo["canonical_url"])
        self.assertEqual("todo", todo["lifecycle"])
        self.assertIsNone(todo["route"])
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
        self.assertEqual(1, len(work["merge_requests"]))
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

    def test_gitlab_work_keeps_merged_ticket_read_only(self):
        issue = dict(gitlab_issues()[3])
        issue.update(
            {
                "iid": 3,
                "title": "Already merged work",
                "description": "",
                "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
                "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
            }
        )
        merged_mr = dict(gitlab_merge_requests()[1])
        merged_mr.update(
            {
                "iid": 12,
                "description": "Closes #3",
                "state": "merged",
            }
        )
        runner = FakeRunner(
            issue_pages=[[issue]],
            merge_request_pages=[[merged_mr]],
        )
        runner.mutation_responses = {
            "/issues/3": issue,
            "projects/getbill1%2Fgetbill/issues/3/notes?per_page=100": [],
        }

        work = self.service(runner).gitlab_work(force_refresh=True)

        self.assertEqual("Already merged work", work["groups"]["blocked"][0]["title"])
        self.assertEqual([], work["groups"]["done"])
        mutation_args = [call[0] for call in runner.calls if "-X" in call[0]]
        self.assertEqual([], mutation_args)

    def test_gitlab_work_ticket_agent_actions(self):
        work = self.service(FakeRunner()).gitlab_work(force_refresh=True)

        self.assertEqual(
            {
                "skill": "implementer-run",
                "label": "Lancer l’implémentation",
                "target": "https://gitlab.com/getbill1/getbill/-/issues/1",
                "available": True,
                "unavailable_reason": None,
            },
            work["groups"]["todo"][0]["agent_action"],
        )
        self.assertEqual("unblock", work["groups"]["blocked"][0]["agent_action"]["skill"])
        self.assertFalse(work["groups"]["blocked"][0]["agent_action"]["available"])
        self.assertEqual("stale-sweep", work["groups"]["done"][0]["agent_action"]["skill"])
        self.assertIsNone(work["groups"]["processing"][0]["agent_action"])
        self.assertIsNone(work["groups"]["review"][0]["agent_action"])

    def test_ticket_launch_is_idempotent_under_concurrency(self):
        runner = FakeRunner()
        store, dispatcher = self.coordinator()
        service = self.service(runner, run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        issue = dict(gitlab_issues()[0])
        with mock.patch.object(service, "_gitlab_document", return_value=issue):
            barrier = threading.Barrier(2)
            def launch():
                barrier.wait()
                return service.launch_ticket_agent("implementer-run", target)
            first, second = list(ThreadPoolExecutor(max_workers=2).map(lambda _: launch(), range(2)))

        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(first["accepted"])
        self.assertTrue(second["accepted"])
        self.assertEqual(1, len(dispatcher.drain_calls))
        self.assertEqual(3, dispatcher.drain_calls[0][1]["implementer-run"])
        self.assertEqual([first["run_id"]], dispatcher.spawned_run_ids)
        self.assertEqual(1, len(store.list_runs("getbill", active_only=True)))

    def test_ticket_launch_keeps_distinct_targets_active(self):
        issues = gitlab_issues()
        issues[1].update({
            "state": "opened",
            "labels": ["pitcrew-agent", "pitcrew-state::todo"],
        })
        runner = FakeRunner(issue_pages=[issues])
        store, dispatcher = self.coordinator()
        service = self.service(runner, run_store=store, run_dispatcher=dispatcher)
        targets = [
            "https://gitlab.com/getbill1/getbill/-/issues/1",
            "https://gitlab.com/getbill1/getbill/-/issues/2",
        ]

        results = [
            service.launch_ticket_agent("implementer-run", target)
            for target in targets
        ]

        self.assertEqual(2, len({result["run_id"] for result in results}))
        self.assertTrue(all(result["accepted"] for result in results))
        active = store.list_runs("getbill", active_only=True)
        self.assertEqual(set(targets), {run["target"] for run in active})
        self.assertEqual(2, len(dispatcher.spawned_run_ids))

    def test_ticket_launch_retries_drain_when_existing_run_is_queued(self):
        store, dispatcher = self.coordinator(
            first_drain_failure="before_claim",
        )
        service = self.service(
            FakeRunner(),
            run_store=store,
            run_dispatcher=dispatcher,
        )
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        issue = dict(gitlab_issues()[0])

        with mock.patch.object(service, "_gitlab_document", return_value=issue):
            with self.assertRaisesRegex(DashboardError, "ticket run is unavailable"):
                service.launch_ticket_agent("implementer-run", target)
            queued = store.list_runs("getbill", active_only=True)[0]
            retried = service.launch_ticket_agent("implementer-run", target)

        self.assertEqual("queued", queued["state"])
        self.assertEqual(queued["run_id"], retried["run_id"])
        self.assertTrue(retried["accepted"])
        self.assertEqual("running", retried["state"])
        self.assertEqual(2, len(dispatcher.drain_calls))
        self.assertEqual([queued["run_id"]], dispatcher.spawned_run_ids)

    def test_ticket_launch_does_not_redrain_existing_running_run(self):
        store, dispatcher = self.coordinator(
            first_drain_failure="after_claim",
        )
        service = self.service(
            FakeRunner(),
            run_store=store,
            run_dispatcher=dispatcher,
        )
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        issue = dict(gitlab_issues()[0])

        with mock.patch.object(service, "_gitlab_document", return_value=issue):
            with self.assertRaisesRegex(DashboardError, "ticket run is unavailable"):
                service.launch_ticket_agent("implementer-run", target)
            running = store.list_runs("getbill", active_only=True)[0]
            retried = service.launch_ticket_agent("implementer-run", target)

        self.assertEqual("running", running["state"])
        self.assertEqual(running["run_id"], retried["run_id"])
        self.assertTrue(retried["accepted"])
        self.assertEqual("running", retried["state"])
        self.assertEqual(1, len(dispatcher.drain_calls))
        self.assertEqual([running["run_id"]], dispatcher.spawned_run_ids)

    def test_ticket_launch_revalidates_lifecycle(self):
        store, dispatcher = self.coordinator()
        runner = FakeRunner()
        service = self.service(runner, run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        result = service.launch_ticket_agent("implementer-run", target)
        self.assertTrue(result["accepted"])
        store.finish(result["run_id"], state="cancelled")
        with self.assertRaisesRegex(DashboardError, "ticket is not eligible"):
            service.launch_ticket_agent("unblock", target)

        for invalid in (
            "https://evil.example/getbill1/getbill/-/issues/1",
            "https://gitlab.com/other/project/-/issues/1",
            "https://gitlab.com/getbill1/getbill/-/issues/0",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DashboardError):
                    service.launch_ticket_agent("implementer-run", invalid)

    def test_ticket_launch_normalizes_gitlab_work_item_urls(self):
        issue = dict(gitlab_issues()[3])
        issue.update(
            {
                "iid": 14,
                "web_url": "https://gitlab.com/getbill1/getbill/-/work_items/14",
                "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            }
        )
        runner = FakeRunner(issue_pages=[[issue]])
        for entry in runner.schedule:
            if entry["skill"] == "unblock":
                entry["enabled"] = True
        store, dispatcher = self.coordinator()
        service = self.service(runner, run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/work_items/14"
        result = service.launch_ticket_agent("unblock", target)

        self.assertIn(result["state"], {"queued", "running"})
        self.assertEqual(
            "https://gitlab.com/getbill1/getbill/-/issues/14",
            store.get(result["run_id"])["target"],
        )

    def test_gitlab_work_is_read_only_and_overlays_active_runs(self):
        store, dispatcher = self.coordinator()
        service = self.service(FakeRunner(), run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        store.enqueue(project="getbill", skill="implementer-run", source="dashboard", target=target)
        with mock.patch.object(service, "_gitlab_mutation") as mutation:
            work = service.gitlab_work(force_refresh=True)
        ticket = work["groups"]["todo"][0]
        self.assertEqual(target, ticket["active_run"]["target"])
        self.assertFalse(ticket["agent_action"]["available"])
        self.assertEqual("Ticket en attente ou en cours", ticket["agent_action"]["unavailable_reason"])
        mutation.assert_not_called()

    def test_gitlab_work_overlays_active_run_for_work_item_url(self):
        issue = dict(gitlab_issues()[3])
        issue.update(
            {
                "iid": 14,
                "state": "opened",
                "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
                "web_url": "https://gitlab.com/getbill1/getbill/-/work_items/14",
            }
        )
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(issue_pages=[[issue]]),
            run_store=store,
            run_dispatcher=dispatcher,
        )
        target = "https://gitlab.com/getbill1/getbill/-/issues/14"
        store.enqueue(project="getbill", skill="unblock", source="dashboard", target=target)

        work = service.gitlab_work(force_refresh=True)

        ticket = work["groups"]["blocked"][0]
        self.assertEqual(target, ticket["active_run"]["target"])
        self.assertFalse(ticket["agent_action"]["available"])
        self.assertEqual("Ticket en attente ou en cours", ticket["agent_action"]["unavailable_reason"])

    def test_runs_snapshot_counts_legacy_live_worker_without_lowering_maximum(self):
        store, dispatcher = self.coordinator()
        live_dir = self.runtime / "live"
        live_dir.mkdir()
        (live_dir / "implementer-run.json").write_text(json.dumps({
            "project": "getbill", "skill": "implementer-run", "pid": os.getpid(),
            "started_at": FIXED_NOW.isoformat(), "phase": "legacy worker",
        }), encoding="utf-8")
        service = self.service(FakeRunner(), run_store=store, run_dispatcher=dispatcher)

        snapshot = service.runs_snapshot()

        capacity = snapshot["capacity"]["implementer-run"]
        self.assertEqual(3, capacity["max_concurrent"])
        self.assertEqual(1, capacity["running"])
        self.assertEqual(1, capacity["legacy_running"])
        self.assertTrue(snapshot["has_active"])
        self.assertEqual(2, service._claim_capacity_map()["implementer-run"])

    def test_gitlab_work_represents_merged_drift_without_syncing_in_read(self):
        issue = dict(gitlab_issues()[3])
        issue.update(
            {
                "iid": 3,
                "state": "opened",
                "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
                "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
            }
        )
        merged_mr = dict(gitlab_merge_requests()[1])
        merged_mr.update(
            {
                "iid": 12,
                "description": "Closes #3",
                "state": "merged",
                "merged_at": "2026-07-24T11:00:00Z",
            }
        )
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(
                issue_pages=[[issue]],
                merge_request_pages=[[merged_mr]],
            ),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        with (
            mock.patch.object(service, "_gitlab_mutation") as mutation,
            mock.patch.object(store, "enqueue", wraps=store.enqueue) as enqueue,
            mock.patch.object(
                service,
                "reconcile_merged_ticket_candidates",
            ) as reconcile,
        ):
            ticket = service.gitlab_work(force_refresh=True)["groups"]["blocked"][0]

        self.assertEqual(
            {
                "skill": "stale-sweep",
                "target": issue["web_url"],
                "reason": "merged_merge_request",
                "evidence": {
                    "provider": "gitlab",
                    "project_path": "getbill1/getbill",
                    "issue_iid": 3,
                    "merge_request_iid": 12,
                    "merge_request_url": merged_mr["web_url"],
                    "merged_at": "2026-07-24T11:00:00Z",
                },
            },
            ticket["reconciliation_candidate"],
        )
        mutation.assert_not_called()
        enqueue.assert_not_called()
        reconcile.assert_not_called()
        self.assertEqual([], dispatcher.drain_calls)
        self.assertFalse(hasattr(service, "_sync_merged_issue"))

    def test_reconcile_merged_ticket_candidates_enqueues_and_drains_stale_sweep(self):
        issue = {
            **gitlab_issues()[3],
            "iid": 3,
            "state": "opened",
            "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
        }
        merged_mr = {
            **gitlab_merge_requests()[1],
            "iid": 12,
            "description": "Closes #3",
            "state": "merged",
            "merged_at": "2026-07-24T11:00:00Z",
        }
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(
                issue_pages=[[issue]],
                merge_request_pages=[[merged_mr]],
            ),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        with mock.patch.object(service, "_gitlab_mutation") as mutation:
            result = service.reconcile_merged_ticket_candidates()

        self.assertTrue(result["drained"])
        self.assertEqual([], result["deferred"])
        self.assertEqual([], result["rejected"])
        self.assertEqual(1, len(result["runs"]))
        admitted = result["runs"][0]
        self.assertTrue(admitted["created"])
        self.assertEqual("stale-sweep", admitted["skill"])
        self.assertEqual("running", admitted["state"])
        stored = store.get(admitted["run_id"])
        self.assertEqual("reconcile", stored["source"])
        self.assertEqual(issue["web_url"], stored["target"])
        self.assertEqual(1, len(dispatcher.drain_calls))
        mutation.assert_not_called()

    def test_reconcile_merged_ticket_candidates_is_idempotent(self):
        issue = {
            **gitlab_issues()[3],
            "iid": 3,
            "state": "opened",
            "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
        }
        merged_mr = {
            **gitlab_merge_requests()[1],
            "iid": 12,
            "description": "Closes #3",
            "state": "merged",
            "merged_at": "2026-07-24T11:00:00Z",
        }
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(
                issue_pages=[[issue]],
                merge_request_pages=[[merged_mr]],
            ),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        first = service.reconcile_merged_ticket_candidates()
        second = service.reconcile_merged_ticket_candidates()

        self.assertEqual(first["runs"][0]["run_id"], second["runs"][0]["run_id"])
        self.assertTrue(first["runs"][0]["created"])
        self.assertFalse(second["runs"][0]["created"])
        self.assertTrue(first["drained"])
        self.assertFalse(second["drained"])
        self.assertEqual(1, len(dispatcher.drain_calls))
        self.assertEqual(1, len(dispatcher.spawned_run_ids))
        self.assertEqual(1, len(store.list_runs("getbill", active_only=True)))

    def test_reconcile_merged_ticket_candidates_defers_other_active_skill(self):
        issue = {
            **gitlab_issues()[3],
            "iid": 3,
            "state": "opened",
            "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
        }
        merged_mr = {
            **gitlab_merge_requests()[1],
            "iid": 12,
            "description": "Closes #3",
            "state": "merged",
            "merged_at": "2026-07-24T11:00:00Z",
        }
        store, dispatcher = self.coordinator()
        active = store.enqueue(
            project="getbill",
            skill="unblock",
            source="dashboard",
            target=issue["web_url"],
        )
        service = self.service(
            FakeRunner(
                issue_pages=[[issue]],
                merge_request_pages=[[merged_mr]],
            ),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        result = service.reconcile_merged_ticket_candidates()

        self.assertFalse(result["drained"])
        self.assertEqual([], result["runs"])
        self.assertEqual([], result["rejected"])
        self.assertEqual(
            [
                {
                    "target": issue["web_url"],
                    "run_id": active["run_id"],
                    "skill": "unblock",
                    "state": "queued",
                    "reason": "active_ticket_run",
                }
            ],
            result["deferred"],
        )
        self.assertEqual([], dispatcher.drain_calls)
        self.assertEqual("unblock", store.get(active["run_id"])["skill"])

    def test_reconcile_merged_ticket_candidates_rejects_stale_and_malformed(self):
        stale_issue = {
            **gitlab_issues()[3],
            "iid": 3,
            "state": "closed",
            "labels": ["pitcrew-agent", "pitcrew-state::blocked"],
            "web_url": "https://gitlab.com/getbill1/getbill/-/issues/3",
        }
        merged_mr = {
            **gitlab_merge_requests()[1],
            "iid": 12,
            "description": "Closes #3",
            "state": "merged",
            "merged_at": "2026-07-24T11:00:00Z",
        }
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(
                issue_pages=[[stale_issue]],
                merge_request_pages=[[merged_mr]],
            ),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        stale = service.reconcile_merged_ticket_candidates()
        malformed_payload = {
            "degraded": False,
            "groups": {
                lifecycle: (
                    [
                        {
                            "reconciliation_candidate": {
                                "skill": "stale-sweep",
                                "target": stale_issue["web_url"],
                                "reason": "merged_merge_request",
                                "evidence": {
                                    "provider": "github",
                                },
                            }
                        }
                    ]
                    if lifecycle == "blocked"
                    else []
                )
                for lifecycle in ("todo", "processing", "review", "blocked", "done")
            },
        }
        with mock.patch.object(
            service,
            "gitlab_work",
            return_value=malformed_payload,
        ):
            malformed = service.reconcile_merged_ticket_candidates()

        self.assertEqual([], stale["runs"])
        self.assertEqual([], stale["deferred"])
        self.assertEqual([], malformed["runs"])
        self.assertEqual(1, len(malformed["rejected"]))
        self.assertEqual([], store.list_runs("getbill", active_only=True))
        self.assertEqual([], dispatcher.drain_calls)

    def test_reconcile_merged_ticket_candidates_fails_closed_on_provider_error(self):
        store, dispatcher = self.coordinator()
        service = self.service(
            FakeRunner(fail_glab=True),
            run_store=store,
            run_dispatcher=dispatcher,
        )

        with self.assertRaisesRegex(DashboardError, "reconciliation unavailable"):
            service.reconcile_merged_ticket_candidates()

        self.assertEqual([], store.list_runs("getbill", active_only=True))
        self.assertEqual([], dispatcher.drain_calls)

    def test_gitlab_work_degrades_when_run_store_is_unavailable(self):
        store, dispatcher = self.coordinator()
        service = self.service(FakeRunner(), run_store=store, run_dispatcher=dispatcher)
        with mock.patch.object(service, "runs_snapshot", side_effect=DashboardError("ticket runs are unavailable")):
            work = service.gitlab_work(force_refresh=True)

        self.assertFalse(work["degraded"])
        self.assertTrue(work["runs_degraded"])
        self.assertEqual("Todo work", work["groups"]["todo"][0]["title"])
        self.assertFalse(work["groups"]["todo"][0]["agent_action"]["available"])
        self.assertEqual("État des tickets indisponible", work["groups"]["todo"][0]["agent_action"]["unavailable_reason"])

    def test_dashboard_construction_survives_default_run_store_failure(self):
        with mock.patch("scripts.pitcrew_dashboard.RunStore", side_effect=RunStoreError("unavailable")):
            service = self.service(FakeRunner())

        work = service.gitlab_work(force_refresh=True)

        self.assertFalse(work["degraded"])
        self.assertTrue(work["runs_degraded"])
        self.assertEqual("Todo work", work["groups"]["todo"][0]["title"])
        self.assertFalse(work["groups"]["todo"][0]["agent_action"]["available"])

    def test_cached_gitlab_work_recomputes_active_run_overlay(self):
        store, dispatcher = self.coordinator()
        service = self.service(FakeRunner(), run_store=store, run_dispatcher=dispatcher)
        target = "https://gitlab.com/getbill1/getbill/-/issues/1"
        first = service.gitlab_work()
        run = store.enqueue(project="getbill", skill="implementer-run", source="dashboard", target=target)
        active = service.gitlab_work()
        store.finish(run["run_id"], state="cancelled")
        finished = service.gitlab_work()

        self.assertTrue(first["groups"]["todo"][0]["agent_action"]["available"])
        self.assertFalse(active["groups"]["todo"][0]["agent_action"]["available"])
        self.assertEqual(run["run_id"], active["groups"]["todo"][0]["active_run"]["run_id"])
        self.assertTrue(finished["groups"]["todo"][0]["agent_action"]["available"])
        self.assertIsNone(finished["groups"]["todo"][0]["active_run"])

    def test_gitlab_work_lists_only_open_merge_requests_with_display_fields(self):
        runner = FakeRunner()
        work = self.service(runner).gitlab_work(force_refresh=True)

        self.assertEqual([11], [mr["iid"] for mr in work["merge_requests"]])
        merge_request = work["merge_requests"][0]
        self.assertEqual("merge_request", merge_request["resource_type"])
        self.assertEqual(merge_request["web_url"], merge_request["canonical_url"])
        self.assertEqual("fix/payment-summary", merge_request["source_branch"])
        self.assertEqual("develop", merge_request["target_branch"])
        self.assertEqual("agent-sol", merge_request["author_username"])
        self.assertEqual("failed", merge_request["pipeline_status"])
        self.assertIn(
            "merge_requests?scope=all&per_page=100&page=1",
            runner.calls[1][0][2],
        )

    def test_gitlab_work_exposes_merge_conflict_status(self):
        conflicted = dict(gitlab_merge_requests()[0])
        conflicted.update(
            {
                "iid": 20,
                "detailed_merge_status": "conflict",
                "has_conflicts": True,
            }
        )
        runner = FakeRunner(merge_request_pages=[[conflicted]])

        merge_request = self.service(runner).gitlab_work(force_refresh=True)[
            "merge_requests"
        ][0]

        self.assertEqual("conflict", merge_request["detailed_merge_status"])
        self.assertTrue(merge_request["has_conflicts"])

    def test_merge_merge_request_merges_then_deletes_source_branch(self):
        service = self.service(FakeRunner())
        with mock.patch.object(
            service,
            "_gitlab_document",
            return_value={
                "iid": 11,
                "state": "opened",
                "source_branch": "fix/payment-summary",
                "target_branch": "develop",
                "sha": "head-sha-11",
            },
        ), mock.patch.object(service, "_gitlab_mutation", return_value="") as mutation:
            result = service.merge_merge_request(11)

        self.assertEqual(
            {
                "accepted": True,
                "partial": False,
                "iid": 11,
                "source_branch": "fix/payment-summary",
                "target_branch": "develop",
            },
            result,
        )
        self.assertEqual(
            [
                mock.call(
                    "projects/getbill1%2Fgetbill/merge_requests/11/merge",
                    "PUT",
                    {"sha": "head-sha-11"},
                ),
                mock.call(
                    "projects/getbill1%2Fgetbill/repository/branches/fix%2Fpayment-summary",
                    "DELETE",
                ),
            ],
            mutation.call_args_list,
        )

    def test_merge_merge_request_does_not_delete_after_failed_merge(self):
        service = self.service(FakeRunner())
        with mock.patch.object(
            service,
            "_gitlab_document",
            return_value={
                "state": "opened",
                "source_branch": "fix/payment-summary",
                "target_branch": "develop",
                "sha": "head-sha-11",
            },
        ), mock.patch.object(
            service,
            "_gitlab_mutation",
            side_effect=DashboardError("merge failed"),
        ) as mutation:
            with self.assertRaises(DashboardError):
                service.merge_merge_request(11)

        mutation.assert_called_once_with(
            "projects/getbill1%2Fgetbill/merge_requests/11/merge", "PUT", {"sha": "head-sha-11"}
        )

    def test_merge_merge_request_rejects_deployment_targets(self):
        service = self.service(FakeRunner())
        with mock.patch.object(
            service,
            "_gitlab_document",
            return_value={
                "state": "opened",
                "source_branch": "fix/payment-summary",
                "target_branch": "prod",
            },
        ), mock.patch.object(service, "_gitlab_mutation") as mutation:
            with self.assertRaises(DashboardError):
                service.merge_merge_request(11)

        mutation.assert_not_called()

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
            [call[0] for call in runner.calls if call[0][0] == "glab"],
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
        self.assertEqual(9, len(runner.calls))
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
        self.assertFalse(hasattr(service, "_trigger_target"))
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

    def test_restart_failure_keeps_restart_public_error(self):
        def runner(args, **kwargs):
            if "status" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=json.dumps(schedule_status()),
                    stderr="",
                )
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

        with self.assertRaisesRegex(DashboardError, "^failed to restart agent$"):
            self.service(runner).control("restart", "research-run")

    def test_change_model_stops_persists_installs_and_triggers_in_order(self):
        runner = FakeRunner()
        service = self.service(runner)
        process = mock.Mock(pid=9753)
        calls = []

        def persist(project, skill, model):
            calls.append(("persist", project, skill, model))

        original_run = service._run

        def tracked_run(args):
            if args[2] in {"stop", "install"}:
                calls.append((args[2],))
            return original_run(args)

        service._run = tracked_run
        with (
            mock.patch(
                "scripts.pitcrew_dashboard.update_runtime_model",
                side_effect=persist,
            ),
            mock.patch(
                "scripts.pitcrew_dashboard.subprocess.Popen",
                return_value=process,
            ) as starter,
        ):
            result = service.change_model("research-run", "gpt-5.6-sol")

        self.assertEqual(
            [
                ("stop",),
                ("persist", "getbill", "research-run", "gpt-5.6-sol"),
                ("install",),
            ],
            calls,
        )
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
        self.assertEqual(
            {"accepted": True, "pid": 9753, "model": "gpt-5.6-sol"},
            result,
        )

    def test_change_model_rejects_unknown_disabled_and_unsupported_before_mutation(self):
        runner = FakeRunner()
        service = self.service(runner)
        with mock.patch("scripts.pitcrew_dashboard.update_runtime_model") as persist:
            for skill, model in (
                ("unknown-run", "gpt-5.6-sol"),
                ("qa-run", "gpt-5.6-sol"),
                ("research-run", "not-a-model"),
            ):
                with self.subTest(skill=skill, model=model):
                    with self.assertRaises(DashboardError):
                        service.change_model(skill, model)
        persist.assert_not_called()
        self.assertFalse(any("stop" in args for args, _ in runner.calls))
        self.assertFalse(any("install" in args for args, _ in runner.calls))

    def test_change_model_stops_at_each_failure_boundary_without_rollback(self):
        service = self.service(FakeRunner())
        original_config = dict(service.config)

        with mock.patch.object(
            service,
            "_enabled_entry",
            return_value={"skill": "research-run", "enabled": True},
        ), mock.patch.object(service, "_scheduler_control") as scheduler, mock.patch(
            "scripts.pitcrew_dashboard.update_runtime_model"
        ) as persist, mock.patch.object(service, "_trigger") as trigger:
            scheduler.side_effect = DashboardError("Authorization: Basic secret")
            with self.assertRaises(DashboardError):
                service.change_model("research-run", "gpt-5.6-sol")
            persist.assert_not_called()
            trigger.assert_not_called()

            scheduler.reset_mock(side_effect=True)
            persist.side_effect = OSError("Authorization: Basic secret")
            with self.assertRaises(DashboardError) as raised:
                service.change_model("research-run", "gpt-5.6-sol")
            self.assertIn("Authorization: [REDACTED]", str(raised.exception))
            self.assertEqual(original_config, service.config)
            self.assertEqual([mock.call("stop", "research-run")], scheduler.call_args_list)
            trigger.assert_not_called()

            scheduler.reset_mock()
            persist.reset_mock(side_effect=True)
            updated = dict(original_config)
            updated["agents"] = {"research-run": {"model": "gpt-5.6-sol"}}
            with mock.patch.object(service, "_load_config", return_value=updated):
                scheduler.side_effect = (None, DashboardError("install failed"))
                with self.assertRaises(DashboardError):
                    service.change_model("research-run", "gpt-5.6-sol")
            self.assertEqual(updated, service.config)
            trigger.assert_not_called()

            scheduler.reset_mock(side_effect=True)
            trigger.side_effect = DashboardError("trigger failed")
            with mock.patch.object(service, "_load_config", return_value=updated):
                with self.assertRaises(DashboardError):
                    service.change_model("research-run", "gpt-5.6-sol")
            self.assertEqual(updated, service.config)
            self.assertEqual(
                [mock.call("stop", "research-run"), mock.call("install", "research-run")],
                scheduler.call_args_list,
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

    def test_global_control_uses_scheduler_actions_and_reports_state(self):
        runner = FakeRunner()
        service = self.service(runner)
        self.assertEqual(
            {"accepted": True, "global_state": "stopped"},
            service.global_control("stop-all"),
        )
        self.assertEqual(
            {"accepted": True, "global_state": "running"},
            service.global_control("resume-all"),
        )
        self.assertEqual(
            ["stop-all", "resume-all"],
            [call[0][2] for call in runner.calls if "pitcrew-schedule.py" in call[0][1]],
        )

    def test_preprod_review_snapshot_reads_latest_report_without_creating_store(self):
        service = self.service(FakeRunner())
        report = {
            "schema_version": 1, "completed_at": "2026-07-24T11:00:00Z",
            "base_ref": "origin/preprod", "compare_ref": "origin/develop",
            "base_sha": "a" * 40, "compare_sha": "b" * 40, "merge_base_sha": "c" * 40,
            "commit_count": 0, "changed_file_count": 0, "files": [], "reviewed_files": [],
            "findings": [], "synthesis": "No changes between configured refs.", "verdict": "ready",
            "model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "failure_reason": None,
        }
        from scripts.pitcrew_preprod_review import ReportStore
        ReportStore(self.runtime / "preprod-review-reports.json", 10).save(report)

        snapshot = service.preprod_review_snapshot()

        self.assertEqual("preprod-review-run", snapshot["skill"])
        self.assertEqual(report, snapshot["latest"])
        self.assertFalse(snapshot["report_stale"])

    def test_preprod_review_skill_and_dashboard_use_same_report_store(self):
        service = self.service(FakeRunner())
        skill = (ROOT / "skills/preprod-review-run/SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(self.runtime / "preprod-review-reports.json", service.preprod_reports.path)
        self.assertIn("STORE=$CONFIG_DIR/preprod-review-reports.json", skill)

    def test_trigger_preprod_review_uses_manual_locked_runner_argv(self):
        service = self.service(FakeRunner())
        with mock.patch("scripts.pitcrew_dashboard.subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            result = service.trigger_preprod_review()

        self.assertEqual({"accepted": True, "pid": 4242, "skill": "preprod-review-run"}, result)
        self.assertEqual(
            [str(ROOT / "bin/pitcrew-codex.sh"), "preprod-review-run", "getbill"],
            popen.call_args.args[0],
        )

    def test_stop_preprod_review_rejects_missing_or_invalid_live_marker(self):
        service = self.service(FakeRunner())
        with self.assertRaisesRegex(DashboardError, "not running"):
            service.stop_preprod_review()

    def test_stop_preprod_review_terminates_tracked_process_before_live_marker(self):
        service = self.service(FakeRunner())
        process = mock.Mock(pid=4242)
        process.poll.return_value = None
        service._preprod_process = process

        result = service.stop_preprod_review()

        self.assertEqual({"accepted": True, "skill": "preprod-review-run", "pid": 4242}, result)
        process.terminate.assert_called_once_with()
        self.assertIsNone(service._preprod_process)

    def test_stop_preprod_review_refuses_stale_marker_for_unrelated_process(self):
        service = self.service(FakeRunner())
        live_dir = self.runtime / "live"
        live_dir.mkdir()
        (live_dir / "preprod-review-run.json").write_text(json.dumps({
            "project": "getbill", "skill": "preprod-review-run", "started_at": "2026-07-24T11:00:00Z",
            "pid": os.getpid(), "phase": "running",
        }), encoding="utf-8")
        service._run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="python unrelated", stderr=""))
        with mock.patch("scripts.pitcrew_dashboard.os.kill") as kill:
            with self.assertRaisesRegex(DashboardError, "not running"):
                service.stop_preprod_review()
        self.assertNotIn(mock.call(os.getpid(), signal.SIGTERM), kill.call_args_list)

    def test_stop_preprod_review_signals_verified_live_helper(self):
        service = self.service(FakeRunner())
        live_dir = self.runtime / "live"
        live_dir.mkdir()
        live_path = live_dir / "preprod-review-run.json"
        live_path.write_text(json.dumps({
            "project": "getbill", "skill": "preprod-review-run", "started_at": "2026-07-24T11:00:00Z",
            "pid": os.getpid(), "phase": "running",
        }), encoding="utf-8")
        service._run = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 0,
            stdout=f"python3 /tmp/pitcrew_locked_exec.py --project getbill --skill preprod-review-run --live-file {live_path}",
            stderr="",
        ))
        with mock.patch("scripts.pitcrew_dashboard.os.kill") as kill:
            result = service.stop_preprod_review()
        self.assertEqual(os.getpid(), result["pid"])
        self.assertIn(mock.call(os.getpid(), signal.SIGTERM), kill.call_args_list)


class FakeDashboardService:
    def __init__(self):
        self.calls = []
        self.accepted_controls = []
        self.config = {"providers": {"forge": "gitlab"}}

    def snapshot(self):
        self.calls.append(("snapshot",))
        return {"project": "getbill", "global_state": "running", "counts": {"enabled": 7, "disabled": 6}}

    def history(self, skill, outcome):
        self.calls.append(("history", skill, outcome))
        return [{"skill": skill, "outcome": outcome}]

    def gitlab_work(self, force_refresh=False):
        self.calls.append(("gitlab", force_refresh))
        return {"degraded": False, "groups": {}}

    def forge_work(self, force_refresh=False):
        self.calls.append(("forge_work", force_refresh))
        return {
            "provider": self.config["providers"]["forge"],
            "degraded": False,
            "groups": {},
        }

    def runs_snapshot(self):
        self.calls.append(("runs_snapshot",))
        return {"project": "getbill", "runs": [], "capacity": {}, "has_active": False}

    def reconcile_merged_ticket_candidates(self):
        self.calls.append(("reconcile_merged_ticket_candidates",))
        return {
            "runs": [],
            "deferred": [],
            "rejected": [],
            "drained": False,
        }

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

    def global_control(self, action):
        self.calls.append(("global_control", action))
        if action not in {"stop-all", "resume-all"}:
            raise DashboardError(f"unknown action: {action}")
        self.accepted_controls.append((action,))
        return {"accepted": True, "global_state": "stopped" if action == "stop-all" else "running"}

    def change_model(self, skill, model):
        self.calls.append(("change_model", skill, model))
        if skill not in ENABLED_SKILLS or model != "gpt-5.6-sol":
            raise DashboardError("action rejected")
        return {"accepted": True, "pid": 6789, "model": model}

    def decisions(self):
        self.calls.append(("decisions",))
        return {"pending": None}

    def preprod_review_snapshot(self):
        self.calls.append(("preprod_review_snapshot",))
        return {"skill": "preprod-review-run", "history": []}

    def trigger_preprod_review(self):
        self.calls.append(("trigger_preprod_review",))
        return {"accepted": True, "pid": 1234, "skill": "preprod-review-run"}

    def stop_preprod_review(self):
        self.calls.append(("stop_preprod_review",))
        return {"accepted": True, "pid": 1234, "skill": "preprod-review-run"}

    def submit_decision(self, ticket_id, answer, notes):
        self.calls.append(("submit_decision", ticket_id, answer, notes))
        return {"accepted": True, "pid": 2468}

    def merge_merge_request(self, iid):
        self.calls.append(("merge_merge_request", iid))
        return {"accepted": True, "partial": False, "iid": iid}

    def launch_ticket_agent(self, skill, target):
        self.calls.append(("launch_ticket_agent", skill, target))
        if skill not in {"implementer-run", "unblock", "stale-sweep"}:
            raise DashboardError("action rejected")
        return {"run_id": "run-1", "state": "queued", "queue_position": 1, "created": True}

    def reconcile_merged_ticket_candidates(self):
        self.calls.append(("reconcile_merged_ticket_candidates",))
        return {"runs": [], "deferred": [], "rejected": [], "drained": False}


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
        (self.asset_root / "index.html").write_text(
            "<html><script>window.token='__PITCREW_SESSION_TOKEN__'</script></html>",
            encoding="utf-8",
        )
        (self.asset_root / "app.js").write_text(
            "window.pitcrew = true;",
            encoding="utf-8",
        )
        (self.asset_root / "styles.css").write_text(
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

    def test_api_forge_work_returns_normalized_work(self):
        status, headers, payload = self.request("GET", "/api/forge-work?refresh=1")

        self.assertEqual(200, status)
        self.assert_security_headers(headers)
        self.assertEqual("gitlab", json.loads(payload)["provider"])
        self.assertEqual([("forge_work", True)], self.service.calls)

    def test_api_gitlab_is_compatibility_alias_only_for_gitlab(self):
        status, headers, _ = self.request("GET", "/api/gitlab?refresh=1")

        self.assertEqual(200, status)
        self.assert_security_headers(headers)
        self.assertEqual([("gitlab", True)], self.service.calls)

        self.service.calls.clear()
        self.service.config = {"providers": {"forge": "github"}}

        status, headers, payload = self.request("GET", "/api/gitlab?refresh=1")
        self.assertIn(status, {404, 409})
        self.assert_security_headers(headers)
        self.assertEqual({"error": "not found"}, json.loads(payload))
        self.assertEqual([], self.service.calls)

        status, headers, payload = self.request("GET", "/api/forge-work?refresh=1")
        self.assertEqual(200, status)
        self.assert_security_headers(headers)
        self.assertEqual("github", json.loads(payload)["provider"])
        self.assertEqual([("forge_work", True)], self.service.calls)

    def test_api_decisions_returns_pending_data(self):
        status, _, payload = self.request("GET", "/api/decisions")
        self.assertEqual(200, status)
        self.assertIsNone(json.loads(payload)["pending"])
        self.assertEqual([("decisions",)], self.service.calls)

    def test_preprod_review_get_requires_session_and_exact_origin(self):
        status, _, _ = self.request("GET", "/api/preprod-review")
        self.assertEqual(403, status)

        status, _, payload = self.request(
            "GET", "/api/preprod-review",
            headers={"X-Pitcrew-Session": self.token},
        )
        self.assertEqual(200, status)
        self.assertEqual("preprod-review-run", json.loads(payload)["skill"])
        self.assertEqual([("preprod_review_snapshot",)], self.service.calls)

        status, _, _ = self.request(
            "GET", "/api/preprod-review",
            headers={"X-Pitcrew-Session": self.token, "Origin": "http://invalid.local"},
        )
        self.assertEqual(403, status)

    def test_preprod_review_actions_require_exact_payload(self):
        headers = {"Content-Type": "application/json", "X-Pitcrew-Session": self.token}
        status, _, payload = self.request(
            "POST", "/api/actions", json.dumps({"action": "trigger-preprod-review"}).encode(), headers,
        )
        self.assertEqual(202, status)
        self.assertEqual("preprod-review-run", json.loads(payload)["skill"])
        self.assertEqual([("trigger_preprod_review",)], self.service.calls)

        status, _, _ = self.request(
            "POST", "/api/actions", json.dumps({"action": "stop-preprod-review", "skill": "x"}).encode(), headers,
        )
        self.assertEqual(400, status)

    def test_post_answer_decision_requires_session_and_forwards_payload(self):
        body = json.dumps({
            "action": "answer-decision",
            "ticket_id": "getbill1/getbill#1",
            "answer": "Ship it",
            "notes": "Approved",
        }).encode()
        status, _, _ = self.request("POST", "/api/actions", body, {"Content-Type": "application/json"})
        self.assertEqual(403, status)
        status, _, payload = self.request(
            "POST", "/api/actions", body,
            {"Content-Type": "application/json", "X-Pitcrew-Session": self.token},
        )
        self.assertEqual(202, status)
        self.assertTrue(json.loads(payload)["accepted"])
        self.assertEqual([("submit_decision", "getbill1/getbill#1", "Ship it", "Approved")], self.service.calls)

    def test_post_answer_decision_rejects_extra_or_missing_fields(self):
        body = json.dumps({"action": "answer-decision", "ticket_id": "x", "answer": "y"}).encode()
        status, _, _ = self.request(
            "POST", "/api/actions", body,
            {"Content-Type": "application/json", "X-Pitcrew-Session": self.token},
        )
        self.assertEqual(400, status)
        self.assertEqual([], self.service.calls)

    def test_post_merge_merge_request_requires_session_and_validates_request(self):
        body = json.dumps({"action": "merge-merge-request", "iid": 11}).encode()
        status, _, _ = self.request(
            "POST", "/api/actions", body, {"Content-Type": "application/json"}
        )
        self.assertEqual(403, status)
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
        self.assertEqual(11, json.loads(payload)["iid"])
        self.assertEqual([("merge_merge_request", 11)], self.service.calls)

        for invalid in (
            {"action": "merge-merge-request", "iid": True},
            {"action": "merge-merge-request", "iid": 0},
            {"action": "merge-merge-request", "iid": 11, "extra": "x"},
        ):
            with self.subTest(invalid=invalid):
                status, _, _ = self.request(
                    "POST",
                    "/api/actions",
                    json.dumps(invalid).encode(),
                    {
                        "Content-Type": "application/json",
                        "X-Pitcrew-Session": self.token,
                    },
                )
                self.assertEqual(400, status)

    def test_ticket_runs_api_requires_session_and_exact_request(self):
        body = json.dumps({
            "skill": "implementer-run",
            "target": "https://gitlab.com/getbill1/getbill/-/issues/1",
        }).encode()
        status, _, _ = self.request(
            "POST", "/api/ticket-runs", body, {"Content-Type": "application/json"}
        )
        self.assertEqual(403, status)
        self.assertEqual([], self.service.calls)

        status, _, payload = self.request(
            "POST",
            "/api/ticket-runs",
            body,
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )
        self.assertEqual(202, status)
        self.assertEqual("queued", json.loads(payload)["state"])
        self.assertEqual(
            [("launch_ticket_agent", "implementer-run", "https://gitlab.com/getbill1/getbill/-/issues/1")],
            self.service.calls,
        )

        for invalid in (
            {"skill": "implementer-run"},
            {"skill": "implementer-run", "target": ""},
            {"skill": " ", "target": "x"},
            {"skill": "implementer-run", "target": " \t "},
            {"skill": "implementer-run", "target": "x", "extra": "x"},
            {"skill": 3, "target": "x"},
            {"skill": "implementer-run", "target": 3},
        ):
            with self.subTest(invalid=invalid):
                status, _, _ = self.request(
                    "POST",
                    "/api/ticket-runs",
                    json.dumps(invalid).encode(),
                    {
                        "Content-Type": "application/json",
                        "X-Pitcrew-Session": self.token,
                    },
                )
                self.assertEqual(400, status)

        status, _, _ = self.request(
            "POST", "/api/ticket-runs", body,
            {"Content-Type": "text/plain", "X-Pitcrew-Session": self.token},
        )
        self.assertEqual(400, status)
        status, _, _ = self.request(
            "POST", "/api/ticket-runs", b"x" * 8193,
            {"Content-Type": "application/json", "X-Pitcrew-Session": self.token},
        )
        self.assertEqual(413, status)

        status, _, payload = self.request(
            "POST", "/api/ticket-runs", body,
            {"Content-Type": "application/json", "X-Pitcrew-Session": self.token},
        )
        self.assertEqual(202, status)
        self.assertEqual("run-1", json.loads(payload)["run_id"])
        self.assertEqual(2, self.service.calls.count(("launch_ticket_agent", "implementer-run", "https://gitlab.com/getbill1/getbill/-/issues/1")))

    def test_ticket_runs_api_maps_rejected_and_unavailable_service(self):
        headers = {"Content-Type": "application/json", "X-Pitcrew-Session": self.token}
        rejected = json.dumps({"skill": "unknown", "target": "https://gitlab.com/getbill1/getbill/-/issues/1"}).encode()
        status, _, _ = self.request("POST", "/api/ticket-runs", rejected, headers)
        self.assertEqual(403, status)
        with mock.patch.object(self.service, "launch_ticket_agent", side_effect=RuntimeError("down")):
            status, _, _ = self.request("POST", "/api/ticket-runs", rejected.replace(b"unknown", b"implementer-run"), headers)
        self.assertEqual(500, status)

    def test_reconciliations_api_requires_session_origin_and_exact_empty_object(self):
        headers = {
            "Content-Type": "application/json",
            "X-Pitcrew-Session": self.token,
        }
        status, _, _ = self.request(
            "POST",
            "/api/reconciliations",
            b"{}",
            {"Content-Type": "application/json"},
        )
        self.assertEqual(403, status)

        status, _, _ = self.request(
            "POST",
            "/api/reconciliations",
            b"{}",
            {**headers, "Origin": "http://invalid.local"},
        )
        self.assertEqual(403, status)

        status, _, _ = self.request(
            "POST",
            "/api/reconciliations?refresh=1",
            b"{}",
            headers,
        )
        self.assertEqual(404, status)

        for invalid in (b'{"extra": true}', b"[]"):
            with self.subTest(invalid=invalid):
                status, _, _ = self.request(
                    "POST",
                    "/api/reconciliations",
                    invalid,
                    headers,
                )
                self.assertEqual(400, status)

        self.assertEqual([], self.service.calls)
        status, _, payload = self.request(
            "POST",
            "/api/reconciliations",
            b"{}",
            headers,
        )
        self.assertEqual(202, status)
        self.assertEqual(
            {
                "runs": [],
                "deferred": [],
                "rejected": [],
                "drained": False,
            },
            json.loads(payload),
        )
        self.assertEqual(
            [("reconcile_merged_ticket_candidates",)],
            self.service.calls,
        )

    def test_reconciliations_api_maps_rejected_and_unavailable_service_safely(self):
        headers = {
            "Content-Type": "application/json",
            "X-Pitcrew-Session": self.token,
        }
        with mock.patch.object(
            self.service,
            "reconcile_merged_ticket_candidates",
            side_effect=DashboardError("provider details"),
        ):
            status, _, payload = self.request(
                "POST",
                "/api/reconciliations",
                b"{}",
                headers,
            )
        self.assertEqual(403, status)
        self.assertEqual({"error": "action rejected"}, json.loads(payload))

        with mock.patch.object(
            self.service,
            "reconcile_merged_ticket_candidates",
            side_effect=RuntimeError("provider details"),
        ):
            status, _, payload = self.request(
                "POST",
                "/api/reconciliations",
                b"{}",
                headers,
            )
        self.assertEqual(500, status)
        self.assertEqual({"error": "service unavailable"}, json.loads(payload))

    def test_runs_api_returns_durable_snapshot(self):
        status, _, _ = self.request("GET", "/api/runs")
        self.assertEqual(403, status)
        self.assertEqual([], self.service.calls)
        status, _, _ = self.request(
            "GET", "/api/runs?x=1", headers={"X-Pitcrew-Session": self.token},
        )
        self.assertEqual(403, status)
        self.assertEqual([], self.service.calls)
        status, _, payload = self.request(
            "GET", "/api/runs", headers={"X-Pitcrew-Session": self.token},
        )

        self.assertEqual(200, status)
        self.assertEqual("getbill", json.loads(payload)["project"])
        self.assertEqual([("runs_snapshot",)], self.service.calls)

    def test_ticket_runs_api_rejects_query_without_service_call(self):
        status, _, _ = self.request(
            "POST", "/api/ticket-runs?x=1",
            json.dumps({"skill": "implementer-run", "target": "https://gitlab.com/getbill1/getbill/-/issues/1"}).encode(),
            {"Content-Type": "application/json", "X-Pitcrew-Session": self.token},
        )

        self.assertEqual(404, status)
        self.assertEqual([], self.service.calls)

    def test_favicon_probe_returns_empty_no_content(self):
        status, headers, payload = self.request("GET", "/favicon.ico")

        self.assertEqual(204, status)
        self.assertEqual(b"", payload)
        self.assertEqual("0", headers["content-length"])
        self.assertNotIn(self.token.encode(), payload)
        self.assert_security_headers(headers)
        self.assertEqual([], self.service.calls)

        missing_status, missing_headers, _ = self.request("GET", "/favicon.png")
        self.assertEqual(404, missing_status)
        self.assert_security_headers(missing_headers)

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

        (self.asset_root / "app.js").unlink()
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
            app = self.asset_root / "app.js"
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

    def test_post_action_accepts_global_stop_without_skill(self):
        status, _, payload = self.request(
            "POST",
            "/api/actions",
            json.dumps({"action": "stop-all"}).encode(),
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )
        self.assertEqual(202, status)
        self.assertEqual("stopped", json.loads(payload)["global_state"])
        self.assertEqual([("global_control", "stop-all")], self.service.calls[-1:])

        status, _, _ = self.request(
            "POST",
            "/api/actions",
            json.dumps({"action": "resume-all", "skill": "research-run"}).encode(),
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )
        self.assertEqual(400, status)

    def test_post_change_model_accepts_exact_request_and_forwards_model(self):
        body = json.dumps(
            {
                "action": "change-model",
                "skill": "research-run",
                "model": "gpt-5.6-sol",
            }
        ).encode()

        status, headers, payload = self.request(
            "POST",
            "/api/actions",
            body,
            {
                "Content-Type": "application/json",
                "X-Pitcrew-Session": self.token,
            },
        )

        self.assertEqual(202, status)
        self.assertEqual(
            {"accepted": True, "pid": 6789, "model": "gpt-5.6-sol"},
            json.loads(payload),
        )
        self.assertEqual(
            [("change_model", "research-run", "gpt-5.6-sol")],
            self.service.calls,
        )
        self.assert_security_headers(headers)

    def test_post_change_model_rejects_malformed_and_boundary_requests(self):
        headers = {
            "Content-Type": "application/json",
            "X-Pitcrew-Session": self.token,
        }
        cases = (
            ({"action": "change-model", "skill": "research-run"}, headers, 400),
            (
                {"action": "change-model", "skill": "research-run", "model": ""},
                headers,
                400,
            ),
            (
                {"action": "change-model", "skill": "research-run", "model": 1},
                headers,
                400,
            ),
            (
                {
                    "action": "change-model",
                    "skill": "research-run",
                    "model": "gpt-5.6-sol",
                    "extra": True,
                },
                headers,
                400,
            ),
            (
                {"action": "change-model", "skill": "research-run", "model": "gpt-5.6-sol"},
                {"Content-Type": "application/json"},
                403,
            ),
            (
                {"action": "change-model", "skill": "research-run", "model": "gpt-5.6-sol"},
                {**headers, "Origin": "https://evil.example"},
                403,
            ),
        )
        for request, request_headers, expected in cases:
            with self.subTest(request=request):
                status, response_headers, _ = self.request(
                    "POST",
                    "/api/actions",
                    json.dumps(request).encode(),
                    request_headers,
                )
                self.assertEqual(expected, status)
                self.assert_security_headers(response_headers)
        self.assertEqual([], self.service.calls)

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

    def test_saturated_server_rejects_connections_and_recovers_capacity(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        with mock.patch.object(
            SERVER.DashboardHTTPServer,
            "max_concurrent_requests",
            1,
            create=True,
        ):
            self.server = SERVER.create_server(
                "127.0.0.1", 0, self.service, self.token
            )
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

        first = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        first.settimeout(1)
        first.sendall(b"GET")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            slots = getattr(self.server, "_request_slots", None)
            if slots is not None and not slots.acquire(blocking=False):
                break
            if slots is not None:
                slots.release()
            time.sleep(0.01)
        else:
            self.fail("partial request did not occupy the only request slot")

        with socket.create_connection(("127.0.0.1", self.port), timeout=1) as second:
            second.settimeout(1)
            second.sendall(
                f"GET /api/status HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode()
            )
            try:
                rejected = second.recv(4096)
            except ConnectionResetError:
                rejected = b""
            self.assertEqual(b"", rejected)
        self.assertEqual([], self.service.calls)

        first.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            slots = getattr(self.server, "_request_slots", None)
            if slots is not None and slots.acquire(blocking=False):
                slots.release()
                break
            time.sleep(0.01)
        else:
            self.fail("request slot was not released after connection close")

        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(200, status)
        self.assertEqual([("snapshot",)], self.service.calls)

    def test_incomplete_headers_time_out_without_blocking_shutdown(self):
        request = (
            "GET /api/status HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            "X-Incomplete: waiting"
        ).encode()
        with (
            mock.patch.object(SERVER, "READ_TIMEOUT_SECONDS", 0.1),
            socket.create_connection(("127.0.0.1", self.port), timeout=1) as client,
        ):
            client.settimeout(0.8)
            client.sendall(request)
            started = time.monotonic()
            try:
                response = client.recv(4096)
            except socket.timeout:
                self.fail("server left incomplete headers waiting")
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.8)
        if response:
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


class DashboardAssetContractTest(unittest.TestCase):
    def setUp(self):
        self.dashboard = ROOT / "dashboard"
        self.html = (self.dashboard / "index.html").read_text(encoding="utf-8")
        self.javascript = (self.dashboard / "app.js").read_text(encoding="utf-8")
        self.styles = (self.dashboard / "styles.css").read_text(encoding="utf-8")
        self.modules = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.dashboard.glob("*.mjs")
        }

    def test_all_dashboard_modules_are_served(self):
        self.assertEqual(
            {path.name for path in self.dashboard.glob("*.mjs")},
            {asset for asset, _ in SERVER.ASSET_ROUTES.values() if asset.endswith(".mjs")},
        )

    def test_agents_are_compact_and_details_are_progressively_disclosed(self):
        agents = self.modules["agents.mjs"]
        self.assertIn('className = "agent-row"', agents)
        self.assertIn('document.createElement("details")', agents)
        self.assertIn("Dernier passage", agents)
        self.assertIn("Prochain passage", agents)
        for detail in ("Modèle", "Jetons", "Coût estimé", "Fréquence", "PID"):
            self.assertIn(detail, agents)
        for action in ("Déclencher", "Réinstaller", "Arrêter"):
            self.assertIn(action, agents)
        self.assertNotIn("innerHTML", agents)

    def test_manual_merge_is_only_offered_for_gitlab_merge_requests(self):
        self.assertIn('work.provider === "gitlab"', self.javascript)
        self.assertIn('change.kind === "merge_request"', self.javascript)
        self.assertIn('action: "merge-merge-request"', self.javascript)

    def test_pilotage_modules_and_application_wiring_contract(self):
        self.assertIn("pilotage.mjs", self.modules)
        self.assertIn("detail-panel.mjs", self.modules)

        pilotage = self.modules["pilotage.mjs"]
        for token in (
            "buildActionQueue",
            "buildWorkflow",
            "queue.slice(0, 3)",
            "Voir toutes",
            "ACTIVE_LIFECYCLES",
            "textContent",
            "createElement",
        ):
            self.assertIn(token, pilotage)
        self.assertNotIn("innerHTML", pilotage)
        self.assertNotIn("insertAdjacentHTML", pilotage)

        detail_panel = self.modules["detail-panel.mjs"]
        for token in (
            "showModal",
            'addEventListener("close"',
            "previousFocus",
            "previousFocusKey",
            'querySelectorAll("[data-focus-key]")',
            "focusTarget?.focus()",
            "event.target === dialog",
        ):
            self.assertIn(token, detail_panel)
        self.assertNotIn("innerHTML", detail_panel)
        self.assertNotIn("CSS.escape", detail_panel)

        for token in (
            'from "./pilotage.mjs"',
            'from "./detail-panel.mjs"',
            "renderPilotage",
            "renderPilotageView",
            "openItem",
            "openAllActions",
            "workflowFilters",
            "syncWorkflowRoles",
            "lastRoleWork",
            "sources.work !== lastRoleWork",
            'action: "answer-decision"',
            'action: "decide-proposal"',
            'action: "merge-merge-request"',
            'api.post("/api/ticket-runs", {skill, target})',
            'api.post("/api/reconciliations", {})',
        ):
            self.assertIn(token, self.javascript)

        self.assertEqual(1, len(re.findall(r"<dialog\b", self.html)))
        for identifier in (
            "action-queue-list",
            "workflow-board-content",
            "done-list",
            "crew-health",
        ):
            self.assertIn(f'id="{identifier}"', self.html)
        for identifier in ("proposals", "decision-banner", "forge-work"):
            self.assertRegex(
                self.html,
                rf'<section[^>]+id="{identifier}"[^>]+\bhidden\b',
            )

    def test_html_has_application_shell_views_and_future_module_regions(self):
        for identifier in (
            "app-header",
            "app-navigation",
            "view-pilotage",
            "view-agents",
            "view-history",
            "action-queue",
            "workflow-board",
            "done-work",
            "crew-health",
            "detail-panel",
        ):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertLess(
            self.html.index('id="action-queue"'),
            self.html.index('id="workflow-board"'),
        )
        self.assertRegex(self.html, r"<dialog\b")
        for view in ("pilotage", "agents", "history"):
            self.assertRegex(
                self.html,
                rf'<section[^>]+id="view-{view}"[^>]*>\s*'
                rf'<h1[^>]+tabindex="-1"',
            )
            self.assertIn(f'data-view-target="{view}"', self.html)

    def test_html_is_semantic_accessible_and_has_all_dashboard_regions(self):
        self.assertRegex(self.html, r"<html[^>]+lang=[\"']fr[\"']")
        for element in ("header", "main", "section"):
            self.assertRegex(self.html, rf"<{element}\b")
        self.assertRegex(
            self.html,
            r'id=[\"\']operational-status[\"\'][^>]+aria-live=[\"\']polite[\"\']',
        )
        for identifier in (
            "overview",
            "decision-banner",
            "live-agents",
            "agents",
            "activity",
            "forge-work",
            "changes",
            "disabled-roles",
        ):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertEqual(1, self.html.count("__PITCREW_SESSION_TOKEN__"))
        self.assertIn(
            '<meta name="pitcrew-session" content="__PITCREW_SESSION_TOKEN__">',
            self.html,
        )
        self.assertIn('<link rel="stylesheet" href="/assets/styles.css">', self.html)
        self.assertIn(
            '<script type="module" src="/assets/app.js"></script>',
            self.html,
        )
        self.assertIn('type="button"', self.html)

    def test_sources_are_local_and_use_no_external_assets(self):
        combined = "\n".join(
            (self.html, self.javascript, self.styles, *self.modules.values())
        )
        self.assertIsNone(re.search(r"https?://", combined, re.IGNORECASE))
        self.assertNotRegex(self.html, r"<(?:img|iframe|object|embed)\b")
        self.assertNotIn("@font-face", self.styles)

    def test_javascript_polls_safely_and_uses_text_dom_apis(self):
        self.assertIn("const ACTIVE_POLL_INTERVAL_MS = 2_000;", self.javascript)
        self.assertIn("const IDLE_POLL_INTERVAL_MS = 10_000;", self.javascript)
        self.assertIn("const FORGE_REFRESH_MS = 60_000;", self.javascript)
        self.assertIn(
            'const ACTIONS = new Set(["trigger", "stop", "restart"]);',
            self.javascript,
        )
        self.assertRegex(
            self.javascript,
            r"\.type\s*=\s*[\"']button[\"']",
        )
        self.assertIn('"X-Pitcrew-Session"', self.javascript)
        self.assertIn(
            "Arrêter ${skill} et son passage courant ?",
            self.javascript,
        )
        self.assertIn(".textContent", self.javascript)
        self.assertNotIn("innerHTML", self.javascript)
        self.assertNotIn("insertAdjacentHTML", self.javascript)
        for identifier in ("global-actions", "global-stop-button", "global-resume-button"):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertIn("Arrêter tous les agents et bloquer les futures exécutions ?", self.javascript)
        self.assertIn('api.action({action})', self.javascript)
        self.assertIn("source_branch", self.javascript)
        self.assertIn("target_branch", self.javascript)
        self.assertIn("Fusionner et supprimer la branche", self.javascript)
        self.assertIn('action: "merge-merge-request"', self.javascript)
        self.assertIn("window.confirm", self.javascript)
        self.assertIn("mergeMergeRequest(change, work)", self.javascript)
        self.assertIn("renderChanges(work)", self.javascript)
        self.assertIn("issue.agent_action", self.javascript)
        self.assertIn("ticket-agent-actions", self.javascript)
        self.assertIn('api.post("/api/ticket-runs"', self.javascript)
        self.assertIn('api.get("/api/runs"', self.javascript)
        self.assertNotIn('action: "launch-ticket-agent"', self.javascript)
        self.assertIn("pendingTicketActions", self.javascript)
        self.assertIn("active_run", self.javascript)
        self.assertIn("queue_position", self.javascript)
        self.assertIn("max_concurrent", self.javascript)
        self.assertIn("ticket-run-status", self.javascript)
        self.assertIn("window.setTimeout", self.javascript)
        self.assertNotIn("window.setInterval", self.javascript)
        self.assertIn("Lancement…", self.javascript)
        self.assertIn("Lancement accepté", self.javascript)
        self.assertIn("Agent indisponible", self.javascript)
        self.assertIn('await refresh({ manual: true });', self.javascript)
        self.assertIn('/api/decisions', self.javascript)
        self.assertIn('answer-decision', self.javascript)
        self.assertIn("scheduleRefresh", self.javascript)
        self.assertIn("const becameTerminal = hadActiveRuns && !latestRuns.has_active;", self.javascript)
        self.assertIn("if (becameTerminal) lastForgeRefresh = 0;", self.javascript)
        self.assertIn('const forgePath = "/api/forge-work";', self.javascript)
        self.assertIn("lastForgeRefresh = now;", self.javascript)
        forge_refresh = self.javascript[
            self.javascript.index("async function refreshForgeWork"):
            self.javascript.index("function sourceStatus")
        ]
        self.assertIn(
            '() => api.post("/api/reconciliations", {})',
            forge_refresh,
        )
        self.assertIn(
            'sourceStore.load(\n      "reconciliation"',
            forge_refresh,
        )
        self.assertLess(
            forge_refresh.index('() => api.post("/api/reconciliations", {})'),
            forge_refresh.index("lastForgeRefresh = now;"),
        )
        self.assertIn("let detailTicketView = null;", self.javascript)
        self.assertIn("function syncDetailTicketAction()", self.javascript)
        self.assertIn("detailTicketView = {actions, button, issue: resource, target};", self.javascript)
        self.assertIn('run?.state === "cancelled"', self.javascript)
        self.assertIn("Annulé · Relancer", self.javascript)
        self.assertNotIn("singleUse: true", self.javascript)
        for token in (".ticket-run-status", ".ticket-run-queued", ".ticket-run-running", ".ticket-run-failed", ".ticket-run-cancelled", '[aria-busy="true"]'):
            self.assertIn(token, self.styles)
        for function_name in (
            "renderOverview",
            "renderLiveAgents",
            "renderAgents",
            "renderForgeWork",
            "renderChanges",
            "mergeMergeRequest",
            "launchTicketAgent",
            "renderDecision",
            "submitDecision",
            "control",
            "refresh",
        ):
            self.assertRegex(
                self.javascript,
                rf"(?:async\s+)?function\s+{function_name}\b",
            )

    def test_css_supports_laptop_shell_workflow_and_accessibility(self):
        for selector in (
            ".app-header",
            ".app-shell",
            ".app-sidebar",
            ".action-queue-list",
            ".workflow-columns",
            ".workflow-lane",
            ".agent-row",
            "#detail-panel",
            ".source-state-stale",
        ):
            self.assertIn(selector, self.styles)
        self.assertIn("position: sticky", self.styles)
        self.assertIn("overflow-x: auto", self.styles)
        self.assertIn(":focus-visible", self.styles)
        self.assertIn("@media (max-width: 900px)", self.styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)

    def test_html_wires_the_application_shell_to_its_css_classes(self):
        for element, identifier, class_name in (
            ("header", "app-header", "app-header"),
            ("aside", None, "app-sidebar"),
            ("main", None, "app-main"),
            ("div", "action-queue-list", "action-queue-list"),
            ("div", "workflow-board-content", "workflow-columns"),
        ):
            identifier_match = (
                rf'(?=[^>]*\bid="{re.escape(identifier)}")'
                if identifier
                else ""
            )
            self.assertRegex(
                self.html,
                rf"<{element}\b{identifier_match}(?=[^>]*\bclass=\"[^\"]*"
                rf"\b{re.escape(class_name)}\b[^\"]*\")[^>]*>",
            )
        self.assertEqual(
            3,
            len(re.findall(r"<button\b[^>]*\bclass=\"[^\"]*\bnav-item\b", self.html)),
        )
        self.assertRegex(
            self.html,
            r'<details\b(?=[^>]*\bid="done-work")[^>]*>\s*'
            r'<summary\b(?=[^>]*\bid="done-work-title")[^>]*>'
            r'Travail terminé\s*<span id="done-count">0</span></summary>',
        )
        self.assertNotRegex(
            self.html,
            r'<details\b(?=[^>]*\bid="done-work")[^>]*\bopen\b',
        )
        self.assertNotIn("site-header", self.html)
        self.assertNotIn(".site-header", self.styles)
        self.assertNotIn(".app-shell > main", self.styles)
        self.assertNotIn(".nav-count", self.styles)
        self.assertRegex(
            self.styles,
            r"(?s)\.action-queue-list\s*{[^}]*align-items:\s*start;",
        )
        self.assertRegex(
            self.styles,
            r"(?s)\.action-card p[^}]*-webkit-line-clamp:\s*3;",
        )
        self.assertIn("#done-work > summary", self.styles)

    def test_pilotage_layout_is_responsive_and_wraps_content(self):
        for token in (
            ".app-shell",
            "grid-template-columns: 190px minmax(0, 1fr)",
            "grid-template-columns: 72px minmax(0, 1fr)",
            "#app-navigation",
            ".workflow-lane",
            ".workflow-card",
            "min-width: 0",
            "overflow-wrap: anywhere",
            "#detail-panel",
            "@media (max-width: 620px)",
        ):
            self.assertIn(token, self.styles)

    def test_history_module_keeps_filters_and_safe_metadata(self):
        history = self.modules["history.mjs"]
        self.assertIn("export function renderHistory", history)
        self.assertIn("export function historyPath", history)
        self.assertIn("URLSearchParams", history)
        self.assertIn("total_tokens", history)
        self.assertIn("Modèle/usage indisponibles", history)
        self.assertIn('metadata.className = "history-metadata";', history)
        self.assertIn("metadata.textContent", history)
        self.assertIn("Object.hasOwn(modelCatalog, model)", history)
        self.assertNotIn("innerHTML", history)

    def test_history_requests_share_latest_request_coordinator(self):
        history = self.modules["history.mjs"]
        self.assertIn("export function createLatestRequestCoordinator", history)
        self.assertIn(
            "const coordinateHistoryRequest = createLatestRequestCoordinator();",
            self.javascript,
        )
        self.assertIn(
            "coordinateHistoryRequest(() => fetchJson(path))",
            self.javascript,
        )
        self.assertNotIn("fetchJson(historyPath(", self.javascript)
        self.assertIn("if (!result.applied)", self.javascript)
        self.assertIn("if (result.error)", self.javascript)
        self.assertIn(
            "if (history.applied && !history.error && history.data != null)",
            self.javascript,
        )
        self.assertIn(".catch(showHistoryError)", self.javascript)

    def test_independent_sources_preserve_stale_data_and_action_feedback(self):
        api = self.modules["api.mjs"]
        source_store = self.modules["source-store.mjs"]
        pilotage = self.modules["pilotage.mjs"]

        self.assertIn("export function createApi", api)
        self.assertIn("function get(path, options = {})", api)
        self.assertIn('"X-Pitcrew-Session": sessionToken', api)
        self.assertIn("export function createSourceStore", source_store)
        self.assertIn("requestVersions", source_store)
        self.assertIn("previous?.data ?? null", source_store)

        for identifier in (
            "pilotage-source-state",
            "agents-source-state",
            "history-source-state",
        ):
            self.assertRegex(
                self.html,
                rf'id="{identifier}"[^>]+aria-live="polite"',
            )

        for token in (
            'from "./api.mjs"',
            'from "./source-store.mjs"',
            "const api = createApi(sessionToken);",
            "const sourceStore = createSourceStore();",
            "async function refreshLocal",
            "async function refreshHumanActions",
            "async function refreshForgeWork",
            "function renderSourceStates",
            'button.textContent = "Réessayer";',
            "const actionStates = new Map();",
            "async function runAction",
        ):
            self.assertIn(token, self.javascript)
        self.assertNotIn("fetch(", self.javascript)
        self.assertIn("action.state", pilotage)
        self.assertNotIn("innerHTML", pilotage)

    def test_global_and_preprod_actions_keep_resource_feedback(self):
        preprod = self.javascript.split(
            "async function runPreprodReviewAction",
            1,
        )[1].split("function finiteNumber", 1)[0]
        global_control = self.javascript.split(
            "async function globalControl",
            1,
        )[1].split("function restoreModelSelect", 1)[0]

        for source, key in (
            (preprod, "preprod:review"),
            (global_control, "global:crew"),
        ):
            self.assertIn("await runAction(", source)
            self.assertIn(f'"{key}"', source)
            self.assertIn("() => api.action({action})", source)
            self.assertLess(source.index(f'"{key}"'), source.index("api.action({action})"))

    def test_global_and_preprod_feedback_is_visible_and_reapplied_after_refresh(self):
        for identifier in ("global-action-status", "preprod-review-action-status"):
            self.assertRegex(
                self.html,
                rf'id="{identifier}"[^>]+aria-live="polite"',
            )

        self.assertIn("renderActionFeedback", self.javascript)
        self.assertIn("renderResourceActionState(key)", self.javascript)
        overview = self.javascript.split(
            "function renderOverview",
            1,
        )[1].split("function formatElapsed", 1)[0]
        preprod = self.javascript.split(
            "function renderPreprodReview",
            1,
        )[1].split("async function runPreprodReviewAction", 1)[0]
        self.assertIn('renderResourceActionState("global:crew")', overview)
        self.assertIn('renderResourceActionState("preprod:review")', preprod)

    def test_javascript_serializes_controls_per_skill(self):
        agents = self.modules["agents.mjs"]
        self.assertIn("const pendingSkills = new Set();", self.javascript)
        self.assertRegex(
            self.javascript,
            r"if\s*\(pendingSkills\.has\(skill\)\)\s*{\s*return;\s*}",
        )
        self.assertIn("pendingSkills.add(skill);", self.javascript)
        self.assertIn("pendingSkills.delete(skill);", self.javascript)
        self.assertIn('button.dataset.skill = skill;', agents)
        self.assertIn('querySelectorAll("[data-skill]")', self.javascript)
        self.assertIn(
            "button.disabled = handlers.controlDisabled?.(skill) === true;",
            agents,
        )
        self.assertIn('snapshot?.global_state === "stopped" || pendingSkills.has(skill)', self.javascript)
        control_source = self.javascript.split(
            "async function control",
            1,
        )[1].split("async function globalControl", 1)[0]
        self.assertLess(
            control_source.index("pendingSkills.has(skill)"),
            control_source.index("api.action"),
        )
        self.assertRegex(
            control_source,
            r"finally\s*{[^}]*pendingSkills\.delete\(skill\);",
        )

    def test_javascript_marks_agent_grid_busy_for_entire_refresh(self):
        refresh_source = self.javascript.split(
            "async function refresh",
            1,
        )[1]
        self.assertIn(
            'elements.agentGrid.setAttribute("aria-busy", "true");',
            refresh_source,
        )
        self.assertRegex(
            refresh_source,
            (
                r"finally\s*{\s*"
                r'elements\.agentGrid\.setAttribute\("aria-busy", "false"\);'
            ),
        )

    def test_css_has_accessible_states_responsiveness_and_motion_fallback(self):
        self.assertIn(":focus-visible", self.styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)
        self.assertRegex(self.styles, r"@media\s*\(max-width:\s*\d+px\)")
        self.assertIn("--warning", self.styles)
        self.assertIn("--error", self.styles)
        self.assertIn(".button-danger", self.styles)
        self.assertIn(".global-state-blocked", self.styles)
        self.assertIn("text-align: left", self.styles)

    def test_compact_agent_rows_have_laptop_and_mobile_layouts(self):
        for selector in (
            ".agent-row",
            ".agent-row-main",
            ".agent-fact",
            ".agent-detail-grid",
            ".agent-controls",
        ):
            self.assertIn(f"{selector} {{", self.styles)
        self.assertRegex(
            self.styles,
            r"(?s)\.agent-row-main\s*{[^}]*display:\s*grid;"
            r"[^}]*grid-template-columns:",
        )
        self.assertRegex(
            self.styles,
            r"(?s)\.agent-controls\s*{[^}]*display:\s*flex;"
            r"[^}]*flex-wrap:\s*wrap;",
        )
        self.assertRegex(
            self.styles,
            r"(?s)\.agent-row details\s*{[^}]*border-top:"
            r".*?\.agent-row summary\s*{[^}]*cursor:\s*pointer;",
        )
        mobile = self.styles.split("@media (max-width: 620px)", 1)[1]
        for selector in (".agent-row-main", ".agent-detail-grid"):
            self.assertRegex(
                mobile,
                rf"(?s){re.escape(selector)}\s*{{[^}}]*"
                r"grid-template-columns:\s*1fr;",
            )
        tablet = self.styles.split("@media (max-width: 900px)", 1)[1].split(
            "@media (max-width: 620px)",
            1,
        )[0]
        self.assertRegex(
            tablet,
            r"(?s)\.agent-row-main\s*{[^}]*grid-template-columns:"
            r"\s*minmax\(180px,\s*2fr\)"
            r"\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);",
        )

    def test_model_controls_and_usage_metrics_are_rendered_from_safe_dom_apis(self):
        formatters = self.modules["format.mjs"]
        self.assertNotIn('id="overview" class="panel overview" aria-labelledby="overview-title" hidden', self.html)
        for identifier in ("metric-tokens-7d", "metric-cost-7d", "metric-cost-total"):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertIn('document.createElement("select")', self.javascript)
        self.assertIn("select.dataset.skill = skill;", self.javascript)
        self.assertIn("renderUsage", self.javascript)
        self.assertIn("Données indisponibles", self.javascript)
        self.assertIn("Sous-total mesuré", self.javascript)
        self.assertIn("formatTokens", self.javascript)
        self.assertIn("formatUsd", self.javascript)
        self.assertIn("usage_total", self.javascript)
        self.assertIn("passage(s) mesuré(s) depuis le début de l’historique", self.javascript)
        self.assertIn('typeof value === "string" && /^\\d+$/.test(value)', formatters)
        self.assertIn("BigInt(value)", formatters)
        self.assertNotIn("Number(value)", formatters)
        self.assertNotIn("innerHTML", self.javascript)

    def test_model_change_is_separate_confirmed_and_session_authenticated(self):
        self.assertRegex(
            self.javascript,
            r"async function changeModel\(skill, model, previous, select\)",
        )
        self.assertIn("passage courant sera interrompu", self.javascript)
        self.assertIn("relancé immédiatement", self.javascript)
        self.assertIn(
            'api.action({action: "change-model", skill, model})',
            self.javascript,
        )
        self.assertIn('querySelectorAll("[data-skill]")', self.javascript)
        self.assertNotIn('"change-model"', self.javascript.split("const ACTIONS", 1)[1].split(";", 1)[0])
        self.assertIn("restoreModelSelect", self.javascript)
        self.assertIn("await refreshFresh({ manual: true, skipForge: true });", self.javascript)
        change_source = self.javascript.split("async function changeModel", 1)[1].split("async function refreshHistory", 1)[0]
        self.assertLess(
            change_source.index("restoreModelSelect(select, previous);"),
            change_source.index("void refresh({ manual: true });"),
        )
        self.assertLess(
            change_source.rindex("restoreModelSelect(select, previous);"),
            change_source.index("await refreshFresh({ manual: true, skipForge: true });"),
        )

    def test_model_change_waits_for_an_inflight_refresh_before_a_fresh_status_fetch(self):
        self.assertIn('import {refreshAfterPending} from "./refresh.mjs";', self.javascript)
        self.assertRegex(
            self.javascript,
            r"async function refreshFresh\([^)]*\)\s*{\s*"
            r"return refreshAfterPending\(refreshPromise, refresh",
        )

    def test_usage_and_model_styles_are_compact_and_responsive(self):
        for selector in (".agent-model", ".usage-section", ".usage-grid", ".usage-note"):
            self.assertIn(selector, self.styles)
        self.assertIn("var(--line)", self.styles)
        self.assertIn("var(--muted)", self.styles)

    def test_preprod_review_panel_is_local_safe_and_has_only_manual_controls(self):
        for identifier in (
            "preprod-review", "preprod-review-title", "preprod-review-state",
            "preprod-review-report", "preprod-review-trigger", "preprod-review-stop",
            "preprod-review-history", "preprod-review-history-list",
        ):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertLess(self.html.index('id="preprod-review"'), self.html.index('id="forge-work"'))
        self.assertIn("Revue avant Preprod", self.html)
        self.assertIn("Contrôle manuel", self.html)
        self.assertIn("origin/preprod...origin/develop", self.html)
        self.assertIn("Sol/xhigh", self.html)
        self.assertRegex(
            self.javascript,
            r'api\.get\("/api/preprod-review", \{\s*'
            r'headers: \{"X-Pitcrew-Session": sessionToken\}',
        )
        self.assertIn('headers: {"X-Pitcrew-Session": sessionToken}', self.javascript)
        self.assertIn("sources.preprod", self.javascript)
        self.assertIn('runPreprodReviewAction("trigger-preprod-review")', self.javascript)
        self.assertIn('runPreprodReviewAction("stop-preprod-review")', self.javascript)
        self.assertIn('await refreshFresh({ manual: true, skipForge: true });', self.javascript)
        self.assertIn("longue et coûteuse", self.javascript)
        self.assertNotIn("innerHTML", self.javascript)
        self.assertNotIn("insertAdjacentHTML", self.javascript)
        for token in (
            "PRÊT", "CORRECTIONS REQUISES", "INCOMPLET",
            "Dernier passage interrompu ou échoué : le rapport précédent est obsolète",
            "Rapport précédent", "critical", "high", "medium", "low",
        ):
            self.assertIn(token, self.javascript)
        self.assertIn("preprodTextList(finding.evidence)", self.javascript)
        self.assertIn("Revue locale indisponible", self.javascript)
        self.assertIn("Lancer la revue complète", self.html)
        self.assertIn(".preprod-review-panel", self.styles)
        self.assertNotRegex(self.html, r'preprod-review[^>]*(?:model|restart|merge|deploy)')


class DashboardRealAssetsHttpTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeDashboardService()
        self.token = "real-assets-session-token"
        self.server = SERVER.create_server(
            "127.0.0.1",
            0,
            self.service,
            self.token,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(
            "GET",
            path,
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        connection.close()
        return status, payload

    def test_real_asset_root_serves_interface_and_replaces_only_index_token(self):
        status, index = self.request("/")
        self.assertEqual(200, status)
        self.assertIn(self.token.encode(), index)
        self.assertNotIn(b"__PITCREW_SESSION_TOKEN__", index)

        for path in (
            "/assets/app.js",
            "/assets/styles.css",
            "/assets/navigation.mjs",
            "/assets/view-model.mjs",
        ):
            with self.subTest(path=path):
                status, payload = self.request(path)
                self.assertEqual(200, status)
                self.assertTrue(payload)
                self.assertNotIn(self.token.encode(), payload)


if __name__ == "__main__":
    unittest.main()
