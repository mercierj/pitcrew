import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_proposals import ProposalError, ProposalStore


ROOT = Path(__file__).resolve().parents[1]


class ProposalStoreTest(unittest.TestCase):
    def test_tracker_validation_rejects_unsafe_urls_and_malformed_ledger_tracker(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "proposals.json"
            store = ProposalStore(path)
            proposal = {
                "id": "architecture-url", "category": "architecture", "severity": "medium",
                "architecture_category": "interfaces fuyantes", "title": "Boundary",
                "summary": "Summary", "evidence": ["src/A.py:1"],
                "recommendation": "Recommendation", "status": "suggested", "source": "architecture-run",
            }
            store.append(proposal)
            store.transition("architecture-url", "approved", actor="dashboard")
            for url in ("https://user:pass@example.com/a", "https://example.com/a bad", "https://example.com\n/a", "https://example.com:99999/a", "https://[bad/a"):
                with self.subTest(url=url), self.assertRaisesRegex(ProposalError, "tracker"):
                    store.attach_tracker("architecture-url", {"iid": 1, "web_url": url})
            malformed = {**proposal, "tracker": {"iid": 1, "web_url": "https://example.com", "extra": True}}
            path.write_text(json.dumps([malformed]), encoding="utf-8")
            with self.assertRaisesRegex(ProposalError, "tracker"):
                store.list()

    def test_attach_tracker_cli_rejects_invalid_url_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "proposals.json"
            store = ProposalStore(path)
            store.append({
                "id": "architecture-cli", "category": "architecture", "severity": "medium",
                "architecture_category": "interfaces fuyantes", "title": "Boundary", "summary": "Summary",
                "evidence": ["src/A.py:1"], "recommendation": "Recommendation", "status": "suggested", "source": "architecture-run",
            })
            store.transition("architecture-cli", "approved", actor="dashboard")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/pitcrew_proposals.py"), "attach-tracker", "--ledger", str(path),
                 "--proposal-id", "architecture-cli", "--iid", "1", "--url", "https://user:pass@example.com"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertNotIn("Traceback", result.stderr)
    def test_architecture_agent_documented_payload_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = {
                "id": "getbill:boundary:abc123", "source": "architecture-run",
                "category": "architecture", "architecture_category": "interfaces fuyantes",
                "severity": "medium", "title": "Repository interface leaks ORM",
                "summary": "An application boundary exposes persistence details.",
                "evidence": ["src/Repository/InvoiceRepository.php:24"],
                "recommendation": "Return a domain-facing interface.", "status": "suggested",
            }
            proposal = ProposalStore(Path(temp) / "proposals.json").append(payload)
            self.assertEqual(payload["architecture_category"], proposal["architecture_category"])
    def test_architecture_proposal_keeps_metadata_and_attaches_tracker_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProposalStore(Path(temp) / "proposals.json")
            store.append({
                "id": "architecture-1", "category": "architecture", "severity": "medium",
                "architecture_category": "couplage framework/persistence", "title": "Controller reaches persistence",
                "summary": "A controller calls the repository directly.",
                "where": ["src/Controller/InvoiceController.php:42"],
                "evidence": ["src/Controller/InvoiceController.php:42"],
                "recommendation": "Move persistence orchestration behind an application service.",
                "status": "suggested", "source": "architecture-run",
            })
            store.transition("architecture-1", "approved", actor="dashboard")
            tracker = {"iid": 42, "web_url": "https://gitlab.com/acme/app/-/issues/42"}
            attached = store.attach_tracker("architecture-1", tracker)
            self.assertEqual(tracker, attached["tracker"])
            self.assertEqual(tracker, store.attach_tracker("architecture-1", tracker)["tracker"])
            with self.assertRaisesRegex(ProposalError, "different"):
                store.attach_tracker("architecture-1", {"iid": 43, "web_url": "https://gitlab.com/acme/app/-/issues/43"})

    def test_tracker_attachment_rejects_non_architecture_or_invalid_tracker(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProposalStore(Path(temp) / "proposals.json")
            feature = {
                "id": "feature-attach", "category": "feature", "severity": "low",
                "title": "Feature", "summary": "Summary", "evidence": ["a:1"],
                "recommendation": "Recommendation", "status": "suggested", "source": "test",
            }
            store.append(feature)
            store.transition("feature-attach", "approved", actor="dashboard")
            with self.assertRaisesRegex(ProposalError, "architecture"):
                store.attach_tracker("feature-attach", {"iid": 1, "web_url": "https://gitlab.com/a"})
    def test_append_lists_and_approves_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProposalStore(Path(temp) / "proposals.json")
            proposal = store.append({
                "id": "feature-1",
                "category": "feature",
                "severity": "medium",
                "title": "Ajouter un résumé de paiement",
                "summary": "Les utilisateurs cherchent le total avant le détail.",
                "evidence": ["templates/payment/show.html.twig:42"],
                "recommendation": "Afficher le total en tête de page.",
                "status": "suggested",
                "source": "product-discovery-run",
            })
            self.assertEqual("suggested", proposal["status"])
            approved = store.transition("feature-1", "approved", actor="dashboard")
            self.assertEqual("approved", approved["status"])
            self.assertEqual("dashboard", approved["decision"]["actor"])
            self.assertEqual("approved", store.list()[0]["status"])

    def test_rejection_requires_reason_and_deduplicates_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProposalStore(Path(temp) / "proposals.json")
            value = {
                "id": "security-1", "category": "security", "severity": "high",
                "title": "Missing access check", "summary": "A tenant boundary is missing.",
                "evidence": ["src/Controller/InvoiceController.php:10"],
                "recommendation": "Enforce tenant ownership.", "status": "suggested",
                "source": "security-run",
            }
            store.append(value)
            with self.assertRaisesRegex(ProposalError, "reason"):
                store.transition("security-1", "dismissed", actor="dashboard")
            with self.assertRaisesRegex(ProposalError, "duplicate"):
                store.append(value)
            dismissed = store.transition("security-1", "dismissed", actor="dashboard", reason="Already fixed")
            self.assertEqual("Already fixed", dismissed["decision"]["reason"])

    def test_corrupt_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "proposals.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ProposalError, "ledger"):
                ProposalStore(path).list()


if __name__ == "__main__":
    unittest.main()
