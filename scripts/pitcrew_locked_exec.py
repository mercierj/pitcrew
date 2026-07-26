#!/usr/bin/env python3
"""Run one command under a non-blocking, crash-safe POSIX file lock."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pitcrew_history import HistoryStore
from pitcrew_run_store import RunStateError, RunStore, RunStoreError


MAX_SUMMARY_BYTES = 64 * 1024
MAX_EVENT_BYTES = 1024 * 1024
NO_SUMMARY = "No bounded final summary was produced."
STRUCTURED_STATUSES = {"success", "noop", "blocked", "failed"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--lock-file", required=True, type=Path)
    result.add_argument("--project", required=True)
    result.add_argument("--skill", required=True)
    result.add_argument("--model", required=True)
    result.add_argument(
        "--reasoning-effort",
        required=True,
        choices=("low", "medium", "high", "xhigh"),
    )
    result.add_argument(
        "--routing-mode",
        required=True,
        choices=("fixed", "observe"),
    )
    result.add_argument("--candidate-model")
    result.add_argument("--routing-reason")
    result.add_argument("--target-id")
    result.add_argument("--gate-decision")
    result.add_argument("--gate-reason")
    result.add_argument("--require-structured-result", action="store_true")
    result.add_argument("--summary-file", required=True, type=Path)
    result.add_argument("--history-file", required=True, type=Path)
    result.add_argument("--live-file", type=Path)
    result.add_argument("--run-db", type=Path)
    result.add_argument("--run-id")
    result.add_argument("--heartbeat-seconds", type=float, default=2)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_summary(path: Path) -> str:
    try:
        with path.open("rb") as summary:
            contents = summary.read(MAX_SUMMARY_BYTES)
    except FileNotFoundError:
        return NO_SUMMARY
    decoded = contents.decode("utf-8", errors="replace").strip()
    return decoded or NO_SUMMARY


def parse_structured_result(summary: str) -> dict | None:
    try:
        value = json.loads(summary)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in STRUCTURED_STATUSES:
        return None
    return value


def normalized_outcome(
    return_code: int,
    structured_result: dict | None,
    *,
    required: bool,
) -> str:
    if return_code < 0:
        return "interrupted"
    if return_code != 0:
        return "failed"
    if structured_result is None:
        return "failed" if required else "success"
    if structured_result["status"] in {"noop", "blocked"}:
        return "noop"
    return structured_result["status"]


def invocation_metadata(args: argparse.Namespace, model_invoked: bool) -> dict:
    record = {
        "model": args.model,
        "model_invoked": model_invoked,
        "reasoning_effort": args.reasoning_effort,
        "routing_mode": args.routing_mode,
    }
    for argument, field in (
        ("candidate_model", "candidate_model"),
        ("routing_reason", "routing_reason"),
        ("target_id", "target_id"),
        ("gate_decision", "gate_decision"),
        ("gate_reason", "gate_reason"),
    ):
        value = getattr(args, argument)
        if value is not None:
            record[field] = value
    return record


def structured_metadata(result: dict | None) -> dict:
    if result is None:
        return {}
    metadata = {}
    for field in ("target_id", "work_kind", "quality_outcome"):
        value = result.get(field)
        if isinstance(value, str) and value:
            metadata[field] = value
    if isinstance(result.get("did_work"), bool):
        metadata["did_work"] = result["did_work"]
    return metadata


def write_fallback_summary(path: Path, exit_code: int | None) -> None:
    """Leave an inspectable result when the bounded child produced no summary."""
    try:
        if path.exists() and path.stat().st_size > 0:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "bounded command exited without producing a final summary",
                    "exit_code": exit_code,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError:
        # History still records NO_SUMMARY; finalization must not mask the child result.
        return


def write_live_status(path: Path | None, status: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(status, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def clear_live_status(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A stale live marker must never hide the bounded command result.
            return


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)


def normalize_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    normalized = {}
    for field in USAGE_FIELDS:
        token_count = value.get(field, 0)
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
            return None
        normalized[field] = token_count
    if normalized["total_tokens"] == 0:
        normalized["total_tokens"] = sum(normalized[field] for field in USAGE_FIELDS[:-1])
    return normalized


def parse_usage_event(line: bytes) -> dict[str, int] | None:
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    return normalize_usage(event.get("usage"))


def drain_child_output(child: subprocess.Popen[str], tick=None, heartbeat_seconds: float = 2) -> dict[str, int] | None:
    if child.stdout is None:
        return None
    descriptor = child.stdout.fileno()
    os.set_blocking(descriptor, False)
    usage = None
    buffered = bytearray()
    discarding = False

    def consume(data: bytes) -> None:
        nonlocal usage, discarding
        while data:
            if discarding:
                newline = data.find(b"\n")
                if newline < 0:
                    return
                discarding = False
                data = data[newline + 1 :]
                continue
            newline = data.find(b"\n")
            fragment = data if newline < 0 else data[:newline]
            if len(buffered) + len(fragment) > MAX_EVENT_BYTES:
                buffered.clear()
                discarding = newline < 0
            elif newline < 0:
                buffered.extend(fragment)
            else:
                buffered.extend(fragment)
                parsed = parse_usage_event(bytes(buffered))
                if parsed is not None:
                    usage = parsed
                buffered.clear()
            if newline < 0:
                return
            data = data[newline + 1 :]

    def drain_available(deadline: float | None = None) -> bool:
        read_any = False
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return read_any
            try:
                data = os.read(descriptor, 64 * 1024)
            except BlockingIOError:
                return read_any
            if not data:
                return read_any
            read_any = True
            consume(data)

    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_READ)
        next_heartbeat = time.monotonic() + heartbeat_seconds
        while child.poll() is None:
            if tick is not None and time.monotonic() >= next_heartbeat:
                tick()
                next_heartbeat = time.monotonic() + heartbeat_seconds
            if selector.select(timeout=0.1):
                drain_available()
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            if drain_available(deadline):
                continue
            if not selector.select(timeout=max(0, deadline - time.monotonic())):
                break
    child.stdout.close()
    return usage


def main() -> int:
    argument_parser = parser()
    args = argument_parser.parse_args()
    if bool(args.run_db) != bool(args.run_id):
        argument_parser.error("--run-db and --run-id must be used together")
    if args.heartbeat_seconds <= 0:
        argument_parser.error("--heartbeat-seconds must be positive")
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("pitcrew lock: command is required", file=sys.stderr)
        return 2

    coordinated_store = None
    coordinated_run = None
    if args.run_db:
        try:
            coordinated_store = RunStore(args.run_db)
            coordinated_run = coordinated_store.get(args.run_id)
            if (coordinated_run is None or coordinated_run["project"] != args.project
                    or coordinated_run["skill"] != args.skill or coordinated_run["state"] != "running"):
                print("pitcrew lock: coordinated run is unavailable", file=sys.stderr)
                return 2
        except RunStoreError:
            print("pitcrew lock: coordinated run is unavailable", file=sys.stderr)
            return 2
    started_at = utc_now()
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(args.lock_file, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+"):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            reason = f"run {args.run_id} already running" if args.run_id else f"{args.skill} already running"
            HistoryStore(args.history_file).append(
                {
                    "project": args.project,
                    "skill": args.skill,
                    **invocation_metadata(args, False),
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "duration_ms": 0,
                    "outcome": "noop",
                    "exit_code": 0,
                    "summary": json.dumps({"reason": reason}, separators=(",", ":")),
                }
            )
            print(
                json.dumps(
                    {
                        "status": "noop",
                        "reason": reason,
                        "project": args.project,
                        "skill": args.skill,
                        "next_action": "run again after the configured interval",
                    },
                    separators=(",", ":"),
                )
            )
            return 0

        started_monotonic = time.monotonic_ns()
        write_live_status(
            args.live_file,
            {
                "project": args.project,
                "skill": args.skill,
                "model": args.model,
                "started_at": started_at,
                "pid": os.getpid(),
                "phase": "Exécution du passage courant",
                **({"run_id": args.run_id} if args.run_id else {}),
            },
        )
        try:
            if coordinated_store is not None:
                try:
                    if coordinated_run is not None and coordinated_run["pid"] is None:
                        coordinated_store.mark_pid(args.run_id, os.getpid())
                except RunStateError:
                    clear_live_status(args.live_file)
                    return 0
                except RunStoreError:
                    clear_live_status(args.live_file)
                    print("pitcrew lock: run store is unavailable", file=sys.stderr)
                    return 2
            args.summary_file.unlink(missing_ok=True)
            os.set_inheritable(descriptor, True)
            child = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                pass_fds=(descriptor,),
                start_new_session=True,
            )
        except OSError:
            clear_live_status(args.live_file)
            if coordinated_store is not None:
                try:
                    coordinated_store.finish(args.run_id, state="failed", error_code="spawn_failed", error_message="worker could not be started")
                except RunStateError:
                    pass
                except RunStoreError:
                    HistoryStore(args.history_file).append({"project": args.project, "skill": args.skill, **invocation_metadata(args, True), "started_at": started_at, "finished_at": utc_now(), "duration_ms": 0, "outcome": "failed", "exit_code": None, "summary": NO_SUMMARY})
                    print("pitcrew lock: run store is unavailable", file=sys.stderr)
                    return 2
            HistoryStore(args.history_file).append(
                {
                    "project": args.project,
                    "skill": args.skill,
                    **invocation_metadata(args, True),
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "duration_ms": (
                        time.monotonic_ns() - started_monotonic
                    )
                    // 1_000_000,
                    "outcome": "failed",
                    "exit_code": None,
                    "summary": NO_SUMMARY,
                }
            )
            print("pitcrew lock: failed to launch command", file=sys.stderr)
            return 127

        def forward(signum: int, _frame: object) -> None:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signum)
                except OSError:
                    pass

        previous = {
            signum: signal.signal(signum, forward)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            def heartbeat() -> None:
                if coordinated_store is not None:
                    coordinated_store.heartbeat(args.run_id, "Exécution du passage courant")
            try:
                usage = drain_child_output(child, heartbeat if coordinated_store is not None else None, args.heartbeat_seconds)
            except RunStoreError:
                if child.poll() is None:
                    try: os.killpg(child.pid, signal.SIGTERM)
                    except OSError: pass
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except OSError: pass
                    child.wait()
                if coordinated_store is not None:
                    try: coordinated_store.finish(args.run_id, state="failed", error_code="store_unavailable", error_message="run store is unavailable")
                    except (RunStateError, RunStoreError): pass
                clear_live_status(args.live_file)
                print("pitcrew lock: run store is unavailable", file=sys.stderr)
                return 2
            return_code = child.wait()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

        summary = read_summary(args.summary_file)
        structured_result = parse_structured_result(summary)
        outcome = normalized_outcome(
            return_code,
            structured_result,
            required=args.require_structured_result,
        )
        try:
            write_fallback_summary(args.summary_file, return_code)
            if coordinated_store is not None:
                try:
                    if outcome in {"success", "noop"}:
                        coordinated_store.finish(args.run_id, state="succeeded")
                    else:
                        coordinated_store.finish(args.run_id, state="failed", error_code="command_failed", error_message="worker command did not complete")
                except RunStateError:
                    pass
                except RunStoreError:
                    clear_live_status(args.live_file)
                    HistoryStore(args.history_file).append({"project": args.project, "skill": args.skill, **invocation_metadata(args, True), "started_at": started_at, "finished_at": utc_now(), "duration_ms": (time.monotonic_ns() - started_monotonic) // 1_000_000, "outcome": "failed", "exit_code": return_code, "summary": read_summary(args.summary_file), **structured_metadata(structured_result)})
                    print("pitcrew lock: run store is unavailable", file=sys.stderr)
                    return 2
            HistoryStore(args.history_file).append(
                {
                    "project": args.project,
                    "skill": args.skill,
                    **invocation_metadata(args, True),
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "duration_ms": (time.monotonic_ns() - started_monotonic) // 1_000_000,
                    "outcome": outcome,
                    "exit_code": return_code,
                    "summary": read_summary(args.summary_file),
                    **({"usage": usage} if usage is not None else {}),
                    **structured_metadata(structured_result),
                }
            )
        finally:
            clear_live_status(args.live_file)
        return return_code


if __name__ == "__main__":
    raise SystemExit(main())
