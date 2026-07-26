import unittest

from scripts.pitcrew_forge_work import (
    ForgeWorkError,
    GitLabForgeWork,
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
