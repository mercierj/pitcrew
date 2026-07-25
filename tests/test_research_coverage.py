import json
import tempfile
import unittest
from pathlib import Path

from scripts.research_coverage import (
    discover_fingerprints,
    discover_areas,
    select_area,
    update_cell_state,
)


class ResearchCoverageTest(unittest.TestCase):
    def test_discover_areas_returns_sorted_relevant_tracked_style_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("src", "tests", "docs", "vendor", "build", ".cache"):
                (root / name).mkdir()
            (root / "src/app.py").write_text("pass\n", encoding="utf-8")
            (root / "tests/test_app.py").write_text("pass\n", encoding="utf-8")
            (root / "docs/README.md").write_text("docs\n", encoding="utf-8")
            (root / "vendor/lib.js").write_text("generated\n", encoding="utf-8")
            (root / "build/output.js").write_text("generated\n", encoding="utf-8")
            (root / ".gitignore").write_text("build/\n.cache/\n", encoding="utf-8")

            self.assertEqual(["docs", "src", "tests"], discover_areas(root))

    def test_discover_areas_falls_back_to_repository_root(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(["."], discover_areas(Path(directory)))

    def test_select_area_chooses_oldest_then_rolls_epoch(self):
        state = {"last_run_at": None, "last_findings_count": 0}
        areas = ["src", "tests"]

        area, coverage = select_area(state, areas, "2026-07-25T10:00:00Z")
        self.assertEqual(("src", 0), (area, coverage["epoch"]))
        state = update_cell_state(
            {"cells": {"cell": state}}, "cell", areas, area, coverage["epoch"], "2026-07-25T10:00:00Z"
        )["cells"]["cell"]

        area, coverage = select_area(state, areas, "2026-07-25T11:00:00Z")
        self.assertEqual(("tests", 0), (area, coverage["epoch"]))
        state = update_cell_state(
            {"cells": {"cell": state}}, "cell", areas, area, coverage["epoch"], "2026-07-25T11:00:00Z"
        )["cells"]["cell"]
        area, coverage = select_area(state, areas, "2026-07-25T12:00:00Z")
        self.assertEqual(("src", 1), (area, coverage["epoch"]))

    def test_select_area_migrates_legacy_and_malformed_coverage(self):
        for state in ({}, {"coverage": {"epoch": "bad", "visited": []}}):
            area, coverage = select_area(state, ["src"], "2026-07-25T10:00:00Z")
            self.assertEqual("src", area)
            self.assertEqual(0, coverage["epoch"])
            self.assertEqual({}, coverage["visited"])

    def test_update_cell_state_preserves_other_cells_and_writes_json(self):
        state = {"cells": {"repo:hygiene": {"last_run_at": None}}, "history": []}
        updated = update_cell_state(
            state,
            "repo:hygiene",
            ["src"],
            "src",
            2,
            "2026-07-25T10:00:00Z",
        )
        self.assertEqual("src", updated["cells"]["repo:hygiene"]["coverage"]["next_area"])
        self.assertEqual("2026-07-25T10:00:00Z", updated["cells"]["repo:hygiene"]["coverage"]["visited"]["src"])
        self.assertEqual([], updated["history"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            update_cell_state(
                state,
                "repo:hygiene",
                ["src"],
                "src",
                2,
                "2026-07-25T10:00:00Z",
                path=path,
            )
            self.assertIn("repo:hygiene", json.loads(path.read_text())["cells"])

    def test_git_fingerprint_change_prioritizes_changed_area(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src/app.py").write_text("one\n", encoding="utf-8")
            (root / "docs/README.md").write_text("docs\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
            areas = discover_areas(root)
            fingerprints = discover_fingerprints(root, areas)
            state = {"coverage": {"areas": areas, "epoch": 0, "visited": {area: "2026-07-25T10:00:00Z" for area in areas}, "fingerprints": fingerprints}}

            (root / "src/app.py").write_text("two\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "change"], check=True)
            changed = discover_fingerprints(root, areas)

            area, coverage = select_area(state, areas, "2026-07-25T11:00:00Z", changed)
            self.assertEqual("src", area)
            self.assertEqual(0, coverage["epoch"])


if __name__ == "__main__":
    unittest.main()
