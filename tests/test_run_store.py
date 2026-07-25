import os
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.pitcrew_run_store import (
    RunConflict,
    RunPaused,
    RunStateError,
    RunStore,
    RunStoreError,
)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class InsertFailureConnection:
    def __init__(self, connection, failures):
        self._connection = connection
        self._failures = failures

    def execute(self, statement, parameters=()):
        if statement.startswith("INSERT INTO runs") and self._failures["remaining"]:
            self._failures["remaining"] -= 1
            self._failures["count"] += 1
            raise sqlite3.IntegrityError("injected collision")
        return self._connection.execute(statement, parameters)

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self._connection, name)


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.path = Path(self.temp.name).resolve() / "private" / "runs.sqlite"
        self.store = RunStore(self.path, now=self.clock, pid_alive=lambda pid: pid == 42)

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, target="T-1", skill="implementer-run"):
        return self.store.enqueue(project="demo", skill=skill, source="dashboard", target=target)

    def test_same_target_concurrently_is_deduplicated(self):
        barrier = threading.Barrier(2)
        def enqueue():
            barrier.wait()
            return self.enqueue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: enqueue(), range(2)))
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual({True, False}, {first["created"], second["created"]})
        self.assertEqual(1, len(self.store.list_runs("demo", active_only=True)))

    def test_enqueue_retries_one_injected_integrity_collision(self):
        path = Path(self.temp.name).resolve() / "retry-once.sqlite"
        failures = {"remaining": 1, "count": 0}
        def factory(database, **kwargs):
            return InsertFailureConnection(sqlite3.connect(database, **kwargs), failures)
        store = RunStore(path, now=self.clock, connect_factory=factory)
        run = store.enqueue(project="demo", skill="implementer-run", source="dashboard", target="T-retry")
        self.assertTrue(run["created"])
        self.assertEqual(1, failures["count"])
        self.assertEqual(1, len(store.list_runs("demo", active_only=True)))

    def test_enqueue_raises_conflict_after_three_injected_integrity_collisions(self):
        path = Path(self.temp.name).resolve() / "retry-limit.sqlite"
        failures = {"remaining": 3, "count": 0}
        def factory(database, **kwargs):
            return InsertFailureConnection(sqlite3.connect(database, **kwargs), failures)
        store = RunStore(path, now=self.clock, connect_factory=factory)
        with self.assertRaises(RunConflict):
            store.enqueue(project="demo", skill="implementer-run", source="dashboard", target="T-retry")
        self.assertEqual(3, failures["count"])
        self.assertEqual([], store.list_runs("demo", active_only=True))

    def test_claims_fifo_and_reports_queue_position(self):
        runs = [self.enqueue(f"T-{number}") for number in range(1, 5)]
        claimed = self.store.claim_ready(project="demo", capacities={"implementer-run": 3})
        self.assertEqual([run["run_id"] for run in runs[:3]], [run["run_id"] for run in claimed])
        snapshot = self.store.snapshot("demo", {"implementer-run": 3})
        queued = [run for run in snapshot["runs"] if run["state"] == "queued"]
        self.assertEqual((runs[3]["run_id"], 1), (queued[0]["run_id"], queued[0]["queue_position"]))
        self.store.finish(claimed[0]["run_id"], state="succeeded")
        self.assertEqual([runs[3]["run_id"]], [run["run_id"] for run in self.store.claim_ready(project="demo", capacities={"implementer-run": 3})])

    def test_capacities_are_independent_per_role(self):
        first = self.enqueue("T-1", "implementer-run")
        second = self.enqueue("T-2", "implementer-run")
        third = self.enqueue("T-3", "qa-run")
        claimed = self.store.claim_ready(project="demo", capacities={"implementer-run": 1, "qa-run": 1})
        self.assertEqual({first["run_id"], third["run_id"]}, {run["run_id"] for run in claimed})
        self.assertEqual("queued", self.store.get(second["run_id"])["state"])

    def test_capacities_are_strictly_validated_by_claim_and_snapshot(self):
        for capacities in ([], {1: 1}, {"": 1}, {"x": 0}, {"x": 17}, {"x": True}, {"x": "3"}):
            with self.subTest(capacities=capacities):
                with self.assertRaisesRegex(RunStoreError, "capacity is invalid"):
                    self.store.claim_ready(project="demo", capacities=capacities)
                with self.assertRaisesRegex(RunStoreError, "capacity is invalid"):
                    self.store.snapshot("demo", capacities)

    def test_bind_target_conflict_is_atomic_and_idempotent(self):
        first = self.store.enqueue(project="demo", skill="scheduled-run", source="scheduled")
        second = self.store.enqueue(project="demo", skill="scheduled-other", source="scheduled")
        self.store.bind_target(first["run_id"], "T-1")
        self.assertEqual("T-1", self.store.bind_target(first["run_id"], "T-1")["target"])
        with self.assertRaisesRegex(RunConflict, first["run_id"]):
            self.store.bind_target(second["run_id"], "T-1")

    def test_terminal_releases_target_and_predecessor_is_recorded(self):
        first = self.enqueue()
        self.store.finish(first["run_id"], state="succeeded")
        second = self.store.enqueue(project="demo", skill="implementer-run", source="dashboard", target="T-1", predecessor_run_id=first["run_id"])
        self.assertTrue(second["created"])
        self.assertEqual(first["run_id"], second["predecessor_run_id"])

    def test_finish_uses_a_public_phase_for_each_terminal_state(self):
        for state, phase in (("succeeded", "Terminé"), ("failed", "Échec"), ("cancelled", "Annulé")):
            with self.subTest(state=state):
                run = self.enqueue(f"T-{state}")
                self.assertEqual(phase, self.store.finish(run["run_id"], state=state)["phase"])
                with self.assertRaises(RunStateError):
                    self.store.finish(run["run_id"], state=state)

    def test_stopped_project_blocks_admission_and_claim_and_tracks_generation(self):
        stopped = self.store.set_project_state("demo", "stopped")
        with self.assertRaises(RunPaused): self.enqueue()
        with self.assertRaises(RunPaused): self.store.claim_ready(project="demo", capacities={})
        resumed = self.store.set_project_state("demo", "running")
        self.assertGreater(resumed["generation"], stopped["generation"])
        self.assertTrue(self.enqueue()["created"])

    def test_project_state_rejects_non_hashable_values_publicly(self):
        for state in ([], {}):
            with self.assertRaises(RunStoreError):
                self.store.set_project_state("demo", state)

    def test_reconcile_fails_dead_or_stale_running_only(self):
        dead = self.enqueue("T-dead")
        stale = self.enqueue("T-stale")
        live = self.enqueue("T-live")
        queued = self.enqueue("T-queued")
        self.store.claim_ready(project="demo", capacities={"implementer-run": 3})
        self.store.mark_pid(dead["run_id"], 13)
        self.store.mark_pid(stale["run_id"], 42)
        self.store.mark_pid(live["run_id"], 42)
        self.clock.advance(seconds=31)
        self.store.heartbeat(live["run_id"], "still running")
        reconciled = self.store.reconcile("demo")
        self.assertEqual({dead["run_id"], stale["run_id"]}, {run["run_id"] for run in reconciled})
        self.assertEqual("failed", self.store.get(dead["run_id"])["state"])
        self.assertEqual("failed", self.store.get(stale["run_id"])["state"])
        self.assertEqual("running", self.store.get(live["run_id"])["state"])
        self.assertEqual("queued", self.store.get(queued["run_id"])["state"])

    def test_reconcile_treats_stale_heartbeat_as_authoritative_over_live_pid(self):
        run = self.enqueue()
        self.store.claim_ready(project="demo", capacities={"implementer-run": 1})
        self.store.mark_pid(run["run_id"], 42)
        self.clock.advance(seconds=31)
        self.assertEqual("failed", self.store.reconcile("demo")[0]["state"])

    def test_purge_only_removes_old_terminal_runs(self):
        old = self.enqueue("T-old")
        self.store.finish(old["run_id"], state="cancelled")
        active = self.enqueue("T-active")
        self.clock.advance(days=8)
        self.assertEqual(1, self.store.purge("demo"))
        self.assertIsNone(self.store.get(old["run_id"]))
        self.assertIsNotNone(self.store.get(active["run_id"]))

    def test_invalid_values_are_public_errors(self):
        with self.assertRaises(RunStoreError): self.store.enqueue(project="", skill="x", source="dashboard")
        with self.assertRaises(RunStoreError): self.store.enqueue(project="demo", skill="x", source="bad")
        with self.assertRaises(RunStoreError): self.store.enqueue(project="demo", skill="x", source="dashboard", target=" ")
        with self.assertRaises(RunStoreError): self.store.enqueue(project="demo", skill="x", source="dashboard", target="x" * 1001)
        run = self.enqueue()
        with self.assertRaises(RunStoreError): self.store.mark_pid(run["run_id"], 0)
        with self.assertRaises(RunStateError): self.store.finish(run["run_id"], state="running")
        with self.assertRaises(RunStoreError): RunStore(self.path.with_name("bad.sqlite"), now=lambda: datetime.now())

    def test_invalid_source_and_mutation_values_are_public_errors(self):
        for source in ([], {}):
            with self.assertRaisesRegex(RunStoreError, "source is invalid"):
                self.store.enqueue(project="demo", skill="x", source=source)
        run = self.enqueue("T-values")
        with self.assertRaises(RunStateError): self.store.heartbeat(run["run_id"], "phase")
        claimed = self.store.claim_ready(project="demo", capacities={"implementer-run": 1})[0]
        for phase in ("", "x" * 201):
            with self.assertRaises(RunStoreError): self.store.heartbeat(claimed["run_id"], phase)
        for code in ("", 1):
            with self.assertRaises(RunStoreError): self.store.finish(claimed["run_id"], state="failed", error_code=code)
        for message in ("", 1, "x" * 2049):
            with self.assertRaises(RunStoreError): self.store.finish(claimed["run_id"], state="failed", error_message=message)

    def test_get_includes_role_queue_position(self):
        first, second = self.enqueue("T-first"), self.enqueue("T-second")
        self.assertEqual(1, self.store.get(first["run_id"])["queue_position"])
        self.assertEqual(2, self.store.get(second["run_id"])["queue_position"])
        self.store.claim_ready(project="demo", capacities={"implementer-run": 1})
        self.assertEqual(0, self.store.get(first["run_id"])["queue_position"])
        self.assertEqual(1, self.store.get(second["run_id"])["queue_position"])

    def test_request_cancel_marks_running_runs_only(self):
        running = self.enqueue("T-running")
        self.store.claim_ready(project="demo", capacities={"implementer-run": 1})
        queued = self.enqueue("T-queued")
        cancelled = self.store.request_cancel("demo")
        self.assertEqual({running["run_id"]}, {run["run_id"] for run in cancelled})
        self.assertTrue(all(run["cancel_requested"] for run in cancelled))
        self.assertEqual("queued", self.store.get(queued["run_id"])["state"])
        self.assertFalse(self.store.get(queued["run_id"])["cancel_requested"])
        self.assertEqual("running", self.store.get(running["run_id"])["state"])

    def test_snapshot_has_deterministic_runs_positions_and_capacity_counts(self):
        first = self.enqueue("T-1")
        second = self.enqueue("T-2")
        terminal = self.enqueue("T-3", "qa-run")
        self.store.claim_ready(project="demo", capacities={"implementer-run": 1})
        self.store.finish(terminal["run_id"], state="succeeded")
        snapshot = self.store.snapshot("demo", {"implementer-run": 2, "qa-run": 4})
        self.assertTrue(snapshot["has_active"])
        self.assertEqual([first["run_id"], second["run_id"], terminal["run_id"]], [run["run_id"] for run in snapshot["runs"]])
        self.assertEqual(0, snapshot["runs"][0]["queue_position"])
        self.assertEqual(1, snapshot["runs"][1]["queue_position"])
        self.assertEqual({"running": 1, "queued": 1, "max_concurrent": 2}, snapshot["capacity"]["implementer-run"])
        self.assertEqual({"running": 0, "queued": 0, "max_concurrent": 4}, snapshot["capacity"]["qa-run"])

    def test_sqlite_errors_are_normalized(self):
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE runs")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(RunStoreError, "^database operation failed$"):
            self.store.list_runs("demo")

    def test_unsupported_schema_version_does_not_change_database(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(RunStoreError, "unsupported schema version"):
            RunStore(self.path)
        self.assertEqual(99, sqlite3.connect(self.path).execute("PRAGMA user_version").fetchone()[0])

    def test_failed_schema_migration_rolls_back_version_and_new_tables(self):
        path = Path(self.temp.name).resolve() / "broken.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
        connection.commit()
        connection.close()
        path.chmod(0o600)
        with self.assertRaisesRegex(RunStoreError, "database initialization failed"):
            RunStore(path)
        connection = sqlite3.connect(path)
        self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_controls'").fetchone())
        connection.close()

    def test_symlink_and_private_permissions_are_enforced(self):
        target = Path(self.temp.name).resolve() / "target.sqlite"
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaisesRegex(RunStoreError, "symlink"):
            RunStore(self.path)
        self.path.unlink()
        store = RunStore(self.path)
        self.assertEqual(0o700, self.path.parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)
        self.assertIsNotNone(store)

    def test_parent_symlink_and_insecure_existing_parent_are_refused(self):
        parent_target = Path(self.temp.name).resolve() / "parent-target"
        parent_target.mkdir(mode=0o700)
        parent_link = Path(self.temp.name).resolve() / "parent-link"
        parent_link.symlink_to(parent_target, target_is_directory=True)
        with self.assertRaisesRegex(RunStoreError, "symlink"):
            RunStore(parent_link / "runs.sqlite")
        insecure = Path(self.temp.name).resolve() / "insecure"
        insecure.mkdir(mode=0o755)
        with self.assertRaisesRegex(RunStoreError, "private"):
            RunStore(insecure / "runs.sqlite")

    def test_non_immediate_ancestor_symlink_is_refused(self):
        actual = Path(self.temp.name).resolve() / "actual"
        actual.mkdir(mode=0o700)
        linked = Path(self.temp.name).resolve() / "linked"
        linked.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(RunStoreError, "symlink"):
            RunStore(linked / "nested" / "runs.sqlite")

    def test_connection_guard_rejects_database_replaced_before_sqlite_connect(self):
        path = Path(self.temp.name).resolve() / "guard.sqlite"
        target = Path(self.temp.name).resolve() / "target.sqlite"
        def replace_then_connect(database, **kwargs):
            Path(database).unlink()
            Path(database).symlink_to(target)
            return sqlite3.connect(":memory:", **kwargs)
        with self.assertRaisesRegex(RunStoreError, "database operation failed"):
            RunStore(path, connect_factory=replace_then_connect)
        self.assertFalse(target.exists())

    def test_schema_migration_is_idempotent(self):
        self.assertEqual(1, sqlite3.connect(self.path).execute("PRAGMA user_version").fetchone()[0])
        RunStore(self.path)
        self.assertEqual(1, sqlite3.connect(self.path).execute("PRAGMA user_version").fetchone()[0])
