import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import mock
import unittest
from pathlib import Path

from scripts.pitcrew_run_dispatcher import RunDispatcher
from scripts import pitcrew_run_dispatcher as dispatcher_module
from scripts.pitcrew_run_store import RunStore


class FakeProcess:
    def __init__(self, pid=321): self.pid = pid


class RunDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name).resolve() / "private" / "runs.sqlite")
        self.calls = []
        def popen(args, **kwargs):
            self.calls.append((args, kwargs)); return FakeProcess()
        self.dispatcher = RunDispatcher(self.store, "/runner", process_factory=popen)

    def tearDown(self): self.temp.cleanup()

    def enqueue(self, target):
        return self.store.enqueue(project="demo", skill="qa-run", source="scheduled", target=target)

    def test_drain_spawns_exact_target_arguments_and_marks_pid(self):
        run = self.enqueue("ABC-1")
        result = self.dispatcher.drain("demo", {"qa-run": 1})
        self.assertEqual([run["run_id"]], [row["run_id"] for row in result["spawned"]])
        self.assertEqual(["/runner", "qa-run", "demo", "--target", "ABC-1", "--scheduled", "--coordinated-run", run["run_id"]], self.calls[0][0])
        self.assertEqual({"stdin": __import__("subprocess").DEVNULL, "stdout": __import__("subprocess").DEVNULL,
                          "stderr": __import__("subprocess").DEVNULL, "start_new_session": True}, self.calls[0][1])
        self.assertEqual(321, self.store.get(run["run_id"])["pid"])

    def test_false_target_is_cancelled_and_next_run_starts(self):
        stale = self.enqueue("stale")
        good = self.enqueue("good")
        self.dispatcher.target_validator = lambda row: row["target"] != "stale"
        result = self.dispatcher.drain("demo", {"qa-run": 2})
        self.assertEqual("cancelled", self.store.get(stale["run_id"])["state"])
        self.assertEqual([good["run_id"]], [row["run_id"] for row in result["spawned"]])

    def test_spawn_error_fails_and_continues(self):
        first, second = self.enqueue("one"), self.enqueue("two")
        calls = 0
        def popen(*args, **kwargs):
            nonlocal calls; calls += 1
            if calls == 1: raise OSError("nope")
            return FakeProcess(99)
        dispatcher = RunDispatcher(self.store, "/runner", process_factory=popen)
        dispatcher.drain("demo", {"qa-run": 2})
        self.assertEqual(("failed", "spawn_failed"), (self.store.get(first["run_id"])["state"], self.store.get(first["run_id"])["error_code"]))
        self.assertEqual("running", self.store.get(second["run_id"])["state"])

    def test_cancel_only_running_signals_and_leaves_queued_unrequested(self):
        running, queued = self.enqueue("running"), self.enqueue("queued")
        self.store.claim_ready(project="demo", capacities={"qa-run": 1})
        self.store.mark_pid(running["run_id"], 123)
        signals = []
        dispatcher = RunDispatcher(self.store, "/runner", killpg=lambda pid, sig: signals.append((pid, sig)), sleep=lambda _: None)
        dispatcher.cancel_running("demo", grace_seconds=0)
        self.assertEqual([(123, 15), (123, 9)], signals)
        self.assertEqual("cancelled", self.store.get(running["run_id"])["state"])
        self.assertEqual(("queued", 0), (self.store.get(queued["run_id"])["state"], self.store.get(queued["run_id"])["cancel_requested"]))

    def test_capacity_three_leaves_fourth_queued_then_starts_it(self):
        runs = [self.enqueue(str(number)) for number in range(4)]
        self.assertEqual(3, len(self.dispatcher.drain("demo", {"qa-run": 3})["spawned"]))
        self.assertEqual("queued", self.store.get(runs[3]["run_id"])["state"])
        self.store.finish(runs[0]["run_id"], state="succeeded")
        self.assertEqual([runs[3]["run_id"]], [row["run_id"] for row in self.dispatcher.drain("demo", {"qa-run": 3})["spawned"]])

    def test_validator_exception_cancels_stale_and_starts_next(self):
        stale, good = self.enqueue("stale"), self.enqueue("good")
        self.dispatcher.target_validator = lambda row: (_ for _ in ()).throw(ValueError()) if row["target"] == "stale" else True
        result = self.dispatcher.drain("demo", {"qa-run": 2})
        self.assertEqual([good["run_id"]], [row["run_id"] for row in result["spawned"]])
        self.assertEqual(("failed", "validation_failed"), (self.store.get(stale["run_id"])["state"], self.store.get(stale["run_id"])["error_code"]))

    def test_cancel_pid_absent_finishes_without_signal(self):
        run = self.enqueue("running")
        self.store.claim_ready(project="demo", capacities={"qa-run": 1})
        calls = []
        RunDispatcher(self.store, "/runner", killpg=lambda *value: calls.append(value), sleep=lambda _: None).cancel_running("demo", grace_seconds=0)
        self.assertEqual([], calls); self.assertEqual("cancelled", self.store.get(run["run_id"])["state"])

    def test_cancel_terminal_race_does_not_kill_or_overwrite(self):
        run = self.enqueue("running")
        self.store.claim_ready(project="demo", capacities={"qa-run": 1}); self.store.mark_pid(run["run_id"], 123)
        signals = []
        dispatcher = RunDispatcher(self.store, "/runner", killpg=lambda *value: signals.append(value),
            sleep=lambda _: self.store.finish(run["run_id"], state="succeeded"))
        self.assertEqual([], dispatcher.cancel_running("demo", grace_seconds=0))
        self.assertEqual([(123, __import__("signal").SIGTERM)], signals)
        self.assertEqual("succeeded", self.store.get(run["run_id"])["state"])

    def test_reconcile_purges_and_drains(self):
        clock = type("Clock", (), {"value": datetime(2026, 1, 1, tzinfo=timezone.utc), "__call__": lambda self: self.value})()
        store = RunStore(Path(self.temp.name).resolve() / "other" / "runs.sqlite", now=clock, pid_alive=lambda _: False)
        dead = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="dead")
        store.claim_ready(project="demo", capacities={"qa-run": 1}); store.mark_pid(dead["run_id"], 99)
        old = store.enqueue(project="demo", skill="other", source="scheduled", target="old"); store.finish(old["run_id"], state="succeeded")
        clock.value += timedelta(days=8)
        queued = store.enqueue(project="demo", skill="qa-run", source="scheduled", target="queued")
        calls = []
        result = RunDispatcher(store, "/runner", process_factory=lambda *a, **k: calls.append((a,k)) or FakeProcess()).reconcile_and_drain("demo", {"qa-run": 1})
        self.assertEqual([dead["run_id"]], [row["run_id"] for row in result["reconciled"]]); self.assertEqual(1, result["purged"])
        self.assertEqual([queued["run_id"]], [row["run_id"] for row in result["spawned"]])

    def test_cli_enqueue_bind_and_drain_contracts(self):
        class NoSpawn(RunDispatcher):
            def __init__(self, store, runner): super().__init__(store, runner, process_factory=lambda *a, **k: FakeProcess())
        runtime = lambda project, skill=None: (self.store, {"qa-run": 1})
        output = StringIO()
        with mock.patch("sys.stdout", output):
            self.assertEqual(0, dispatcher_module.main(["enqueue", "--project", "demo", "--skill", "qa-run", "--target", "ABC-1"], runtime, NoSpawn))
        first = json.loads(output.getvalue()); self.assertEqual({"run_id", "state", "queue_position", "created"}, set(first)); self.assertTrue(first["created"])
        output = StringIO()
        with mock.patch("sys.stdout", output): dispatcher_module.main(["enqueue", "--project", "demo", "--skill", "qa-run", "--target", "ABC-1"], runtime, NoSpawn)
        second = json.loads(output.getvalue()); self.assertEqual(first["run_id"], second["run_id"]); self.assertFalse(second["created"])
        scheduled = self.store.enqueue(project="demo", skill="qa-run", source="scheduled")
        output = StringIO()
        with mock.patch("sys.stdout", output): self.assertEqual(0, dispatcher_module.main(["bind-target", "--project", "demo", "--run-id", scheduled["run_id"], "--target", "ABC-2"], runtime, NoSpawn))
        self.assertEqual("ABC-2", json.loads(output.getvalue())["target"])
        output = StringIO()
        with mock.patch("sys.stdout", output): self.assertEqual(0, dispatcher_module.main(["drain", "--project", "demo"], runtime, NoSpawn))
        self.assertEqual({"reconciled", "purged", "claimed", "spawned", "cancelled", "failed"}, set(json.loads(output.getvalue())))

    def test_cli_errors_are_safe(self):
        runtime = lambda project, skill=None: (_ for _ in ()).throw(ValueError("global stop is active"))
        error = StringIO()
        with mock.patch("sys.stderr", error):
            self.assertEqual(2, dispatcher_module.main(["enqueue", "--project", "demo", "--skill", "qa-run"], runtime))
        self.assertIn("pitcrew dispatcher:", error.getvalue())

    def test_cli_real_runtime_and_global_stop(self):
        root = Path(self.temp.name).resolve() / "codex"
        env = {**os.environ, "CODEX_HOME": str(root), "HOME": self.temp.name}
        configured = subprocess.run([str(Path(__file__).resolve().parents[1] / "bin/configure.sh"), "getbill", "--profile", "getbill"], env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(0, configured.returncode, configured.stderr)
        with mock.patch.dict(os.environ, env, clear=False):
            store, capacity = dispatcher_module._runtime("getbill", "implementer-run")
            self.assertEqual(3, capacity["implementer-run"])
            class NoSpawn(RunDispatcher):
                def __init__(self, store, runner): super().__init__(store, runner, process_factory=lambda *a, **k: FakeProcess())
            output = StringIO()
            with mock.patch("sys.stdout", output): self.assertEqual(0, dispatcher_module.main(["enqueue", "--project", "getbill", "--skill", "implementer-run", "--target", "ABC-9"], dispatcher_factory=NoSpawn))
            row = json.loads(output.getvalue()); self.assertEqual({"run_id", "state", "queue_position", "created"}, set(row)); self.assertEqual("ABC-9", store.get(row["run_id"])["target"])
            from scripts.pitcrew_runtime_state import write_state
            write_state("getbill", "stopped", env)
            error = StringIO()
            with mock.patch("sys.stderr", error): self.assertEqual(2, dispatcher_module.main(["enqueue", "--project", "getbill", "--skill", "implementer-run"], dispatcher_factory=NoSpawn))
            self.assertIn("global stop is active", error.getvalue())

    def test_mark_pid_race_reaps_spawned_process(self):
        run = self.enqueue("race")
        signals = []
        class Process:
            pid = 456
            def __init__(self): self.waited = False
            def wait(self, timeout=None): self.waited = True; raise TimeoutError()
        process = Process()
        def popen(*args, **kwargs): return process
        original = self.store.mark_pid
        def race(run_id, pid):
            self.store.finish(run_id, state="cancelled")
            raise __import__("scripts.pitcrew_run_store", fromlist=["RunStateError"]).RunStateError("race")
        self.store.mark_pid = race
        dispatcher = RunDispatcher(self.store, "/runner", process_factory=popen, killpg=lambda *value: signals.append(value), sleep=lambda _: None)
        self.assertEqual([], dispatcher.drain("demo", {"qa-run": 1})["spawned"])
        self.assertEqual([(456, __import__("signal").SIGTERM), (456, __import__("signal").SIGKILL)], signals)
        self.assertTrue(process.waited); self.assertEqual("cancelled", self.store.get(run["run_id"])["state"])
        self.store.mark_pid = original

    def test_bind_target_requires_project(self):
        run = self.store.enqueue(project="other", skill="qa-run", source="scheduled")
        with self.assertRaises(ValueError): self.dispatcher.bind_target("demo", run["run_id"], "ABC-2")
        self.assertEqual("ABC-2", self.dispatcher.bind_target("other", run["run_id"], "ABC-2")["target"])
