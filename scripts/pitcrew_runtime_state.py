#!/usr/bin/env python3
"""Read and update the per-project global execution state."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping

try:
    from scripts.pitcrew_config import ConfigError, runtime_root
except ModuleNotFoundError:
    from pitcrew_config import ConfigError, runtime_root


PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STATE_FILENAME = "execution-state.json"
STATES = {"running", "stopped"}


def validated_project(value: str) -> str:
    if not PROJECT_NAME.fullmatch(value):
        raise ValueError("project must match ^[a-z0-9][a-z0-9_-]*$")
    return value


def project_directory(project: str, env: Mapping[str, str] | None = None) -> Path:
    validated_project(project)
    root = runtime_root(env)
    if root.is_symlink():
        raise ConfigError(f"runtime root must not be a symlink: {root}")
    directory = root / project
    if directory.is_symlink():
        raise ConfigError(f"project directory must not be a symlink: {directory}")
    return directory


def state_path(project: str, env: Mapping[str, str] | None = None) -> Path:
    return project_directory(project, env) / STATE_FILENAME


def read_state(project: str, env: Mapping[str, str] | None = None) -> str:
    path = state_path(project, env)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "running"
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError("execution state is unreadable") from error
    if not isinstance(value, dict) or value.get("state") not in STATES:
        raise ConfigError("execution state is invalid")
    return str(value["state"])


def write_state(
    project: str,
    state: str,
    env: Mapping[str, str] | None = None,
) -> None:
    if state not in STATES:
        raise ValueError("state must be running or stopped")
    directory = project_directory(project, env)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ConfigError(f"project directory must not be a symlink: {directory}")
    path = directory / STATE_FILENAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATE_FILENAME}.", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"state": state}, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if path.is_symlink():
            raise ConfigError(f"refusing to overwrite symlink {path}")
        os.replace(temporary, path)
        # Replacing the file alone is not durable until its containing
        # directory has reached stable storage as well.
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage Pitcrew project execution state")
    result.add_argument("command", choices=("status", "stop", "resume"))
    result.add_argument("--project", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "status":
            print(read_state(args.project))
        else:
            state = "stopped" if args.command == "stop" else "running"
            write_state(args.project, state)
            print(state)
    except (ConfigError, OSError, ValueError) as error:
        print(error, file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
