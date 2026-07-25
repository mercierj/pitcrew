#!/usr/bin/env python3
"""Claim queued Pitcrew runs and start their isolated worker processes."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from scripts.pitcrew_run_store import RunStateError, RunStore, RunStoreError
    from scripts.pitcrew_runtime_state import read_state
except ImportError:
    from pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from pitcrew_run_store import RunStateError, RunStore, RunStoreError
    from pitcrew_runtime_state import read_state


class RunDispatcher:
    def __init__(self, store: RunStore, runner: str, process_factory: Callable[..., Any] = subprocess.Popen,
                 target_validator: Callable[[dict[str, Any]], bool] | None = None,
                 killpg: Callable[[int, int], None] = os.killpg, sleep: Callable[[float], None] = time.sleep):
        self.store, self.runner, self.process_factory = store, runner, process_factory
        self.target_validator, self.killpg, self.sleep = target_validator, killpg, sleep

    def drain(self, project: str, capacities: Mapping[str, int]) -> dict[str, list[dict[str, Any]]]:
        claimed = self.store.claim_ready(project=project, capacities=capacities)
        spawned: list[dict[str, Any]] = []; cancelled: list[dict[str, Any]] = []; failed: list[dict[str, Any]] = []
        for row in claimed:
            if row["target"] is not None and self.target_validator is not None:
                try:
                    valid = bool(self.target_validator(row))
                except Exception:
                    failed.append(self.store.finish(row["run_id"], state="failed", error_code="validation_failed", error_message="target validation is unavailable"))
                    continue
                if not valid:
                    cancelled.append(self.store.finish(row["run_id"], state="cancelled", error_code="stale_target", error_message="target is no longer eligible"))
                    continue
            args = [self.runner, row["skill"], project]
            if row["target"] is not None: args += ["--target", row["target"]]
            args += ["--scheduled", "--coordinated-run", row["run_id"]]
            try:
                process = self.process_factory(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    spawned.append(self.store.mark_pid(row["run_id"], process.pid))
                except (RunStateError, RunStoreError):
                    try: self.killpg(process.pid, signal.SIGTERM)
                    except OSError: pass
                    self.sleep(0.1)
                    try: self.killpg(process.pid, signal.SIGKILL)
                    except OSError: pass
                    try: process.wait(timeout=1)
                    except Exception: pass
            except OSError:
                failed.append(self.store.finish(row["run_id"], state="failed", error_code="spawn_failed", error_message="worker could not be started"))
        return {"claimed": claimed, "spawned": spawned, "cancelled": cancelled, "failed": failed}

    def reconcile_and_drain(self, project: str, capacities: Mapping[str, int]) -> dict[str, Any]:
        reconciled = self.store.reconcile(project)
        purged = self.store.purge(project)
        result = self.drain(project, capacities)
        return {"reconciled": reconciled, "purged": purged, **result}

    def cancel_running(self, project: str, error_code: str = "global_stop", grace_seconds: float = 0.1) -> list[dict[str, Any]]:
        if not isinstance(grace_seconds, (float, int)) or isinstance(grace_seconds, bool) or grace_seconds < 0:
            raise ValueError("grace_seconds is invalid")
        runs = self.store.request_cancel(project)
        for row in runs:
            if row["pid"] is None: continue
            try: self.killpg(row["pid"], signal.SIGTERM)
            except OSError: pass
        self.sleep(float(grace_seconds))
        finished = []
        for row in runs:
            current = self.store.get(row["run_id"])
            if current is None or current["state"] != "running":
                continue
            if current["pid"] is not None:
                try: self.killpg(current["pid"], signal.SIGKILL)
                except OSError: pass
            try: finished.append(self.store.finish(row["run_id"], state="cancelled", error_code=error_code, error_message="run cancelled"))
            except RunStateError: pass
        return finished

    def bind_target(self, project: str, run_id: str, target: str) -> dict[str, Any]:
        row = self.store.get(run_id)
        if row is None or row["project"] != project:
            raise RunStateError("run does not belong to project")
        return self.store.bind_target(run_id, target)


def _capacities(config: Mapping[str, Any], requested: str | None = None) -> dict[str, int]:
    agents = config.get("agents", {})
    skills = set(agents) if isinstance(agents, Mapping) else set()
    if requested: skills.add(requested)
    return {skill: max_concurrent_for(config, skill) for skill in skills}


def _runtime(project: str, skill: str | None = None) -> tuple[RunStore, dict[str, int]]:
    config = load_runtime_config(project)
    if read_state(project) == "stopped": raise RunStoreError("global stop is active")
    directory = runtime_root() / project
    directory.chmod(0o700)
    return RunStore(directory / "runs.sqlite3"), _capacities(config, skill)


def main(argv: list[str] | None = None, runtime_factory: Callable[[str, str | None], tuple[RunStore, dict[str, int]]] = _runtime,
         dispatcher_factory: Callable[[RunStore, str], RunDispatcher] = RunDispatcher) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue"); enqueue.add_argument("--project", required=True); enqueue.add_argument("--skill", required=True); enqueue.add_argument("--target")
    bind = commands.add_parser("bind-target"); bind.add_argument("--project", required=True); bind.add_argument("--run-id", required=True); bind.add_argument("--target", required=True)
    drain = commands.add_parser("drain"); drain.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        store, capacities = runtime_factory(args.project, getattr(args, "skill", None))
        dispatcher = dispatcher_factory(store, str(Path(__file__).resolve().parents[1] / "bin" / "pitcrew-codex.sh"))
        if args.command == "enqueue":
            row = store.enqueue(project=args.project, skill=args.skill, source="scheduled", target=args.target)
            dispatcher.reconcile_and_drain(args.project, capacities)
            latest = store.get(row["run_id"]) or row
            print(json.dumps({"run_id": latest["run_id"], "state": latest["state"],
                              "queue_position": latest["queue_position"], "created": row["created"]}, separators=(",", ":")))
        elif args.command == "bind-target":
            print(json.dumps(dispatcher.bind_target(args.project, args.run_id, args.target), separators=(",", ":")))
        else:
            print(json.dumps(dispatcher.reconcile_and_drain(args.project, capacities), separators=(",", ":")))
        return 0
    except (RunStoreError, ConfigError, ValueError, OSError) as error:
        print(f"pitcrew dispatcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
