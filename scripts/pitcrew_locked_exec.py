#!/usr/bin/env python3
"""Run one command under a non-blocking, crash-safe POSIX file lock."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--lock-file", required=True, type=Path)
    result.add_argument("--project", required=True)
    result.add_argument("--skill", required=True)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main() -> int:
    args = parser().parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("pitcrew lock: command is required", file=sys.stderr)
        return 2

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(args.lock_file, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+"):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                json.dumps(
                    {
                        "status": "noop",
                        "reason": f"{args.skill} already running",
                        "project": args.project,
                        "skill": args.skill,
                        "next_action": "run again after the configured interval",
                    },
                    separators=(",", ":"),
                )
            )
            return 0

        os.set_inheritable(descriptor, True)
        child = subprocess.Popen(command, pass_fds=(descriptor,))

        def forward(signum: int, _frame: object) -> None:
            if child.poll() is None:
                child.send_signal(signum)

        previous = {
            signum: signal.signal(signum, forward)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            return child.wait()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
