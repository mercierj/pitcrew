import json
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import mock
import unittest
from pathlib import Path

from scripts.pitcrew_run_dispatcher import RunDispatcher
from scripts import pitcrew_run_dispatcher as dispatcher_module
from scripts.pitcrew_run_store import RunPaused, RunStore


class FakeProcess:
    def __init__(self, pid=321): self.pid = pid


class RunDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name).resolve() / "private" / "runs.sqlite")
        self.calls = []
        def popen(args, **kwargs):
            self.calls.append((args, kwargs)); return FakeProcess()
        self.dispatcher = RunDispatcher(
            self.store,
            "/runner",
            process_factory=popen,
            target_validator=lambda row: True,
        )

    def tearDown(self): self.temp.cleanup()

    def enqueue(self, target):
        return self.store.enqueue(project="demo", skill="qa-run", source="scheduled", target=target)

    def gitlab_config(self):
        return {
            "providers": {"forge": "gitlab", "tracker": "gitlab"},
            "gitlab": {
                "host": "gitlab.example",
                "project_path": "crew/demo",
                "project_id": 42,
                "tracker": {
                    "labels": {
                        "agent": "agent-label",
                        "investigate": "investigate-label",
                    },
                    "states": {
                        "todo": "todo-label",
                        "review": "review-label",
                        "blocked": "blocked-label",
                        "done": "done-label",
                    },
                },
            },
        }

    def provider_result(self, *, iid=7, state="opened", labels=None, returncode=0, stdout=None, stderr=None):
        payload = {
            "iid": iid,
            "state": state,
            "labels": labels if labels is not None else ["agent-label", "todo-label"],
        }
        return subprocess.CompletedProcess(
            [],
            returncode,
            stdout=json.dumps(payload) if stdout is None else stdout,
            stderr="provider secret must not be exposed" if stderr is None else stderr,
        )

    def test_drain_spawns_exact_target_arguments_and_marks_pid(self):
        run = self.enqueue("ABC-1")
        result = self.dispatcher.drain("demo", {"qa-run": 1})
        self.assertEqual([run["run_id"]], [row["run_id"] for row in result["spawned"]])
        self.assertEqual(["/runner", "qa-run", "demo", "--target", "ABC-1", "--scheduled", "--coordinated-run", run["run_id"]], self.calls[0][0])
        self.assertEqual({"stdin": __import__("subprocess").DEVNULL, "stdout": __import__("subprocess").DEVNULL,
                          "stderr": __import__("subprocess").DEVNULL, "start_new_session": True}, self.calls[0][1])
        self.assertEqual(321, self.store.get(run["run_id"])["pid"])

    def test_cli_default_validator_checks_gitlab_before_spawn(self):
        provider = mock.Mock(return_value=self.provider_result())
        spawned = []

        class NoProcess(RunDispatcher):
            def __init__(inner_self, store, runner):
                super().__init__(
                    store,
                    runner,
                    process_factory=lambda *args, **kwargs: spawned.append((args, kwargs)) or FakeProcess(),
                )

        runtime = lambda project, skill=None: (self.store, {"implementer-run": 1})
        output = StringIO()
        error = StringIO()
        target = "https://gitlab.example/crew/demo/-/issues/7"
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
            mock.patch("sys.stdout", output),
            mock.patch("sys.stderr", error),
        ):
            self.assertEqual(
                0,
                dispatcher_module.main(
                    ["enqueue", "--project", "demo", "--skill", "implementer-run", "--target", target],
                    runtime,
                    NoProcess,
                ),
            )
        self.assertEqual("", error.getvalue())
        self.assertEqual("running", json.loads(output.getvalue())["state"])
        provider.assert_called_once_with(
            ["glab", "api", "--hostname", "gitlab.example", "projects/42/issues/7"]
        )
        self.assertEqual(1, len(spawned))

    def test_scheduler_like_default_validator_cancels_stale_targets(self):
        provider = mock.Mock(
            side_effect=[
                self.provider_result(state="closed"),
                self.provider_result(iid=8, labels=["agent-label", "blocked-label"]),
            ]
        )
        calls = []
        with mock.patch.object(
            dispatcher_module,
            "load_runtime_config",
            return_value=self.gitlab_config(),
        ):
            dispatcher = RunDispatcher(
                self.store,
                "/runner",
                process_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or FakeProcess(),
                provider_runner=provider,
            )
            for iid in (7, 8):
                run = self.store.enqueue(
                    project="demo",
                    skill="implementer-run",
                    source="scheduled",
                    target=f"https://gitlab.example/crew/demo/-/issues/{iid}",
                )
                dispatcher.drain("demo", {"implementer-run": 1})
                self.assertEqual(
                    ("cancelled", "stale_target"),
                    (self.store.get(run["run_id"])["state"], self.store.get(run["run_id"])["error_code"]),
                )
        self.assertEqual([], calls)
        self.assertEqual(2, provider.call_count)

    def test_default_validator_fails_closed_when_provider_is_unavailable_or_malformed(self):
        provider = mock.Mock(
            side_effect=[
                OSError("provider unavailable"),
                self.provider_result(iid=8, stdout="{malformed"),
            ]
        )
        calls = []
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
        ):
            dispatcher = RunDispatcher(
                self.store,
                "/runner",
                process_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or FakeProcess(),
            )
            for iid in (7, 8):
                run = self.store.enqueue(
                    project="demo",
                    skill="implementer-run",
                    source="scheduled",
                    target=f"https://gitlab.example/crew/demo/-/issues/{iid}",
                )
                dispatcher.drain("demo", {"implementer-run": 1})
                self.assertEqual(
                    ("failed", "validation_failed"),
                    (self.store.get(run["run_id"])["state"], self.store.get(run["run_id"])["error_code"]),
                )
        self.assertEqual([], calls)

    def test_default_validator_classifies_provider_404_as_stale(self):
        provider = mock.Mock(
            return_value=self.provider_result(
                returncode=1,
                stderr="glab: API request failed (HTTP 404)\n",
            )
        )
        calls = []
        run = self.store.enqueue(
            project="demo",
            skill="implementer-run",
            source="scheduled",
            target="https://gitlab.example/crew/demo/-/issues/7",
        )
        with mock.patch.object(
            dispatcher_module,
            "load_runtime_config",
            return_value=self.gitlab_config(),
        ):
            RunDispatcher(
                self.store,
                "/runner",
                process_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or FakeProcess(),
                provider_runner=provider,
            ).drain("demo", {"implementer-run": 1})
        self.assertEqual([], calls)
        self.assertEqual(
            ("cancelled", "stale_target"),
            (self.store.get(run["run_id"])["state"], self.store.get(run["run_id"])["error_code"]),
        )

    def test_default_validator_keeps_provider_500_as_validation_failure(self):
        secret = "provider-secret-value"
        provider = mock.Mock(
            return_value=self.provider_result(
                returncode=1,
                stderr=f"glab: {secret} (HTTP 500)\n",
            )
        )
        run = self.store.enqueue(
            project="demo",
            skill="implementer-run",
            source="scheduled",
            target="https://gitlab.example/crew/demo/-/issues/7",
        )
        with mock.patch.object(
            dispatcher_module,
            "load_runtime_config",
            return_value=self.gitlab_config(),
        ):
            RunDispatcher(self.store, "/runner", provider_runner=provider).drain(
                "demo",
                {"implementer-run": 1},
            )
        row = self.store.get(run["run_id"])
        self.assertEqual(("failed", "validation_failed"), (row["state"], row["error_code"]))
        self.assertNotIn(secret, row["error_message"])

    def test_default_validator_rejects_unknown_roles_without_provider_call(self):
        provider = mock.Mock()
        run = self.store.enqueue(
            project="demo",
            skill="qa-run",
            source="scheduled",
            target="https://gitlab.example/crew/demo/-/issues/7",
        )
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
        ):
            dispatcher = RunDispatcher(self.store, "/runner")
            dispatcher.drain("demo", {"qa-run": 1})
        self.assertEqual(
            ("cancelled", "stale_target"),
            (self.store.get(run["run_id"])["state"], self.store.get(run["run_id"])["error_code"]),
        )
        provider.assert_not_called()

    def test_default_validator_rejects_noncanonical_targets_without_provider_call(self):
        provider = mock.Mock()
        targets = [
            "http://gitlab.example/crew/demo/-/issues/1",
            "https://other.example/crew/demo/-/issues/2",
            "https://gitlab.example/crew/demo/-/issues/3?view=full",
            "https://gitlab.example/crew/demo/-/issues/4#note",
            "https://gitlab.example/crew/demo/-/work_items/5",
            "https://gitlab.example/crew/demo/-/issues/0",
        ]
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
        ):
            dispatcher = RunDispatcher(
                self.store,
                "/runner",
                process_factory=lambda *args, **kwargs: FakeProcess(),
            )
            for target in targets:
                run = self.store.enqueue(
                    project="demo",
                    skill="implementer-run",
                    source="scheduled",
                    target=target,
                )
                dispatcher.drain("demo", {"implementer-run": 1})
                self.assertEqual("cancelled", self.store.get(run["run_id"])["state"])
        provider.assert_not_called()

    def test_default_validator_resolves_role_lifecycle_labels_from_config(self):
        cases = [
            ("implementer-run", ["agent-label", "review-label"]),
            ("validator-run", ["agent-label", "review-label"]),
            ("reviewer-run", ["agent-label", "review-label"]),
            ("investigate-run", ["agent-label", "investigate-label", "todo-label"]),
            ("unblock", ["agent-label", "blocked-label"]),
            ("stale-sweep", ["agent-label", "done-label"]),
        ]
        responses = {
            iid: self.provider_result(iid=iid, labels=labels)
            for iid, (_, labels) in enumerate(cases, start=20)
        }

        def response_for(command):
            return responses[int(command[-1].rsplit("/", 1)[1])]

        provider = mock.Mock(side_effect=response_for)
        spawned = []
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
        ):
            dispatcher = RunDispatcher(
                self.store,
                "/runner",
                process_factory=lambda *args, **kwargs: spawned.append((args, kwargs)) or FakeProcess(),
            )
            for iid, (skill, _) in enumerate(cases, start=20):
                run = self.store.enqueue(
                    project="demo",
                    skill=skill,
                    source="scheduled",
                    target=f"https://gitlab.example/crew/demo/-/issues/{iid}",
                )
                dispatcher.drain("demo", {skill: 1})
                self.assertEqual("running", self.store.get(run["run_id"])["state"])
        self.assertEqual(len(cases), len(spawned))
        self.assertEqual(len(cases), provider.call_count)

    def test_default_validator_requires_gitlab_forge_and_tracker(self):
        provider = mock.Mock()
        config = self.gitlab_config()
        config["providers"] = {"forge": "github", "tracker": "gitlab"}
        run = self.store.enqueue(
            project="demo",
            skill="implementer-run",
            source="scheduled",
            target="https://gitlab.example/crew/demo/-/issues/7",
        )
        with (
            mock.patch.object(dispatcher_module, "load_runtime_config", return_value=config),
            mock.patch.object(dispatcher_module, "default_provider_run", provider, create=True),
        ):
            dispatcher = RunDispatcher(self.store, "/runner")
            dispatcher.drain("demo", {"implementer-run": 1})
        self.assertEqual(
            ("failed", "validation_failed"),
            (self.store.get(run["run_id"])["state"], self.store.get(run["run_id"])["error_code"]),
        )
        provider.assert_not_called()

    def test_false_target_is_cancelled_and_next_run_starts(self):
        stale = self.enqueue("stale")
        good = self.enqueue("good")
        self.dispatcher.target_validator = lambda row: row["target"] != "stale"
        result = self.dispatcher.drain("demo", {"qa-run": 1})
        self.assertEqual("cancelled", self.store.get(stale["run_id"])["state"])
        self.assertEqual([good["run_id"]], [row["run_id"] for row in result["spawned"]])

    def test_spawn_error_fails_and_continues(self):
        first, second = self.enqueue("one"), self.enqueue("two")
        calls = 0
        def popen(*args, **kwargs):
            nonlocal calls; calls += 1
            if calls == 1: raise OSError("nope")
            return FakeProcess(99)
        dispatcher = RunDispatcher(
            self.store,
            "/runner",
            process_factory=popen,
            target_validator=lambda row: True,
        )
        dispatcher.drain("demo", {"qa-run": 1})
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
        result = RunDispatcher(
            store,
            "/runner",
            process_factory=lambda *a, **k: calls.append((a,k)) or FakeProcess(),
            target_validator=lambda row: True,
        ).reconcile_and_drain("demo", {"qa-run": 1})
        self.assertEqual([dead["run_id"]], [row["run_id"] for row in result["reconciled"]]); self.assertEqual(1, result["purged"])
        self.assertEqual([queued["run_id"]], [row["run_id"] for row in result["spawned"]])

    def test_cli_enqueue_bind_and_drain_contracts(self):
        class NoSpawn(RunDispatcher):
            def __init__(self, store, runner): super().__init__(
                store,
                runner,
                process_factory=lambda *a, **k: FakeProcess(),
                target_validator=lambda row: True,
            )
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

    def test_cli_argument_errors_are_one_safe_line(self):
        error = StringIO()
        with mock.patch("sys.stderr", error):
            self.assertEqual(2, dispatcher_module.main(["enqueue", "--skill", "qa-run"]))
        self.assertEqual("pitcrew dispatcher: invalid arguments\n", error.getvalue())

    def test_cli_real_runtime_and_global_stop(self):
        root = Path(self.temp.name).resolve() / "codex"
        env = {**os.environ, "CODEX_HOME": str(root), "HOME": self.temp.name}
        configured = subprocess.run([str(Path(__file__).resolve().parents[1] / "bin/configure.sh"), "getbill", "--profile", "getbill"], env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(0, configured.returncode, configured.stderr)
        with mock.patch.dict(os.environ, env, clear=False):
            store, capacity = dispatcher_module._runtime("getbill", "implementer-run")
            self.assertEqual(3, capacity["implementer-run"])
            class NoSpawn(RunDispatcher):
                def __init__(self, store, runner): super().__init__(
                    store,
                    runner,
                    process_factory=lambda *a, **k: FakeProcess(),
                    target_validator=lambda row: True,
                )
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
        dispatcher = RunDispatcher(
            self.store,
            "/runner",
            process_factory=popen,
            target_validator=lambda row: True,
            killpg=lambda *value: signals.append(value),
            sleep=lambda _: None,
        )
        self.assertEqual([], dispatcher.drain("demo", {"qa-run": 1})["spawned"])
        self.assertEqual([(456, __import__("signal").SIGTERM), (456, __import__("signal").SIGKILL)], signals)
        self.assertTrue(process.waited); self.assertEqual("cancelled", self.store.get(run["run_id"])["state"])
        self.store.mark_pid = original

    def test_bind_target_requires_project(self):
        run = self.store.enqueue(project="other", skill="qa-run", source="scheduled")
        with self.assertRaises(ValueError): self.dispatcher.bind_target("demo", run["run_id"], "ABC-2")
        self.assertEqual("ABC-2", self.dispatcher.bind_target("other", run["run_id"], "ABC-2")["target"])

    def test_stopped_project_keeps_queued_work_and_rejects_racing_admission(self):
        queued = self.enqueue("queued")
        self.store.set_project_state("demo", "stopped")
        with self.assertRaises(RunPaused):
            self.enqueue("racing-ticket")
        self.assertEqual("queued", self.store.get(queued["run_id"])["state"])
        self.assertEqual([], self.dispatcher.cancel_running("demo", grace_seconds=0))

    def test_stop_between_claim_and_mark_pid_terminates_spawned_worker(self):
        run = self.enqueue("race")
        signals = []
        after_stop_reread = threading.Event()
        allow_stop_finish = threading.Event()
        worker_started = threading.Event()
        original_get = self.store.get

        def get_after_stop_reread(run_id):
            row = original_get(run_id)
            if row is not None and row["cancel_requested"]:
                after_stop_reread.set()
                self.assertTrue(allow_stop_finish.wait(timeout=2))
            return row

        self.store.get = get_after_stop_reread

        def popen(*args, **kwargs):
            # The stopper has already reread pid=None and is paused before
            # finish. Popen/mark_pid now takes the formerly leaky path.
            worker_started.set()
            self.assertTrue(after_stop_reread.wait(timeout=2))
            return FakeProcess(456)

        dispatcher = RunDispatcher(
            self.store,
            "/runner",
            process_factory=popen,
            target_validator=lambda row: True,
            killpg=lambda pid, sig: signals.append((pid, sig)),
            sleep=lambda _: None,
        )
        worker_result = []
        worker = threading.Thread(
            target=lambda: worker_result.append(dispatcher.drain("demo", {"qa-run": 1})),
        )
        worker.start()
        self.assertTrue(worker_started.wait(timeout=2))
        stopper = threading.Thread(
            target=lambda: dispatcher.cancel_running("demo", grace_seconds=0),
        )
        stopper.start()
        self.assertTrue(after_stop_reread.wait(timeout=2))
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        allow_stop_finish.set()
        stopper.join(timeout=2)
        self.assertFalse(stopper.is_alive())
        self.assertEqual([], worker_result[0]["spawned"])
        self.assertEqual(
            [(456, __import__("signal").SIGTERM), (456, __import__("signal").SIGKILL)],
            signals,
        )
        self.assertEqual(1, self.store.get(run["run_id"])["cancel_requested"])
