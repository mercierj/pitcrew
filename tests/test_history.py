import json
import fcntl
import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.pitcrew_history import HistoryStore, append_gate_record, classify_record

SCRIPTS_PATH = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, SCRIPTS_PATH)
try:
    import pitcrew_locked_exec
finally:
    sys.path.remove(SCRIPTS_PATH)


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
    def test_append_gate_record_marks_model_not_invoked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.jsonl"

            append_gate_record(
                path,
                project="getbill",
                skill="reviewer-run",
                decision="empty",
                reason="no authored open merge request requires review",
                outcome="noop",
                target_id=None,
                fingerprint="sha256:abc",
            )

            record = HistoryStore(path).read()[0]
            self.assertEqual("noop", record["outcome"])
            self.assertFalse(record["model_invoked"])
            self.assertEqual("empty", record["gate_decision"])
            self.assertEqual("sha256:abc", record["fingerprint"])
            self.assertNotIn("model", record)
            self.assertNotIn("usage", record)

    def test_append_gate_record_rejects_model_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "pre-model outcome"):
                append_gate_record(
                    Path(temp) / "history.jsonl",
                    project="getbill",
                    skill="reviewer-run",
                    decision="empty",
                    reason="no eligible item",
                    outcome="success",
                    target_id=None,
                    fingerprint=None,
                )

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

    def test_usage_total_survives_detail_history_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl", retention_days=7)
            store.append(
                _record(
                    "research-run",
                    "2026-07-24T12:00:00Z",
                    model="gpt-5.6-luna",
                    usage={
                        "input_tokens": 10,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 10,
                    },
                ),
                now="2026-07-24T12:00:00Z",
            )

            self.assertEqual(1, store.usage_total(now="2026-07-24T12:00:00Z")["measured_runs"])
            self.assertEqual([], store.read(now="2026-08-01T12:00:01Z"))
            total = store.usage_total(now="2026-08-01T12:00:01Z")

            self.assertEqual(1, total["measured_runs"])
            self.assertEqual(10, total["tokens"]["input_tokens"])

    def test_usage_total_recovers_from_non_finite_sidecar_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            store.usage_total_path.write_text(
                json.dumps({
                    "measured_runs": 0,
                    "unmeasured_runs": 0,
                    "tokens": {
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                    "estimated_cost_usd": "Infinity",
                }),
                encoding="utf-8",
            )

            total = store.usage_total(now="2026-07-24T12:00:00Z")

            self.assertEqual("0.000000", total["estimated_cost_usd"])

    def test_failed_history_write_rolls_back_usage_total(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            record = _record(
                "research-run",
                "2026-07-24T12:00:00Z",
                model="gpt-5.6-luna",
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 10,
                },
            )
            with mock.patch.object(store, "_replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    store.append(record, now="2026-07-24T12:00:00Z")

            store.append(record, now="2026-07-24T12:00:00Z")

            self.assertEqual(1, store.usage_total(now="2026-07-24T12:00:00Z")["measured_runs"])

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

    def test_history_round_trips_valid_model_and_usage_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            now = "2026-07-24T12:00:00Z"
            record = _record(
                "research-run",
                now,
                model="gpt-5.6-terra",
                usage={
                    "input_tokens": 120,
                    "cached_input_tokens": 40,
                    "cache_write_tokens": 10,
                    "output_tokens": 30,
                    "total_tokens": 200,
                },
            )

            store.append(record, now=now)

            self.assertEqual(record, store.read(now=now)[0])

    def test_history_round_trips_valid_invocation_metadata_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            now = "2026-07-24T12:00:00Z"
            record = _record(
                "research-run",
                now,
                routing_reason="baseline retained during observation",
                target_id="ISSUE-42",
                work_kind="research",
                quality_outcome="validated",
                gate_decision="directed",
                gate_reason="human supplied directed target",
                fingerprint="fp-123",
                model_invoked=True,
                did_work=False,
                reasoning_effort="xhigh",
                routing_mode="observe",
                candidate_model="gpt-5.6-sol",
            )
            original = dict(record)

            store.append(record, now=now)

            self.assertEqual(original, record)
            self.assertEqual(original, store.read(now=now)[0])

    def test_history_drops_invalid_optional_invocation_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            now = "2026-07-24T12:00:00Z"
            record = _record(
                "research-run",
                now,
                routing_reason="",
                target_id=42,
                work_kind=None,
                quality_outcome=[],
                gate_decision=False,
                gate_reason="",
                fingerprint={},
                model_invoked=1,
                did_work="false",
                reasoning_effort="ultra",
                routing_mode="dynamic",
                candidate_model="invented",
            )

            store.append(record, now=now)

            stored = store.read(now=now)[0]
            for field in (
                "routing_reason",
                "target_id",
                "work_kind",
                "quality_outcome",
                "gate_decision",
                "gate_reason",
                "fingerprint",
                "model_invoked",
                "did_work",
                "reasoning_effort",
                "routing_mode",
                "candidate_model",
            ):
                with self.subTest(field=field):
                    self.assertNotIn(field, stored)

    def test_history_keeps_legacy_records_but_removes_unknown_model_and_bad_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl")
            now = "2026-07-24T12:00:00Z"
            cases = (
                _record("legacy", now),
                _record(
                    "unknown-model",
                    now,
                    model="invented",
                    usage={
                        "input_tokens": 120,
                        "cached_input_tokens": 40,
                        "cache_write_tokens": 10,
                        "output_tokens": 30,
                        "total_tokens": 200,
                    },
                ),
                _record("negative", now, usage={"input_tokens": -1}),
                _record("non-int", now, usage={"input_tokens": "1"}),
                _record("bool", now, usage={"input_tokens": True}),
                _record("incomplete", now, usage={"input_tokens": 1}),
                _record(
                    "malformed",
                    now,
                    model="gpt-5.6-terra",
                    usage="not-an-object",
                ),
            )
            for record in cases:
                store.append(record, now=now)

            by_skill = {record["skill"]: record for record in store.read(now=now)}
            self.assertNotIn("model", by_skill["legacy"])
            for skill in ("unknown-model", "negative", "non-int", "bool", "incomplete", "malformed"):
                self.assertNotIn("usage", by_skill[skill])
            self.assertNotIn("model", by_skill["unknown-model"])
            self.assertNotIn("usage", by_skill["unknown-model"])
            self.assertEqual("gpt-5.6-terra", by_skill["malformed"]["model"])

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

    def test_configured_provider_access_failures_are_warnings(self):
        reasons = (
            "configured tracker not available",
            "configured forge unauth'd",
            "configured provider unavailable",
            "configured provider unauthenticated",
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertEqual(
                    "warning",
                    classify_record(
                        {
                            "outcome": "success",
                            "exit_code": 0,
                            "summary": json.dumps(
                                {"status": "noop", "reason": reason}
                            ),
                        }
                    ),
                )

    def test_free_form_configured_provider_failures_are_warnings(self):
        reasons = (
            "the required configured tracker operation is unavailable",
            "configured forge or git capability unavailable",
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertEqual(
                    "warning",
                    classify_record(
                        {
                            "outcome": "success",
                            "exit_code": 0,
                            "summary": json.dumps(
                                {"status": "noop", "reason": reason}
                            ),
                        }
                    ),
                )

    def test_leading_structured_noop_with_trailing_prose_is_classified(self):
        cases = (
            (
                "warning",
                "configured GitLab provider authentication failed",
                "No GitLab operations were made.",
            ),
            (
                "warning",
                "missing configuration",
                "No provider operations were made.",
            ),
            (
                "healthy",
                "no eligible item",
                "No repository operations were made.",
            ),
        )
        for expected, reason, trailing_prose in cases:
            summary = (
                "\n  "
                + json.dumps({"status": "noop", "reason": reason}, indent=2)
                + f"\n\n{trailing_prose}"
            )
            with self.subTest(reason=reason):
                self.assertEqual(
                    expected,
                    classify_record(
                        {
                            "outcome": "success",
                            "exit_code": 0,
                            "summary": summary,
                        }
                    ),
                )

    def test_structured_json_buried_after_plain_text_is_not_parsed(self):
        self.assertEqual(
            "healthy",
            classify_record(
                {
                    "outcome": "success",
                    "exit_code": 0,
                    "summary": 'Completed.\n{"status":"failed"}',
                }
            ),
        )

    def test_benign_noop_phrases_are_healthy(self):
        records = (
            {
                "outcome": "success",
                "exit_code": 0,
                "summary": json.dumps(
                    {
                        "status": "noop",
                        "reason": "no eligible item; no approval required",
                    }
                ),
            },
            {
                "outcome": "noop",
                "exit_code": 0,
                "summary": json.dumps({"reason": "nothing missing"}),
            },
            {
                "outcome": "success",
                "exit_code": 0,
                "summary": json.dumps(
                    {
                        "status": "noop",
                        "reason": (
                            "provider check completed; "
                            "no permission changes required"
                        ),
                    }
                ),
            },
        )

        for record in records:
            with self.subTest(summary=record["summary"]):
                self.assertEqual("healthy", classify_record(record))

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


class LockedExecHistoryTest(unittest.TestCase):
    def test_normalized_outcome_uses_structured_status_after_successful_exit(self):
        for status, expected in (
            ("success", "success"),
            ("noop", "noop"),
            ("blocked", "noop"),
            ("failed", "failed"),
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    expected,
                    pitcrew_locked_exec.normalized_outcome(
                        0, {"status": status}, required=True
                    ),
                )

    def test_normalized_outcome_preserves_process_precedence_and_legacy_fallback(self):
        self.assertEqual(
            "interrupted",
            pitcrew_locked_exec.normalized_outcome(
                -15, {"status": "success"}, required=True
            ),
        )
        self.assertEqual(
            "failed",
            pitcrew_locked_exec.normalized_outcome(
                7, {"status": "success"}, required=True
            ),
        )
        self.assertEqual(
            "failed",
            pitcrew_locked_exec.normalized_outcome(0, None, required=True),
        )
        self.assertEqual(
            "success",
            pitcrew_locked_exec.normalized_outcome(0, None, required=False),
        )

    def test_locked_helper_copies_structured_and_routing_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = (
                '{"status":"noop","reason":"nothing eligible","project":"getbill",'
                '"skill":"research-run","target_id":"ISSUE-42","did_work":false,'
                '"work_kind":"none","quality_outcome":"not-applicable",'
                '"next_action":"wait"}'
            )
            result = self._run_helper(
                root,
                summary,
                "--candidate-model",
                "gpt-5.6-sol",
                "--routing-reason",
                "baseline retained during observation",
                "--target-id",
                "directed-target",
                "--gate-decision",
                "directed",
                "--gate-reason",
                "human supplied directed target",
                "--fingerprint",
                "sha256:abc",
                "--require-structured-result",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            record = json.loads(
                (root / "history.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual("noop", record["outcome"])
            self.assertEqual(summary, record["summary"])
            self.assertTrue(record["model_invoked"])
            self.assertEqual("high", record["reasoning_effort"])
            self.assertEqual("observe", record["routing_mode"])
            self.assertEqual("gpt-5.6-sol", record["candidate_model"])
            self.assertEqual(
                "baseline retained during observation", record["routing_reason"]
            )
            self.assertEqual("ISSUE-42", record["target_id"])
            self.assertEqual("none", record["work_kind"])
            self.assertEqual("not-applicable", record["quality_outcome"])
            self.assertFalse(record["did_work"])
            self.assertEqual("directed", record["gate_decision"])
            self.assertEqual(
                "human supplied directed target", record["gate_reason"]
            )
            self.assertEqual("sha256:abc", record["fingerprint"])

    def test_locked_helper_rejects_incomplete_or_mistyped_strict_results(self):
        valid = {
            "status": "success",
            "reason": "completed",
            "project": "getbill",
            "skill": "research-run",
            "target_id": None,
            "did_work": True,
            "work_kind": "research",
            "quality_outcome": "validated",
            "next_action": "review results",
        }
        cases = {
            "incomplete": {"status": "success"},
            "mistyped": {**valid, "did_work": "true"},
            "additional-property": {**valid, "unexpected": "value"},
        }

        for name, payload in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                summary = json.dumps(payload, separators=(",", ":"))

                result = self._run_helper(
                    root,
                    summary,
                    "--require-structured-result",
                )

                self.assertNotEqual(0, result.returncode, result.stderr)
                record = json.loads(
                    (root / "history.jsonl").read_text(encoding="utf-8").strip()
                )
                self.assertEqual("failed", record["outcome"])
                self.assertEqual(0, record["exit_code"])
                self.assertEqual(summary, record["summary"])

    def test_locked_helper_keeps_manual_legacy_success_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            result = self._run_helper(root, "legacy bounded summary")

            self.assertEqual(0, result.returncode, result.stderr)
            record = json.loads(
                (root / "history.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual("success", record["outcome"])
            self.assertEqual("legacy bounded summary", record["summary"])

    def test_locked_helper_marks_overlap_as_not_invoked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = root / "role.lock"
            with lock_path.open("w", encoding="utf-8") as held_lock:
                fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self._run_helper(root, '{"status":"success"}')

            self.assertEqual(0, result.returncode, result.stderr)
            record = json.loads(
                (root / "history.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual("noop", record["outcome"])
            self.assertFalse(record["model_invoked"])
            self.assertEqual("high", record["reasoning_effort"])
            self.assertEqual("observe", record["routing_mode"])

    def _run_helper(self, root: Path, summary: str, *extra: str):
        command = (
            "from pathlib import Path; "
            f"Path({str(root / 'summary.txt')!r}).write_text({summary!r}, encoding='utf-8')"
        )
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts/pitcrew_locked_exec.py"),
                "--lock-file",
                str(root / "role.lock"),
                "--project",
                "getbill",
                "--skill",
                "research-run",
                "--model",
                "gpt-5.6-terra",
                "--reasoning-effort",
                "high",
                "--routing-mode",
                "observe",
                "--summary-file",
                str(root / "summary.txt"),
                "--history-file",
                str(root / "history.jsonl"),
                *extra,
                "--",
                sys.executable,
                "-c",
                command,
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
