import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.pitcrew_history import HistoryStore, classify_record


def _record(skill: str, finished_at: str, **overrides) -> dict:
    record = {
        "project": "getbill",
        "skill": skill,
        "started_at": finished_at,
        "finished_at": finished_at,
        "outcome": "success",
        "summary": "Completed.",
    }
    record.update(overrides)
    return record


def _append_in_process(path: str, record: dict, now: str) -> None:
    HistoryStore(Path(path)).append(record, now=now)


class HistoryStoreTest(unittest.TestCase):
    def test_history_retains_only_records_from_last_seven_days(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl", retention_days=7)
            store.append(
                _record("old", "2026-07-17T11:59:59Z"),
                now="2026-07-24T12:00:00Z",
            )
            store.append(
                _record("boundary", "2026-07-17T12:00:00Z"),
                now="2026-07-24T12:00:00Z",
            )
            store.append(
                _record("recent", "2026-07-24T08:00:03Z"),
                now="2026-07-24T12:00:00Z",
            )

            records = store.read(now="2026-07-24T12:00:00Z")

            self.assertEqual(["recent", "boundary"], [record["skill"] for record in records])

    def test_read_persists_retention_pruning_to_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.jsonl"
            store = HistoryStore(path, retention_days=7)
            store.append(
                _record("expired", "2026-07-17T12:00:00Z"),
                now="2026-07-17T12:00:00Z",
            )

            self.assertEqual([], store.read(now="2026-07-25T12:00:00Z"))
            self.assertEqual("", path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_read_rewrites_only_when_a_valid_record_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl", retention_days=7)
            store.append(
                _record("recent", "2026-07-24T12:00:00Z"),
                now="2026-07-24T12:00:00Z",
            )

            with mock.patch.object(store, "_replace", wraps=store._replace) as replace:
                store.read(now="2026-07-24T12:00:00Z")
                replace.assert_not_called()

                store.read(now="2026-08-01T12:00:00Z")
                replace.assert_called_once_with([])

    def test_malformed_and_invalid_jsonl_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.jsonl"
            valid = _record("valid", "2026-07-24T08:00:03Z")
            invalid = {**valid, "outcome": "maybe"}
            path.write_text(
                "\n".join(("{not-json", json.dumps(invalid), json.dumps(valid))) + "\n",
                encoding="utf-8",
            )

            records = HistoryStore(path).read(now="2026-07-24T12:00:00Z")

            self.assertEqual([valid], records)

    def test_two_processes_append_two_valid_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.jsonl"
            now = "2026-07-24T12:00:00Z"
            processes = [
                multiprocessing.Process(
                    target=_append_in_process,
                    args=(str(path), _record(skill, now), now),
                )
                for skill in ("research-run", "security-run")
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)

            self.assertEqual([0, 0], [process.exitcode for process in processes])
            records = HistoryStore(path).read(now=now)
            self.assertEqual(
                {"research-run", "security-run"},
                {record["skill"] for record in records},
            )
            self.assertEqual(2, len(records))

    def test_read_filters_by_skill_and_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            now = "2026-07-24T12:00:00Z"
            store.append(_record("research-run", now), now=now)
            store.append(
                _record("research-run", now, outcome="noop", summary="no eligible item"),
                now=now,
            )
            store.append(_record("security-run", now), now=now)

            records = store.read(now=now, skill="research-run", outcome="noop")

            self.assertEqual(1, len(records))
            self.assertEqual("noop", records[0]["outcome"])

    def test_success_and_expected_noop_are_healthy(self):
        self.assertEqual("healthy", classify_record({"outcome": "success", "summary": ""}))
        self.assertEqual(
            "healthy",
            classify_record({"outcome": "noop", "summary": '{"reason":"no eligible item"}'}),
        )

    def test_structured_noop_refines_successful_process_outcome(self):
        authentication_noop = {
            "outcome": "success",
            "exit_code": 0,
            "summary": json.dumps(
                {
                    "status": "noop",
                    "reason": "configured GitLab provider authentication failed",
                }
            ),
        }
        expected_noop = {
            "outcome": "success",
            "exit_code": 0,
            "summary": json.dumps(
                {"status": "noop", "reason": "no eligible item"}
            ),
        }

        self.assertEqual("warning", classify_record(authentication_noop))
        self.assertEqual("healthy", classify_record(expected_noop))

    def test_plain_non_json_success_remains_healthy(self):
        self.assertEqual(
            "healthy",
            classify_record(
                {"outcome": "success", "exit_code": 0, "summary": "Completed."}
            ),
        )

    def test_structured_failures_are_failed_after_successful_process_exit(self):
        for status in ("failed", "interrupted"):
            with self.subTest(status=status):
                self.assertEqual(
                    "failed",
                    classify_record(
                        {
                            "outcome": "success",
                            "exit_code": 0,
                            "summary": json.dumps({"status": status}),
                        }
                    ),
                )

    def test_unrecognized_structured_status_falls_back_to_record_outcome(self):
        for status in ("unknown", ["noop"]):
            with self.subTest(status=status):
                self.assertEqual(
                    "healthy",
                    classify_record(
                        {
                            "outcome": "success",
                            "exit_code": 0,
                            "summary": json.dumps({"status": status}),
                        }
                    ),
                )

    def test_actionable_noops_are_warnings(self):
        reasons = (
            "required file unavailable",
            "missing configuration",
            "provider unavailable",
            "authentication required",
            "permission denied",
            "provider check failed",
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertEqual(
                    "warning",
                    classify_record(
                        {"outcome": "noop", "summary": json.dumps({"reason": reason})}
                    ),
                )

    def test_nonzero_exit_and_failed_outcome_are_failed(self):
        self.assertEqual(
            "failed",
            classify_record({"outcome": "success", "exit_code": 2, "summary": ""}),
        )
        self.assertEqual(
            "failed",
            classify_record({"outcome": "failed", "exit_code": 0, "summary": ""}),
        )
        self.assertEqual(
            "failed",
            classify_record(
                {
                    "outcome": "success",
                    "exit_code": 2,
                    "summary": '{"status":"success"}',
                }
            ),
        )

    def test_none_is_unknown(self):
        self.assertEqual("unknown", classify_record(None))


if __name__ == "__main__":
    unittest.main()
