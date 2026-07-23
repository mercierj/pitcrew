#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


FORGES = {"github", "gitlab"}
TRACKERS = {"linear", "github", "gitlab", "none"}
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(ValueError):
    pass


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    codex_home = values.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "pitcrew"
    home = values.get("HOME")
    if not home:
        raise ConfigError("HOME or CODEX_HOME is required")
    return Path(home).expanduser() / ".codex" / "pitcrew"


def load_profile(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate(value)
    return dict(value)


def _mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} must be an object")
    return value


def validate(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise ConfigError("config must be an object")
    providers = _mapping(config, "providers")
    if providers.get("forge") not in FORGES:
        raise ConfigError("providers.forge must be github or gitlab")
    if providers.get("tracker") not in TRACKERS:
        raise ConfigError("providers.tracker is unsupported")

    if config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    project_name = config.get("project_name")
    if not isinstance(project_name, str) or not PROJECT_NAME.fullmatch(project_name):
        raise ConfigError("project_name must match ^[a-z0-9][a-z0-9_-]*$")

    if not isinstance(config.get("repos"), list):
        raise ConfigError("repos must be an array")
    release = config.get("release", {})
    if not isinstance(release, Mapping):
        raise ConfigError("release must be an object")
    if release.get("autonomy", "off") not in {"off", "prepare", "dev", "full"}:
        raise ConfigError("release.autonomy is unsupported")
    safety = config.get("safety", {})
    if not isinstance(safety, Mapping):
        raise ConfigError("safety must be an object")
    remote_actions = safety.get("confirm_each_remote_action", [])
    if not isinstance(remote_actions, list):
        raise ConfigError("safety.confirm_each_remote_action must be an array")
    for key in (
        "allow_database_writes",
        "allow_destructive_git",
        "allow_secret_reads",
    ):
        if safety.get(key, False) is not False:
            raise ConfigError(f"safety.{key} must default to false")


def write_project(
    profile_path: Path, project: str, env: Mapping[str, str] | None = None
) -> Path:
    profile = load_profile(profile_path)
    profile["project_name"] = project
    validate(profile)
    root = runtime_root(env)
    serialized = json.dumps(profile, indent=2) + "\n"
    if root.is_symlink():
        raise ConfigError(f"runtime root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ConfigError(f"runtime root must not be a symlink: {root}")
    project_directory = root / project
    if project_directory.is_symlink():
        raise ConfigError(f"project directory must not be a symlink: {project_directory}")
    project_directory.mkdir(parents=True, exist_ok=True)
    if project_directory.is_symlink():
        raise ConfigError(f"project directory must not be a symlink: {project_directory}")
    default_file = root / "default.txt"
    if default_file.is_symlink():
        raise ConfigError(f"refusing to overwrite symlink {default_file}")
    destination = project_directory / "config.json"
    if destination.is_symlink() or destination.exists():
        raise ConfigError(f"refusing to overwrite {destination}")
    created_inode: tuple[int, int] | None = None
    try:
        with destination.open("x", encoding="utf-8") as config_file:
            stat = destination.stat(follow_symlinks=False)
            created_inode = (stat.st_dev, stat.st_ino)
            config_file.write(serialized)
    except FileExistsError as error:
        raise ConfigError(f"refusing to overwrite {destination}") from error
    except OSError:
        if created_inode is not None:
            try:
                stat = destination.stat(follow_symlinks=False)
                if (stat.st_dev, stat.st_ino) == created_inode:
                    destination.unlink()
            except OSError:
                pass
        raise
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".default-", dir=root)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as default_handle:
            default_handle.write(project + "\n")
        if root.is_symlink() or default_file.is_symlink():
            raise ConfigError(f"refusing to overwrite symlink {default_file}")
        os.replace(temporary, default_file)
        temporary = None
    except (OSError, ConfigError):
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--profile", choices=("generic", "getbill"), required=True)
    init_parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.command == "validate":
        validate(json.loads(args.config.read_text(encoding="utf-8")))
        print(f"Valid config: {args.config}")
        return 0
    destination = write_project(root / "profiles" / f"{args.profile}.json", args.project)
    print(f"Created {destination}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (json.JSONDecodeError, OSError, UnicodeError, ConfigError) as error:
        print(f"pitcrew-config: {error}", file=sys.stderr)
        raise SystemExit(1)
