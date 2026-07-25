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


MAX_SUMMARY_BYTES = 64 * 1024
MAX_EVENT_BYTES = 1024 * 1024
NO_SUMMARY = "No bounded final summary was produced."


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--lock-file", required=True, type=Path)
    result.add_argument("--project", required=True)
    result.add_argument("--skill", required=True)
    result.add_argument("--model", required=True)
    result.add_argument("--summary-file", required=True, type=Path)
    result.add_argument("--history-file", required=True, type=Path)
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


def drain_child_output(child: subprocess.Popen[str]) -> dict[str, int] | None:
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
        while child.poll() is None:
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
    args = parser().parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("pitcrew lock: command is required", file=sys.stderr)
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
            reason = f"{args.skill} already running"
            HistoryStore(args.history_file).append(
                {
                    "project": args.project,
                    "skill": args.skill,
                    "model": args.model,
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
        try:
            args.summary_file.unlink(missing_ok=True)
            os.set_inheritable(descriptor, True)
            child = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                pass_fds=(descriptor,),
            )
        except OSError:
            HistoryStore(args.history_file).append(
                {
                    "project": args.project,
                    "skill": args.skill,
                    "model": args.model,
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
                child.send_signal(signum)

        previous = {
            signum: signal.signal(signum, forward)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            usage = drain_child_output(child)
            return_code = child.wait()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

        if return_code == 0:
            outcome = "success"
        elif return_code < 0:
            outcome = "interrupted"
        else:
            outcome = "failed"
        HistoryStore(args.history_file).append(
            {
                "project": args.project,
                "skill": args.skill,
                "model": args.model,
                "started_at": started_at,
                "finished_at": utc_now(),
                "duration_ms": (time.monotonic_ns() - started_monotonic) // 1_000_000,
                "outcome": outcome,
                "exit_code": return_code,
                "summary": read_summary(args.summary_file),
                **({"usage": usage} if usage is not None else {}),
            }
        )
        return return_code


if __name__ == "__main__":
    raise SystemExit(main())
