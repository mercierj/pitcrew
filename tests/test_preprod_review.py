import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


class PreprodReviewRefTest(unittest.TestCase):
    def test_validate_remote_ref_accepts_origin_branch_names(self):
        from scripts.pitcrew_preprod_review import validate_remote_ref

        self.assertEqual("origin/preprod", validate_remote_ref("origin/preprod"))
        self.assertEqual("origin/feature/release-1", validate_remote_ref("origin/feature/release-1"))

    def test_validate_remote_ref_rejects_unsafe_or_non_origin_names(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, validate_remote_ref

        invalid = (None, "", "preprod", "upstream/preprod", "origin/../preprod", "origin/a//b",
                   "origin/.hidden", "origin/a.lock", "origin/a@{1}", "origin/a:b", "origin/a b",
                   "origin/a\\b", "origin/a..b", "origin/a/", "origin/--upload-pack=evil", "origin/a/-bad")
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PreprodReviewError):
                    validate_remote_ref(value)


class PreprodReviewManifestTest(unittest.TestCase):
    def _git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def test_build_manifest_fetches_and_covers_delta_types(self):
        from scripts.pitcrew_preprod_review import build_manifest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, remote, clone = root / "source", root / "remote.git", root / "clone"
            source.mkdir()
            self._git(source, "init", "-b", "preprod")
            self._git(source, "config", "user.email", "test@example.test")
            self._git(source, "config", "user.name", "Test User")
            (source / "old.txt").write_text("old\n", encoding="utf-8")
            (source / "delete.txt").write_text("remove\n", encoding="utf-8")
            (source / "asset.bin").write_bytes(b"\x00\x01\xff")
            (source / "copy-source.txt").write_text("copyable line\n" * 100, encoding="utf-8")
            (source / "typechange.txt").write_text("regular file\n", encoding="utf-8")
            self._git(source, "add", ".")
            self._git(source, "commit", "-m", "base\tsubject")
            self._git(root, "init", "--bare", str(remote))
            self._git(source, "remote", "add", "origin", str(remote))
            self._git(source, "push", "-u", "origin", "preprod")
            self._git(source, "checkout", "-b", "develop")
            self._git(source, "mv", "old.txt", "renamed.txt")
            self._git(source, "rm", "delete.txt")
            (source / "added.txt").write_text("new\n", encoding="utf-8")
            (source / "build").mkdir()
            (source / "build/output.js").write_text("generated\n", encoding="utf-8")
            (source / "asset.bin").write_bytes(b"\x00\x02\xfe")
            (source / "copy-target.txt").write_text((source / "copy-source.txt").read_text(), encoding="utf-8")
            (source / "copy-source.txt").write_text("changed header\n" + (source / "copy-source.txt").read_text(), encoding="utf-8")
            (source / "typechange.txt").unlink()
            (source / "typechange.txt").symlink_to("added.txt")
            self._git(source, "add", ".")
            self._git(source, "commit", "-m", "développe\tsubject")
            self._git(source, "push", "-u", "origin", "develop")
            self._git(root, "clone", str(remote), str(clone))
            (source / "fresh-after-clone.txt").write_text("must be fetched\n", encoding="utf-8")
            self._git(source, "add", ".")
            self._git(source, "commit", "-m", "fresh remote commit")
            self._git(source, "push", "origin", "develop")

            manifest = build_manifest(clone, "origin/preprod", "origin/develop")

        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("origin/preprod", manifest["base_ref"])
        self.assertEqual("origin/develop", manifest["compare_ref"])
        self.assertEqual(2, manifest["commit_count"])
        self.assertEqual(manifest["changed_file_count"], len(manifest["files"]))
        files = {item["path"]: item for item in manifest["files"]}
        self.assertEqual("D", files["delete.txt"]["status"])
        self.assertTrue(files["asset.bin"]["binary"])
        self.assertTrue(files["build/output.js"]["generated"])
        self.assertEqual("T", files["typechange.txt"]["status"])
        self.assertFalse(files["typechange.txt"]["binary"])
        self.assertFalse(files["typechange.txt"]["generated"])
        self.assertFalse(files["typechange.txt"]["reviewed"])
        self.assertEqual("C100", files["copy-target.txt"]["status"])
        self.assertEqual("copy-source.txt", files["copy-target.txt"]["old_path"])
        self.assertIn("fresh-after-clone.txt", files)
        renamed = files["renamed.txt"]
        self.assertEqual("R100", renamed["status"])
        self.assertEqual("old.txt", renamed["old_path"])
        self.assertTrue(all(item["reviewed"] is False for item in manifest["files"]))
        self.assertRegex(manifest["base_sha"], r"^[0-9a-f]{40}$")

    def test_build_manifest_rejects_same_refs_and_controlled_runner_errors(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, _parse_commits, _parse_name_status, build_manifest

        with self.assertRaises(PreprodReviewError):
            build_manifest(".", "origin/preprod", "origin/preprod")
        runner = Mock(side_effect=OSError("private path detail"))
        with self.assertRaisesRegex(PreprodReviewError, "git command failed"):
            build_manifest(".", "origin/preprod", "origin/develop", runner=runner)
        with self.assertRaises(PreprodReviewError):
            _parse_commits(b"not-a-sha\x00")
        with self.assertRaises(PreprodReviewError):
            _parse_name_status(b"R100\x00only-one-path\x00")


class PreprodReviewReportTest(unittest.TestCase):
    def _manifest(self, paths=("a.py", "b.py")):
        return {
            "schema_version": 1, "base_ref": "origin/preprod", "compare_ref": "origin/develop",
            "prepared_at": "2026-07-26T00:00:00Z",
            "base_sha": "a" * 40, "compare_sha": "b" * 40, "merge_base_sha": "c" * 40,
            "commit_count": 1, "changed_file_count": len(paths),
            "commits": [{"sha": "d" * 40, "authored_at": "2026-07-26T00:00:00Z", "subject": "Test"}],
            "files": [{"status": "M", "path": path, "binary": False, "generated": False, "reviewed": False} for path in paths],
        }

    def _result(self, severity="medium"):
        return {"reviewed_files": ["a.py", "b.py"], "synthesis": "Checked changes.", "findings": [{
            "severity": severity, "title": "Issue", "evidence": ["proof"], "affected_files": ["a.py"],
            "impact": "Impact", "recommendation": "Fix it",
        }]}

    def test_finalize_ready_medium_and_high_changes_required(self):
        from scripts.pitcrew_preprod_review import finalize_report
        ready = finalize_report(self._manifest(), self._result(), "gpt-5.6-sol", "xhigh")
        self.assertEqual("ready", ready["verdict"])
        blocked = finalize_report(self._manifest(), self._result("high"), "gpt-5.6-sol", "xhigh")
        self.assertEqual("changes_required", blocked["verdict"])
        self.assertTrue(all(path in {"a.py", "b.py"} for path in blocked["reviewed_files"]))

    def test_finalize_fails_closed_for_model_and_coverage(self):
        from scripts.pitcrew_preprod_review import finalize_report
        report = finalize_report(self._manifest(), self._result(), "other", "xhigh")
        self.assertEqual("incomplete", report["verdict"])
        self.assertEqual([], report["findings"])
        invalid = self._result()
        invalid["reviewed_files"] = ["a.py", "a.py"]
        self.assertEqual("incomplete", finalize_report(self._manifest(), invalid, "gpt-5.6-sol", "xhigh")["verdict"])

    def test_no_delta_is_normalized(self):
        from scripts.pitcrew_preprod_review import finalize_report
        manifest = self._manifest(())
        manifest["commit_count"] = 0
        manifest["commits"] = []
        report = finalize_report(manifest, {"reviewed_files": [], "findings": [], "synthesis": ""}, "gpt-5.6-sol", "xhigh")
        self.assertEqual("ready", report["verdict"])
        self.assertEqual("No changes between configured refs.", report["synthesis"])

    def test_report_store_replaces_and_trims(self):
        from scripts.pitcrew_preprod_review import ReportStore, finalize_report
        with tempfile.TemporaryDirectory() as directory:
            store = ReportStore(Path(directory) / "private" / "reports.json", 2)
            first = finalize_report(self._manifest(), self._result(), "gpt-5.6-sol", "xhigh")
            store.save(first)
            store.save(first)
            self.assertEqual(1, len(store.read()["reports"]))
            for sha in ("d", "e"):
                manifest = self._manifest()
                manifest["compare_sha"] = sha * 40
                store.save(finalize_report(manifest, self._result(), "gpt-5.6-sol", "xhigh"))
            self.assertEqual(2, len(store.read()["reports"]))

    def test_write_manifest_refuses_corrupt_existing_file_without_overwrite(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, write_manifest
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            target.write_bytes(b"\xffnot-json")
            with self.assertRaises(PreprodReviewError):
                write_manifest(self._manifest(), target)
            self.assertEqual(b"\xffnot-json", target.read_bytes())

    def test_write_manifest_refuses_existing_json_null_without_overwrite(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, write_manifest
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            target.write_text("null", encoding="utf-8")
            with self.assertRaises(PreprodReviewError):
                write_manifest(self._manifest(), target)
            self.assertEqual("null", target.read_text(encoding="utf-8"))

    def test_safe_read_refuses_symlink_and_report_store_truncated_state(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, ReportStore, _read_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external.json"
            external.write_text('{"secret":true}', encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(external)
            with self.assertRaises(PreprodReviewError):
                _read_json(link)
            store_path = root / "store.json"
            store_path.write_text('{"schema_version":1,"reports":[{}]}', encoding="utf-8")
            with self.assertRaises(PreprodReviewError):
                ReportStore(store_path).read()
            self.assertIn('"reports":[{}]', store_path.read_text(encoding="utf-8"))

    def test_report_store_keys_by_resolved_shas_and_bounds_history(self):
        from scripts.pitcrew_preprod_review import ReportStore, _failure_report, finalize_report
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            store = ReportStore(path, 2)
            first = finalize_report(self._manifest(), self._result(), "gpt-5.6-sol", "xhigh")
            alias = dict(first, base_ref="origin/release", compare_ref="origin/candidate")
            store.save(first)
            store.save(alias)
            self.assertEqual(1, len(store.read()["reports"]))
            store.save(_failure_report("origin/preprod", "origin/develop", "failed"))
            store.save(_failure_report("origin/release", "origin/candidate", "failed"))
            self.assertEqual(2, len(store.read()["reports"]))
            path.write_text('{"schema_version":1,"reports":[' + ','.join(['{}'] * 3) + ']}', encoding="utf-8")
            with self.assertRaises(Exception):
                store.read()

    def test_finalize_rejects_over_limit_findings(self):
        from scripts.pitcrew_preprod_review import finalize_report
        result = self._result()
        result["findings"] *= 201
        report = finalize_report(self._manifest(), result, "gpt-5.6-sol", "xhigh")
        self.assertEqual("incomplete", report["verdict"])

    def test_store_rejects_truncated_or_oversized_incomplete_report(self):
        from scripts.pitcrew_preprod_review import ReportStore, finalize_report
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            result = self._result()
            result["reviewed_files"] = []
            incomplete = finalize_report(self._manifest(), result, "gpt-5.6-sol", "xhigh")
            incomplete["files"] = [{"path": "a.py", "reviewed": False}]
            path.write_text(json.dumps({"schema_version": 1, "reports": [incomplete]}), encoding="utf-8")
            with self.assertRaises(Exception):
                ReportStore(path).read()
            incomplete = finalize_report(self._manifest(), result, "gpt-5.6-sol", "xhigh")
            incomplete["changed_file_count"] = 10001
            path.write_text(json.dumps({"schema_version": 1, "reports": [incomplete]}), encoding="utf-8")
            with self.assertRaises(Exception):
                ReportStore(path).read()

    def test_store_rejects_persisted_complete_report_over_finding_limit(self):
        from scripts.pitcrew_preprod_review import ReportStore, finalize_report
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            complete = finalize_report(self._manifest(), self._result(), "gpt-5.6-sol", "xhigh")
            complete["findings"] *= 201
            path.write_text(json.dumps({"schema_version": 1, "reports": [complete]}), encoding="utf-8")
            with self.assertRaises(Exception):
                ReportStore(path).read()

    def test_failure_report_and_persisted_incomplete_require_distinct_refs(self):
        from scripts.pitcrew_preprod_review import PreprodReviewError, ReportStore, _failure_report, main
        import json
        with self.assertRaises(PreprodReviewError):
            _failure_report("origin/preprod", "origin/preprod", "failed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            self.assertEqual(2, main(["fail", "--base-ref", "origin/preprod", "--compare-ref", "origin/preprod", "--reason", "failed", "--store", str(path)]))
            self.assertFalse(path.exists())
            report = _failure_report("origin/preprod", "origin/develop", "failed")
            report["compare_ref"] = "origin/preprod"
            path.write_text(json.dumps({"schema_version": 1, "reports": [report]}), encoding="utf-8")
            with self.assertRaises(PreprodReviewError):
                ReportStore(path).read()


if __name__ == "__main__":
    unittest.main()
