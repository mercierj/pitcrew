import unittest

from scripts.pitcrew_forge_work import (
    ForgeWorkError,
    normalize_change,
    normalize_issue,
)


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
