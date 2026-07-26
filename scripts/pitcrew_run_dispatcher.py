#!/usr/bin/env python3
"""Claim queued Pitcrew runs and start their isolated worker processes."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

try:
    from scripts.pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from scripts.pitcrew_run_store import RunStateError, RunStore, RunStoreError
    from scripts.pitcrew_runtime_state import read_state
except ImportError:
    from pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from pitcrew_run_store import RunStateError, RunStore, RunStoreError
    from pitcrew_runtime_state import read_state


PROVIDER_TIMEOUT_SECONDS = 15
MAX_PROVIDER_ERROR_CHARS = 4096
MAX_CLAIMS_PER_DRAIN = 100
HTTP_STATUS_SUFFIX = re.compile(r"\(HTTP ([1-5][0-9]{2})\)\s*\Z")


class TargetValidationUnavailable(Exception):
    pass


def default_provider_run(command: list[str]) -> subprocess.CompletedProcess[str]:
    invocation = list(command)
    if invocation[:1] == ["glab"]:
        invocation[0] = os.environ.get("GLAB_BIN", "glab")
    return subprocess.run(
        invocation,
        text=True,
        capture_output=True,
        check=False,
        timeout=PROVIDER_TIMEOUT_SECONDS,
    )


def _provider_http_status(completed: subprocess.CompletedProcess[str]) -> int | None:
    stderr = completed.stderr
    if not isinstance(stderr, str) or len(stderr) > MAX_PROVIDER_ERROR_CHARS:
        return None
    match = HTTP_STATUS_SUFFIX.search(stderr)
    return int(match.group(1)) if match is not None else None


def _required_labels(skill: str, tracker: Mapping[str, Any]) -> tuple[frozenset[str], ...] | None:
    labels = tracker.get("labels")
    states = tracker.get("states")
    if not isinstance(labels, Mapping) or not isinstance(states, Mapping):
        raise TargetValidationUnavailable("target validation is unavailable")
    try:
        agent = labels["agent"]
        investigate = labels["investigate"]
        todo = states["todo"]
        review = states["review"]
        blocked = states["blocked"]
        done = states["done"]
    except KeyError as error:
        raise TargetValidationUnavailable("target validation is unavailable") from error
    values = (agent, investigate, todo, review, blocked, done)
    if any(not isinstance(value, str) or not value for value in values):
        raise TargetValidationUnavailable("target validation is unavailable")
    roles = {
        "implementer-run": (frozenset((agent, todo)), frozenset((agent, review))),
        "validator-run": (frozenset((agent, review)),),
        "reviewer-run": (frozenset((agent, review)),),
        "investigate-run": (frozenset((agent, investigate, todo)),),
        "unblock": (frozenset((agent, blocked)),),
        "stale-sweep": (frozenset((agent, done)),),
    }
    return roles.get(skill)


def validate_queued_target(
    row: dict[str, Any],
    provider_run: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> bool:
    config = load_runtime_config(row["project"])
    providers = config.get("providers")
    gitlab = config.get("gitlab")
    if (
        not isinstance(providers, Mapping)
        or providers.get("forge") != "gitlab"
        or providers.get("tracker") != "gitlab"
        or not isinstance(gitlab, Mapping)
    ):
        raise TargetValidationUnavailable("target validation is unavailable")
    host = gitlab.get("host")
    project_path = gitlab.get("project_path")
    project_id = gitlab.get("project_id")
    tracker = gitlab.get("tracker")
    if (
        not isinstance(host, str)
        or not host
        or not isinstance(project_path, str)
        or not project_path
        or not isinstance(project_id, int)
        or isinstance(project_id, bool)
        or project_id <= 0
        or not isinstance(tracker, Mapping)
    ):
        raise TargetValidationUnavailable("target validation is unavailable")
    required = _required_labels(str(row["skill"]), tracker)
    if required is None:
        return False

    target = row.get("target")
    if not isinstance(target, str):
        return False
    parsed = urlsplit(target)
    prefix = f"/{project_path}/-/issues/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != host
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(prefix)
    ):
        return False
    raw_iid = parsed.path.removeprefix(prefix)
    if not raw_iid.isdigit() or int(raw_iid) <= 0:
        return False
    iid = int(raw_iid)
    endpoint = f"projects/{quote(str(project_id), safe='')}/issues/{iid}"
    try:
        completed = provider_run(["glab", "api", "--hostname", host, endpoint])
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TargetValidationUnavailable("target validation is unavailable") from error
    if completed.returncode != 0:
        if _provider_http_status(completed) == 404:
            return False
        raise TargetValidationUnavailable("target validation is unavailable")
    try:
        issue = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise TargetValidationUnavailable("target validation is unavailable") from error
    if not isinstance(issue, dict):
        raise TargetValidationUnavailable("target validation is unavailable")
    response_iid = issue.get("iid")
    state = issue.get("state")
    issue_labels = issue.get("labels")
    if (
        not isinstance(response_iid, int)
        or isinstance(response_iid, bool)
        or response_iid != iid
        or not isinstance(state, str)
        or not isinstance(issue_labels, list)
        or any(not isinstance(label, str) for label in issue_labels)
    ):
        raise TargetValidationUnavailable("target validation is unavailable")
    if state != "opened":
        return False
    actual = frozenset(issue_labels)
    return any(expected.issubset(actual) for expected in required)


class RunDispatcher:
    def __init__(self, store: RunStore, runner: str, process_factory: Callable[..., Any] = subprocess.Popen,
                 target_validator: Callable[[dict[str, Any]], bool] | None = None,
                 killpg: Callable[[int, int], None] = os.killpg, sleep: Callable[[float], None] = time.sleep,
                 provider_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
                 max_claims_per_drain: int = MAX_CLAIMS_PER_DRAIN):
        if (
            not isinstance(max_claims_per_drain, int)
            or isinstance(max_claims_per_drain, bool)
            or max_claims_per_drain <= 0
        ):
            raise ValueError("max_claims_per_drain must be a positive integer")
        self.store, self.runner, self.process_factory = store, runner, process_factory
        self.max_claims_per_drain = max_claims_per_drain
        if target_validator is None:
            provider_run = provider_runner if provider_runner is not None else default_provider_run
            target_validator = lambda row: validate_queued_target(row, provider_run)
        self.target_validator, self.killpg, self.sleep = target_validator, killpg, sleep

    def drain(self, project: str, capacities: Mapping[str, int]) -> dict[str, list[dict[str, Any]]]:
        claimed: list[dict[str, Any]] = []
        spawned: list[dict[str, Any]] = []; cancelled: list[dict[str, Any]] = []; failed: list[dict[str, Any]] = []
        for _claim_number in range(self.max_claims_per_drain):
            ready = self.store.claim_ready(project=project, capacities=capacities)
            if not ready:
                break
            claimed.extend(ready)
            for row in ready:
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
                        marked = self.store.mark_pid(row["run_id"], process.pid)
                        # A global stop may request cancellation after claim
                        # but before Popen/mark_pid.  In that interleaving its
                        # initial PID snapshot is empty, so the spawning side
                        # must terminate the just-recorded process itself.
                        if marked["cancel_requested"]:
                            try: self.killpg(process.pid, signal.SIGTERM)
                            except OSError: pass
                            self.sleep(0.1)
                            try: self.killpg(process.pid, signal.SIGKILL)
                            except OSError: pass
                            try: process.wait(timeout=1)
                            except Exception: pass
                        else:
                            spawned.append(marked)
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
        candidate = dict(row)
        candidate["target"] = target
        try:
            valid = bool(self.target_validator(candidate))
        except Exception as error:
            raise RunStoreError("target validation is unavailable") from error
        if not valid:
            raise RunStateError("target is stale or ineligible")
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


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid arguments")


def main(argv: list[str] | None = None, runtime_factory: Callable[[str, str | None], tuple[RunStore, dict[str, int]]] = _runtime,
         dispatcher_factory: Callable[[RunStore, str], RunDispatcher] = RunDispatcher) -> int:
    parser = SafeArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue"); enqueue.add_argument("--project", required=True); enqueue.add_argument("--skill", required=True); enqueue.add_argument("--target")
    bind = commands.add_parser("bind-target"); bind.add_argument("--project", required=True); bind.add_argument("--run-id", required=True); bind.add_argument("--target", required=True)
    drain = commands.add_parser("drain"); drain.add_argument("--project", required=True)
    try:
        args = parser.parse_args(argv)
        store, capacities = runtime_factory(args.project, getattr(args, "skill", None))
        dispatcher = dispatcher_factory(store, str(Path(__file__).resolve().parents[1] / "bin" / "pitcrew-codex.sh"))
        if args.command == "enqueue":
            row = store.enqueue(project=args.project, skill=args.skill, source="scheduled", target=args.target)
            dispatcher.reconcile_and_drain(args.project, capacities)
            latest = store.get(row["run_id"]) or row
            print(json.dumps({"run_id": latest["run_id"], "state": latest["state"],
                              "queue_position": latest["queue_position"], "created": row["created"]}, separators=(",", ":")))
        elif args.command == "bind-target":
            # Reconciliation never claims work here, but makes stale workers
            # terminal before a target is attached to a queued run.
            store.reconcile(args.project)
            print(json.dumps(dispatcher.bind_target(args.project, args.run_id, args.target), separators=(",", ":")))
        else:
            print(json.dumps(dispatcher.reconcile_and_drain(args.project, capacities), separators=(",", ":")))
        return 0
    except (RunStoreError, ConfigError, ValueError, OSError) as error:
        print(f"pitcrew dispatcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
