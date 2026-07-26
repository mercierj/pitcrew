import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from scripts.research_coverage import (
    discover_fingerprints,
    discover_areas,
    select_area,
    update_cell_state,
)


class ResearchCoverageTest(unittest.TestCase):
    def test_discover_areas_returns_sorted_relevant_tracked_style_directories(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
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
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
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
        state = {
            "cells": {
                "repo:hygiene": {"last_run_at": None},
                "repo:other": {"coverage": {"areas": ["docs"], "epoch": 3, "visited": {"docs": "old"}, "fingerprints": {"docs": "fingerprint"}}},
            },
            "history": [],
        }
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
        self.assertEqual("fingerprint", updated["cells"]["repo:other"]["coverage"]["fingerprints"]["docs"])

        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
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

    def test_update_cell_state_writes_private_state_and_parent(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "private" / "state.json"
            update_cell_state(
                {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                "2026-07-25T10:00:00Z", path=path,
            )
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_update_cell_state_sets_private_mode_on_temporary_descriptor(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "state.json"
            with patch("scripts.research_coverage.os.fchmod") as fchmod:
                update_cell_state(
                    {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                    "2026-07-25T10:00:00Z", path=path,
                )
            fchmod.assert_any_call(ANY, 0o600)

    def test_update_cell_state_restricts_existing_parent_permissions(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            parent = Path(directory) / "state"
            parent.mkdir(mode=0o755)
            os.chmod(parent, 0o755)
            update_cell_state(
                {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                "2026-07-25T10:00:00Z", path=parent / "state.json",
            )
            self.assertEqual(0o700, stat.S_IMODE(parent.stat().st_mode))

    def test_update_cell_state_allows_unsupported_directory_fsync(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "state.json"
            unsupported = OSError(errno.EINVAL, "directory fsync unsupported")
            with patch("scripts.research_coverage.os.fsync", side_effect=[None, unsupported]):
                update_cell_state(
                    {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                    "2026-07-25T10:00:00Z", path=path,
                )
            self.assertTrue(path.exists())

    def test_update_cell_state_propagates_directory_fsync_io_error(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            path = Path(directory) / "state.json"
            disk_error = OSError(errno.EIO, "directory fsync failed")
            with patch("scripts.research_coverage.os.fsync", side_effect=[None, disk_error]):
                with self.assertRaises(OSError) as raised:
                    update_cell_state(
                        {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                        "2026-07-25T10:00:00Z", path=path,
                    )
            self.assertEqual(errno.EIO, raised.exception.errno)

    def test_update_cell_state_refuses_symlinked_state(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text('{"preserve": true}\n', encoding="utf-8")
            state_path = root / "state.json"
            try:
                state_path.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            with self.assertRaises(OSError):
                update_cell_state(
                    {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                    "2026-07-25T10:00:00Z", path=state_path,
                )
            self.assertTrue(state_path.is_symlink())
            self.assertEqual('{"preserve": true}\n', target.read_text(encoding="utf-8"))

    def test_update_cell_state_refuses_symlinked_ancestor_without_creating_target_files(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            link = root / "link"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            with self.assertRaises(OSError):
                update_cell_state(
                    {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                    "2026-07-25T10:00:00Z", path=link / "nested" / "state.json",
                )
            self.assertFalse((external / "nested").exists())

    def test_update_cell_state_rejects_parent_swapped_to_symlink_during_write(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            nested = root / "state" / "nested"
            nested.mkdir(parents=True)
            external = root / "external"
            external.mkdir()
            original_mkdir = os.mkdir
            swapped = False

            def swap_parent(name, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                try:
                    return original_mkdir(name, mode, dir_fd=dir_fd)
                except FileExistsError:
                    if not swapped and Path(name).name == "nested":
                        nested.rename(root / "held")
                        nested.symlink_to(external, target_is_directory=True)
                        swapped = True
                    raise

            with patch("scripts.research_coverage.os.mkdir", side_effect=swap_parent):
                with self.assertRaises(OSError):
                    update_cell_state(
                        {"cells": {}, "history": []}, "repo:hygiene", ["src"], "src", 0,
                        "2026-07-25T10:00:00Z", path=nested / "state.json",
                    )
            self.assertTrue(swapped)
            self.assertFalse((external / "state.json").exists())

    def test_select_and_record_reject_malformed_state_without_overwriting_it(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            state_path = root / "state.json"
            malformed = "{not valid json\n"
            state_path.write_text(malformed, encoding="utf-8")
            script = Path(__file__).parents[1] / "scripts" / "research_coverage.py"

            for command in ("select", "record"):
                args = [sys.executable, str(script), command, str(state_path), "repo:test", str(root)]
                if command == "record":
                    args.extend([".", "0"])
                result = subprocess.run(args, capture_output=True, text=True)
                self.assertNotEqual(0, result.returncode, msg=command)
                self.assertIn("invalid JSON state", result.stderr, msg=command)
                self.assertEqual(malformed, state_path.read_text(encoding="utf-8"), msg=command)

    def test_select_rejects_non_utf8_state_without_traceback_or_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            state_path = root / "state.json"
            invalid_bytes = b'{"invalid": "\xff"}'
            state_path.write_bytes(invalid_bytes)
            script = Path(__file__).parents[1] / "scripts" / "research_coverage.py"

            result = subprocess.run(
                [sys.executable, str(script), "select", str(state_path), "repo:test", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid state encoding", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(invalid_bytes, state_path.read_bytes())

    def test_git_fingerprint_change_prioritizes_changed_area(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
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
