import unittest

from scripts.pitcrew_forge_work import (
    ForgeWorkError,
    GitHubForgeWork,
    GitLabForgeWork,
    canonical_issue_target,
    ticket_agent_action,
    normalize_change,
    normalize_issue,
)


def gitlab_config():
    return {
        "providers": {"forge": "gitlab", "tracker": "gitlab"},
        "gitlab": {
            "host": "gitlab.com",
            "project_id": 42,
            "project_path": "acme/payments",
        },
    }


def gitlab_issues():
    return [{
        "iid": 1,
        "title": "Stale payment",
        "description": "Related !7",
        "labels": ["pitcrew-agent", "pitcrew-state::todo"],
        "state": "opened",
        "web_url": "https://gitlab.com/acme/payments/-/issues/1",
    }]


def gitlab_merge_requests():
    return [{
        "iid": 7,
        "title": "Fix stale payment",
        "state": "opened",
        "web_url": "https://gitlab.com/acme/payments/-/merge_requests/7",
        "source_branch": "fix/one",
        "target_branch": "main",
        "author": {"username": "octocat"},
        "head_pipeline": {"status": "success"},
        "sha": "a" * 40,
        "description": "Closes #1",
    }]


class GitLabRunner:
    def __init__(self, malformed=False):
        self.calls = []
        self.malformed = malformed

    def __call__(self, args, **kwargs):
        import json
        import subprocess

        self.calls.append(list(args))
        path = args[-1]
        payload = "invalid" if self.malformed else (
            gitlab_merge_requests() if "merge_requests" in path else gitlab_issues()
        )
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")


class ForgeWorkSchemaTest(unittest.TestCase):
    def test_normalized_issue_has_no_provider_specific_identity_fields(self):
        issue = normalize_issue(
            provider="gitlab",
            number=12,
            title="Payment total is stale",
            body="Observed after retry",
            labels=[
                "pitcrew-agent",
                "pitcrew-type::bug",
                "pitcrew-state::todo",
            ],
            state="open",
            canonical_url="https://gitlab.com/getbill1/getbill/-/issues/12",
            lifecycle="todo",
            route=None,
            source=None,
            related_change_urls=[],
            bugfix=None,
        )
        self.assertEqual(
            {
                "provider": "gitlab",
                "resource_type": "issue",
                "number": 12,
                "reference": "#12",
                "title": "Payment total is stale",
                "body": "Observed after retry",
                "labels": [
                    "pitcrew-agent",
                    "pitcrew-type::bug",
                    "pitcrew-state::todo",
                ],
                "state": "open",
                "canonical_url": "https://gitlab.com/getbill1/getbill/-/issues/12",
                "lifecycle": "todo",
                "route": None,
                "source": None,
                "related_change_urls": [],
                "bugfix": {
                    "reproduction": None,
                    "verification": None,
                    "ready_head_sha": None,
                    "blocked_reason": None,
                },
                "agent_action": None,
            },
            issue,
        )
        self.assertNotIn("iid", issue)
        self.assertNotIn("web_url", issue)

    def test_normalized_change_uses_pull_or_merge_request_kind(self):
        change = normalize_change(
            provider="github",
            kind="pull_request",
            number=7,
            title="Fix stale payment total",
            canonical_url="https://github.com/acme/payments/pull/7",
            state="open",
            source_branch="fix/issue-12",
            target_branch="main",
            author="octocat",
            checks_status="passing",
            head_sha="a" * 40,
        )
        self.assertEqual("pull_request", change["kind"])
        self.assertEqual("#7", change["reference"])
        self.assertEqual("passing", change["checks_status"])

    def test_normalizers_reject_unsafe_urls_and_invalid_numbers(self):
        with self.assertRaises(ForgeWorkError):
            normalize_issue(
                provider="github",
                number=0,
                title="x",
                body="",
                labels=[],
                state="open",
                canonical_url="javascript:alert(1)",
                lifecycle="todo",
                route=None,
                source=None,
                related_change_urls=[],
                bugfix=None,
            )


class GitLabForgeWorkTest(unittest.TestCase):
    def test_collects_normalized_work_with_configured_binding(self):
        runner = GitLabRunner()
        work = GitLabForgeWork(gitlab_config(), runner).collect()
        self.assertEqual("gitlab", work["provider"])
        self.assertEqual({"todo", "processing", "review", "blocked", "done"}, set(work["groups"]))
        self.assertEqual(1, work["groups"]["todo"][0]["number"])
        self.assertEqual("merge_request", work["changes"][0]["kind"])
        self.assertNotIn("iid", work["groups"]["todo"][0])
        self.assertNotIn("web_url", work["changes"][0])
        for args in runner.calls:
            self.assertIn("--hostname", args)
            self.assertIn("gitlab.com", args)
            self.assertIn("42", args[-1])

    def test_malformed_list_payload_degrades_without_raising(self):
        work = GitLabForgeWork(gitlab_config(), GitLabRunner(malformed=True)).collect()
        self.assertTrue(work["degraded"])
        self.assertEqual([], work["groups"]["todo"])


class GitHubRunner:
    def __init__(self, malformed=False):
        self.calls = []
        self.malformed = malformed

    def __call__(self, args, **kwargs):
        import json
        import subprocess

        self.calls.append(list(args))
        endpoint = args[-1]
        if self.malformed:
            payload = {"not": "a list"}
        elif "/pulls?" in endpoint:
            payload = [{
                "number": 7, "title": "Fix stale payment", "html_url": "https://github.com/acme/payments/pull/7",
                "state": "open", "head": {"ref": "fix/one", "sha": "a" * 40},
                "base": {"ref": "main"}, "user": {"login": "octocat"}, "body": "Closes #1",
            }]
        elif "check-runs" in endpoint:
            payload = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        else:
            payload = [
                {"number": index, "title": state, "body": "", "state": "open", "html_url": f"https://github.com/acme/payments/issues/{index}",
                 "labels": [{"name": "pitcrew-agent"}, {"name": f"pitcrew-state::{state}"}]}
                for index, state in enumerate(("todo", "processing", "review", "blocked", "done"), 1)
            ] + [{"number": 99, "pull_request": {}, "labels": []}]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")


class GitHubForgeWorkTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "providers": {"forge": "github", "tracker": "github"},
            "github": {
                "host": "github.com", "user": "octocat", "owner": "acme", "repository": "acme/payments",
                "tracker": {"labels": {"agent": "pitcrew-agent"}},
            },
        }

    def test_collects_normalized_paginated_github_work(self):
        runner = GitHubRunner()
        work = GitHubForgeWork(self.config, runner).collect()
        self.assertEqual("github", work["provider"])
        self.assertEqual(set(GitLabForgeWork(gitlab_config(), GitLabRunner()).collect()), set(work))
        self.assertEqual({"todo", "processing", "review", "blocked", "done"}, set(work["groups"]))
        self.assertEqual(1, work["groups"]["todo"][0]["number"])
        self.assertEqual("pull_request", work["changes"][0]["kind"])
        self.assertEqual("passing", work["changes"][0]["checks_status"])
        self.assertEqual(5, sum(len(group) for group in work["groups"].values()))
        for args in runner.calls:
            self.assertIn("--hostname", args)
            self.assertIn("github.com", args)

    def test_degrades_on_malformed_github_response(self):
        self.assertTrue(GitHubForgeWork(self.config, GitHubRunner(malformed=True)).collect()["degraded"])

    def test_canonical_issue_target_validates_provider_binding(self):
        self.assertEqual(
            "https://github.com/acme/payments/issues/12",
            canonical_issue_target(self.config, "https://github.com/acme/payments/issues/12"),
        )
        for value in (
            "http://github.com/acme/payments/issues/12",
            "https://github.com/other/payments/issues/12",
            "https://github.com/acme/payments/pull/12",
            "https://github.com/acme/payments/issues/0",
            "https://github.com/acme/payments/issues/12?x=1",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ForgeWorkError):
                    canonical_issue_target(self.config, value)

    def test_canonical_issue_target_normalizes_gitlab_work_items(self):
        config = gitlab_config()
        self.assertEqual(
            "https://gitlab.com/acme/payments/-/issues/12",
            canonical_issue_target(
                config,
                "https://gitlab.com/acme/payments/-/work_items/12",
            ),
        )


class ForgeWorkActionTest(unittest.TestCase):
    def setUp(self):
        self.labels = {"agent": "pitcrew-agent", "bug": "bug", "investigate": "investigate"}
        self.enabled = {"bugfixer-run", "implementer-run", "unblock", "stale-sweep"}

    def issue(self, labels, lifecycle):
        return {"labels": labels, "lifecycle": lifecycle, "canonical_url": "https://github.com/acme/payments/issues/12"}

    def test_routes_lifecycle_and_bug_labels(self):
        cases = (
            (["pitcrew-agent", "bug"], "todo", "bugfixer-run", "Corriger ce bug"),
            (["pitcrew-agent", "enhancement"], "todo", "implementer-run", "Lancer l’implémentation"),
            (["pitcrew-agent"], "blocked", "unblock", "Débloquer ce ticket"),
            (["pitcrew-agent"], "done", "stale-sweep", "Vérifier la clôture"),
        )
        for labels, lifecycle, skill, label in cases:
            with self.subTest(labels=labels, lifecycle=lifecycle):
                action = ticket_agent_action(issue=self.issue(labels, lifecycle), labels=self.labels, enabled_skills=self.enabled, active_run=None, globally_stopped=False)
                self.assertEqual(skill, action["skill"])
                self.assertEqual(label, action["label"])
                self.assertTrue(action["available"])

    def test_todo_bug_requires_agent_and_excludes_investigation(self):
        self.assertIsNone(ticket_agent_action(issue=self.issue(["bug"], "todo"), labels=self.labels, enabled_skills=self.enabled, active_run=None, globally_stopped=False))
        self.assertIsNone(ticket_agent_action(issue=self.issue(["pitcrew-agent", "bug", "investigate"], "todo"), labels=self.labels, enabled_skills=self.enabled, active_run=None, globally_stopped=False))

    def test_active_run_disables_action(self):
        action = ticket_agent_action(issue=self.issue(["pitcrew-agent", "bug"], "todo"), labels=self.labels, enabled_skills=self.enabled, active_run={"state": "queued"}, globally_stopped=False)
        self.assertFalse(action["available"])
        self.assertEqual("queued", action["run_state"])
