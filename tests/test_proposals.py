import json
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_proposals import ProposalError, ProposalStore


class ProposalStoreTest(unittest.TestCase):
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
