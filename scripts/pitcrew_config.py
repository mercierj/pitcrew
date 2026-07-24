#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


FORGES = {"github", "gitlab"}
TRACKERS = {"linear", "github", "gitlab", "none"}
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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
    if "gitlab" in {providers.get("forge"), providers.get("tracker")}:
        gitlab = _mapping(config, "gitlab")
        for key in ("host", "user", "owner", "project_path"):
            value = gitlab.get(key)
            if not isinstance(value, str) or not value:
                raise ConfigError(f"gitlab.{key} must be a non-empty string")
        if "/" not in gitlab["project_path"] or "://" in gitlab["project_path"]:
            raise ConfigError("gitlab.project_path must be an owner/project path")
        if not isinstance(gitlab.get("project_id"), int) or gitlab["project_id"] <= 0:
            raise ConfigError("gitlab.project_id must be a positive integer")
        if providers.get("tracker") == "gitlab":
            tracker = gitlab.get("tracker")
            if not isinstance(tracker, Mapping):
                raise ConfigError("gitlab.tracker must be an object")
            labels = tracker.get("labels")
            states = tracker.get("states")
            if not isinstance(labels, Mapping):
                raise ConfigError("gitlab.tracker.labels must be an object")
            if not isinstance(states, Mapping):
                raise ConfigError("gitlab.tracker.states must be an object")
            ticket_prefix = tracker.get("ticket_prefix")
            if not isinstance(ticket_prefix, str) or not ticket_prefix:
                raise ConfigError("gitlab.tracker.ticket_prefix must be a non-empty string")
            assignee_username = tracker.get("assignee_username")
            if assignee_username is not None and (
                not isinstance(assignee_username, str) or not assignee_username
            ):
                raise ConfigError(
                    "gitlab.tracker.assignee_username must be a non-empty string when set"
                )
            for key in ("agent", "investigate", "quick_win", "bug", "improvement"):
                if not isinstance(labels.get(key), str) or not labels[key]:
                    raise ConfigError(f"gitlab.tracker.labels.{key} must be a non-empty string")
            for key in ("todo", "processing", "review", "blocked", "done"):
                if not isinstance(states.get(key), str) or not states[key]:
                    raise ConfigError(f"gitlab.tracker.states.{key} must be a non-empty string")

    if config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    project_name = config.get("project_name")
    if not isinstance(project_name, str) or not PROJECT_NAME.fullmatch(project_name):
        raise ConfigError("project_name must match ^[a-z0-9][a-z0-9_-]*$")

    if not isinstance(config.get("repos"), list):
        raise ConfigError("repos must be an array")
    for index, repository in enumerate(config["repos"]):
        if not isinstance(repository, Mapping):
            raise ConfigError(f"repos[{index}] must be an object")
        for key in ("name", "path"):
            value = repository.get(key)
            if not isinstance(value, str) or not value:
                raise ConfigError(f"repos[{index}].{key} must be a non-empty string")
        if any(unicodedata.category(character).startswith("C") for character in repository["path"]):
            raise ConfigError(f"repos[{index}].path must not contain control characters")
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
    project_fd = os.open(project_directory, DIRECTORY_FLAGS)
    try:
        _write_exclusive_config(project_fd, "config.json", serialized)
    finally:
        os.close(project_fd)
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


def _write_exclusive_config(parent_fd: int, name: str, serialized: str) -> None:
    descriptor: int | None = None
    created_inode: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        stat = os.fstat(descriptor)
        created_inode = (stat.st_dev, stat.st_ino)
        data = serialized.encode("utf-8")
        while data:
            written = os.write(descriptor, data)
            data = data[written:]
    except FileExistsError as error:
        raise ConfigError(f"refusing to overwrite {name}") from error
    except OSError:
        if created_inode is not None:
            try:
                stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (stat.st_dev, stat.st_ino) == created_inode:
                    os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)


def _open_absolute_directory(path: Path, *, create: bool = False) -> int:
    if not path.is_absolute():
        raise ConfigError("directory anchor must be an absolute path")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_descriptor = _open_directory_at(descriptor, component, create=create)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise ConfigError("directory anchor must not be a symlink or missing") from error


def _read_legacy_config(project: str, home: str) -> Mapping[str, Any]:
    home_fd = _open_absolute_directory(Path(home).expanduser())
    descriptors = [home_fd]
    try:
        try:
            for name in (".claude", "agent-loop", project):
                descriptors.append(_open_directory_at(descriptors[-1], name, create=False))
            config_fd = os.open(
                "config.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptors[-1]
            )
            with os.fdopen(config_fd, "r", encoding="utf-8") as config_file:
                legacy = json.load(config_file)
        except OSError as error:
            raise ConfigError("legacy config path must not be a symlink or missing") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if not isinstance(legacy, Mapping):
        raise ConfigError("legacy config must be an object")
    return legacy


def _open_runtime_project(project: str, values: Mapping[str, str]) -> int:
    codex_home = values.get("CODEX_HOME")
    if codex_home:
        base = Path(codex_home).expanduser()
        base_fd = _open_absolute_directory(base, create=True)
        try:
            root_fd = _open_directory_at(base_fd, "pitcrew", create=True)
        finally:
            os.close(base_fd)
    else:
        home = values.get("HOME")
        if not home:
            raise ConfigError("HOME or CODEX_HOME is required")
        home_fd = _open_absolute_directory(Path(home).expanduser())
        try:
            codex_fd = _open_directory_at(home_fd, ".codex", create=True)
        finally:
            os.close(home_fd)
        try:
            root_fd = _open_directory_at(codex_fd, "pitcrew", create=True)
        finally:
            os.close(codex_fd)
    try:
        return _open_directory_at(root_fd, project, create=True)
    finally:
        os.close(root_fd)


def _open_runtime_root_for_read(values: Mapping[str, str]) -> int:
    codex_home = values.get("CODEX_HOME")
    if codex_home:
        anchor = Path(codex_home).expanduser()
        components = ("pitcrew",)
    else:
        home = values.get("HOME")
        if not home:
            raise ConfigError("HOME or CODEX_HOME is required")
        anchor = Path(home).expanduser()
        components = (".codex", "pitcrew")

    descriptor: int | None = None
    try:
        descriptor = _open_absolute_directory(anchor)
        for component in components:
            next_descriptor = _open_directory_at(descriptor, component, create=False)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ConfigError("runtime config path must not be a symlink or missing") from error


def _open_runtime_project_for_read(project: str, values: Mapping[str, str]) -> int:
    if not PROJECT_NAME.fullmatch(project):
        raise ConfigError("project_name must match ^[a-z0-9][a-z0-9_-]*$")
    root_fd = _open_runtime_root_for_read(values)
    try:
        return _open_directory_at(root_fd, project, create=False)
    except OSError as error:
        raise ConfigError("runtime config path must not be a symlink or missing") from error
    finally:
        os.close(root_fd)


def resolve_runtime_project(env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    project = values.get("PITCREW_PROJECT")
    if project is None:
        root_fd = _open_runtime_root_for_read(values)
        try:
            try:
                default_fd = os.open(
                    "default.txt", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd
                )
                with os.fdopen(default_fd, "r", encoding="utf-8") as default_file:
                    project = default_file.read()
            except OSError as error:
                raise ConfigError("runtime default path must not be a symlink or missing") from error
        finally:
            os.close(root_fd)
        if project.endswith("\n"):
            project = project[:-1]
    if not PROJECT_NAME.fullmatch(project):
        raise ConfigError("default project must match ^[a-z0-9][a-z0-9_-]*$")
    project_fd = _open_runtime_project_for_read(project, values)
    os.close(project_fd)
    return project


def load_runtime_config(
    project: str, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    values = os.environ if env is None else env
    project_fd = _open_runtime_project_for_read(project, values)
    try:
        try:
            config_fd = os.open(
                "config.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=project_fd
            )
            with os.fdopen(config_fd, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
        except OSError as error:
            raise ConfigError("runtime config path must not be a symlink or missing") from error
    finally:
        os.close(project_fd)
    validate(config)
    return dict(config)


def configured_repo_path(config: Mapping[str, Any], project: str) -> str:
    repositories = config["repos"]
    selected = next(
        (repository for repository in repositories if repository["name"] == project),
        repositories[0] if repositories else None,
    )
    if selected is None:
        raise ConfigError("repos must contain a configured repository")
    path = selected["path"]
    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    return path


def legacy_config_paths(
    project: str, env: Mapping[str, str] | None = None
) -> tuple[Path, Path]:
    if not isinstance(project, str) or not PROJECT_NAME.fullmatch(project):
        raise ConfigError("project_name must match ^[a-z0-9][a-z0-9_-]*$")
    values = os.environ if env is None else env
    home = values.get("HOME")
    if not home:
        raise ConfigError("HOME is required for legacy migration")
    source = Path(home).expanduser() / ".claude" / "agent-loop" / project / "config.json"
    return source, runtime_root(values) / project / "config.json"


def migrate_legacy(project: str, env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    _, destination = legacy_config_paths(project, values)
    legacy = _read_legacy_config(project, values["HOME"])
    migrated = dict(legacy)
    migrated["project_name"] = project
    migrated["schema_version"] = 1
    linear = legacy.get("linear", {})
    migrated["providers"] = {
        "forge": "github",
        "tracker": "linear" if isinstance(linear, Mapping) and linear.get("use") else "none",
    }
    migrated.setdefault("release", {"autonomy": "off"})
    safety = migrated.setdefault("safety", {})
    if not isinstance(safety, Mapping):
        raise ConfigError("safety must be an object")
    migrated["safety"] = dict(safety)
    migrated["safety"].update(
        {
            "allow_database_writes": False,
            "allow_destructive_git": False,
            "allow_secret_reads": False,
        }
    )
    validate(migrated)
    serialized = json.dumps(migrated, indent=2) + "\n"

    try:
        project_fd = _open_runtime_project(project, values)
    except OSError as error:
        raise ConfigError("runtime project path must not be a symlink") from error
    try:
        _write_exclusive_config(project_fd, "config.json", serialized)
    finally:
        os.close(project_fd)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--profile", choices=("generic", "getbill"), required=True)
    init_parser.add_argument("--project", required=True)
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--project", required=True)
    repo_parser = subparsers.add_parser("repo")
    repo_parser.add_argument("--project", required=True)
    subparsers.add_parser("project")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.command == "validate":
        validate(json.loads(args.config.read_text(encoding="utf-8")))
        print(f"Valid config: {args.config}")
        return 0
    if args.command == "migrate":
        source, destination = legacy_config_paths(args.project)
        print(f"Migrating legacy config from {source} to {destination}")
        destination = migrate_legacy(args.project)
        print(f"Migrated config to {destination}")
        return 0
    if args.command == "repo":
        print(configured_repo_path(load_runtime_config(args.project), args.project))
        return 0
    if args.command == "project":
        print(resolve_runtime_project())
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
