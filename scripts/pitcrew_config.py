#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Mapping
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

try:
    from scripts.pitcrew_preprod_review import PreprodReviewError, validate_remote_ref
except ImportError:
    from pitcrew_preprod_review import PreprodReviewError, validate_remote_ref

if __package__:
    from scripts.pitcrew_models import (
        DEFAULT_MODELS,
        MODEL_CATALOG,
        REASONING_EFFORTS,
        ROUTING_MODES,
        resolve_model,
        resolve_reasoning_effort,
        resolve_routing_mode,
    )
else:
    from pitcrew_models import (
        DEFAULT_MODELS,
        MODEL_CATALOG,
        REASONING_EFFORTS,
        ROUTING_MODES,
        resolve_model,
        resolve_reasoning_effort,
        resolve_routing_mode,
    )


FORGES = {"github", "gitlab"}
TRACKERS = {"linear", "github", "gitlab", "none"}
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
DEFAULT_MAX_CONCURRENT_PER_SKILL = 3
MAX_CONCURRENT_PER_SKILL = 16
FIX_AUTONOMY_VALUES = {"off", "on"}
EVENT_DRIVEN_ROLES = {
    "manager-run",
    "implementer-run",
    "reviewer-run",
    "validator-run",
    "investigate-run",
    "unblock",
}


class ConfigError(ValueError):
    pass


def _capacity(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_CONCURRENT_PER_SKILL
    ):
        raise ConfigError(f"{field} must be an integer from 1 to 16")
    return value


def _execution(config: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = config.get("execution", {})
    if not isinstance(execution, Mapping):
        raise ConfigError("execution must be an object")
    if set(execution) - {
        "default_max_concurrent_per_skill",
        "max_concurrent_per_skill",
    }:
        raise ConfigError("execution contains unsupported fields")
    if "default_max_concurrent_per_skill" in execution:
        _capacity(
            execution["default_max_concurrent_per_skill"],
            "execution.default_max_concurrent_per_skill",
        )
    overrides = execution.get("max_concurrent_per_skill", {})
    if not isinstance(overrides, Mapping):
        raise ConfigError("execution.max_concurrent_per_skill must be an object")
    for skill, value in overrides.items():
        if skill not in DEFAULT_MODELS:
            raise ConfigError("execution role is unsupported")
        _capacity(value, f"execution.max_concurrent_per_skill.{skill}")
    return execution


def max_concurrent_for(config: Mapping[str, Any], skill: str) -> int:
    execution = _execution(config)
    default = _capacity(
        execution.get("default_max_concurrent_per_skill", DEFAULT_MAX_CONCURRENT_PER_SKILL),
        "execution.default_max_concurrent_per_skill",
    )
    overrides = execution.get("max_concurrent_per_skill", {})
    if skill in overrides:
        return _capacity(overrides[skill], f"execution.max_concurrent_per_skill.{skill}")
    return default


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


def fix_autonomy(config: Mapping[str, Any]) -> str:
    delivery = config.get("delivery", {})
    if not isinstance(delivery, Mapping):
        raise ConfigError("delivery must be an object")
    return delivery.get("fix_autonomy", "off")


def _non_empty_string(mapping: Mapping[str, Any], key: str, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _validate_native_github(config: Mapping[str, Any]) -> None:
    github = _mapping(config, "github")
    host = _non_empty_string(github, "host", "github.host")
    _non_empty_string(github, "user", "github.user")
    owner = _non_empty_string(github, "owner", "github.owner")
    repository = _non_empty_string(
        github, "repository", "github.repository"
    )
    repository_parts = repository.split("/")
    github_slug = re.compile(
        r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
    )
    if (
        host.startswith(("http://", "https://"))
        or "/" in host
        or "/" in owner
        or len(repository_parts) != 2
        or repository_parts[0] != owner
        or not repository_parts[1]
        or github_slug.fullmatch(owner) is None
        or github_slug.fullmatch(repository_parts[1]) is None
        or "://" in repository
    ):
        raise ConfigError("github binding must use host plus owner/repository")

    tracker = github.get("tracker")
    if not isinstance(tracker, Mapping):
        raise ConfigError("github.tracker must be an object")
    _non_empty_string(
        tracker, "ticket_prefix", "github.tracker.ticket_prefix"
    )
    assignee = tracker.get("assignee_login")
    if assignee is not None and (not isinstance(assignee, str) or not assignee):
        raise ConfigError(
            "github.tracker.assignee_login must be a non-empty string when set"
        )
    labels = tracker.get("labels")
    states = tracker.get("states")
    if not isinstance(labels, Mapping):
        raise ConfigError("github.tracker.labels must be an object")
    if not isinstance(states, Mapping):
        raise ConfigError("github.tracker.states must be an object")
    for key in ("agent", "investigate", "quick_win", "bug", "improvement"):
        _non_empty_string(labels, key, f"github.tracker.labels.{key}")
    for key in ("todo", "processing", "review", "blocked", "done"):
        _non_empty_string(states, key, f"github.tracker.states.{key}")


def _validate_bugfixer(config: Mapping[str, Any]) -> None:
    policy = config.get("bugfixer")
    if policy is None:
        return
    if not isinstance(policy, Mapping):
        raise ConfigError("bugfixer must be an object")
    allowed = {
        "sensitive_labels",
        "risky_categories_regex",
        "sensitive_approved_label",
    }
    if set(policy) != allowed:
        missing = sorted(allowed - set(policy))
        extra = sorted(set(policy) - allowed)
        field = (missing or extra)[0]
        raise ConfigError(f"bugfixer.{field} is invalid")
    labels = policy["sensitive_labels"]
    if (
        not isinstance(labels, list)
        or not labels
        or any(not isinstance(label, str) or not label for label in labels)
        or len(labels) != len(set(labels))
    ):
        raise ConfigError(
            "bugfixer.sensitive_labels must be a unique non-empty string array"
        )
    regex = policy["risky_categories_regex"]
    if not isinstance(regex, str) or not regex:
        raise ConfigError(
            "bugfixer.risky_categories_regex must be a non-empty string"
        )
    try:
        re.compile(regex, re.IGNORECASE)
    except re.error as error:
        raise ConfigError(
            "bugfixer.risky_categories_regex must compile"
        ) from error
    approved = policy["sensitive_approved_label"]
    if not isinstance(approved, str) or not approved:
        raise ConfigError(
            "bugfixer.sensitive_approved_label must be a non-empty string"
        )


def validate(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise ConfigError("config must be an object")
    providers = _mapping(config, "providers")
    if providers.get("forge") not in FORGES:
        raise ConfigError("providers.forge must be github or gitlab")
    if providers.get("tracker") not in TRACKERS:
        raise ConfigError("providers.tracker is unsupported")
    _validate_bugfixer(config)
    if providers.get("tracker") == "github":
        if providers.get("forge") != "github":
            raise ConfigError(
                "native github tracker requires providers.forge=github"
            )
        _validate_native_github(config)
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
            for key in (
                "proposal", "proposal_suggested", "proposal_approved",
                "proposal_dismissed", "category_security", "category_feature",
            ):
                if key in labels and (not isinstance(labels.get(key), str) or not labels[key]):
                    raise ConfigError(f"gitlab.tracker.labels.{key} must be a non-empty string")
            for key in ("todo", "processing", "review", "blocked", "done"):
                if not isinstance(states.get(key), str) or not states[key]:
                    raise ConfigError(f"gitlab.tracker.states.{key} must be a non-empty string")
            for key in ("suggested", "approved", "dismissed"):
                if key in states and (not isinstance(states.get(key), str) or not states[key]):
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
    delivery = config.get("delivery", {})
    if not isinstance(delivery, Mapping):
        raise ConfigError("delivery must be an object")
    if set(delivery) - {"fix_autonomy"}:
        raise ConfigError("delivery contains unsupported fields")
    if fix_autonomy(config) not in FIX_AUTONOMY_VALUES:
        raise ConfigError("delivery.fix_autonomy must be off or on")
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
    _execution(config)
    if "architecture" in config:
        architecture = config["architecture"]
        if not isinstance(architecture, Mapping):
            raise ConfigError("architecture must be an object")
        unsupported = set(architecture) - {"interval_seconds"}
        if unsupported:
            setting = sorted(unsupported, key=str)[0]
            raise ConfigError(f"architecture.{setting} is unsupported")
        interval_seconds = architecture.get("interval_seconds")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, int)
            or interval_seconds != 604800
        ):
            raise ConfigError("architecture.interval_seconds must be exactly 604800")
    if "preprod_review" in config:
        preprod_review = config["preprod_review"]
        if not isinstance(preprod_review, Mapping):
            raise ConfigError("preprod_review must be an object")
        required = {"base_ref", "compare_ref", "history_limit"}
        if set(preprod_review) != required:
            raise ConfigError("preprod_review must contain exactly base_ref, compare_ref, history_limit")
        try:
            base_ref = validate_remote_ref(preprod_review["base_ref"])
        except PreprodReviewError as error:
            raise ConfigError("preprod_review.base_ref is invalid") from error
        try:
            compare_ref = validate_remote_ref(preprod_review["compare_ref"])
        except PreprodReviewError as error:
            raise ConfigError("preprod_review.compare_ref is invalid") from error
        if base_ref == compare_ref:
            raise ConfigError("preprod_review refs must differ")
        history_limit = preprod_review["history_limit"]
        if isinstance(history_limit, bool) or not isinstance(history_limit, int) or not 1 <= history_limit <= 50:
            raise ConfigError("preprod_review.history_limit must be an integer from 1 to 50")
    agents = config.get("agents", {})
    if not isinstance(agents, Mapping):
        raise ConfigError("agents must be an object")
    for skill, entry in agents.items():
        if skill not in DEFAULT_MODELS:
            raise ConfigError(f"agents.{skill} is unsupported")
        if not isinstance(entry, Mapping):
            raise ConfigError(f"agents.{skill} must be an object")
        unsupported = set(entry) - {"model", "reasoning_effort", "routing_mode"}
        if unsupported:
            setting = sorted(unsupported, key=str)[0]
            raise ConfigError(f"agents.{skill}.{setting} is unsupported")
        model = entry.get("model")
        if not isinstance(model, str) or model not in MODEL_CATALOG:
            raise ConfigError(f"agents.{skill}.model is unsupported")
        if "reasoning_effort" in entry:
            reasoning_effort = entry["reasoning_effort"]
            if (
                not isinstance(reasoning_effort, str)
                or reasoning_effort not in REASONING_EFFORTS
            ):
                raise ConfigError(f"agents.{skill}.reasoning_effort is unsupported")
        if "routing_mode" in entry:
            routing_mode = entry["routing_mode"]
            if not isinstance(routing_mode, str) or routing_mode not in ROUTING_MODES:
                raise ConfigError(f"agents.{skill}.routing_mode is unsupported")
        if skill == "preprod-review-run":
            if entry.get("model") != "gpt-5.6-sol":
                raise ConfigError("agents.preprod-review-run.model must be gpt-5.6-sol")
            if entry.get("reasoning_effort") != "xhigh":
                raise ConfigError(
                    "agents.preprod-review-run.reasoning_effort must be xhigh"
                )
            if entry.get("routing_mode", "observe") != "observe":
                raise ConfigError("agents.preprod-review-run.routing_mode must be observe")


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
            if written == 0:
                raise OSError("unable to write temporary config")
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
        return _load_runtime_config_from_fd(project_fd)
    finally:
        os.close(project_fd)


def _load_runtime_config_from_fd(project_fd: int) -> dict[str, Any]:
    try:
        config_fd = os.open("config.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=project_fd)
        with os.fdopen(config_fd, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except OSError as error:
        raise ConfigError("runtime config path must not be a symlink or missing") from error
    validate(config)
    return dict(config)


def _lock_runtime_config(parent_fd: int) -> int:
    lock_fd: int | None = None
    acquired = False
    try:
        lock_fd = os.open(
            "config.json.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ConfigError("runtime config lock must be a regular file")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        acquired = True
        return lock_fd
    except OSError as error:
        raise ConfigError("runtime config lock must not be a symlink or unavailable") from error
    finally:
        if lock_fd is not None and not acquired:
            os.close(lock_fd)


def _replace_runtime_config(parent_fd: int, serialized: str) -> None:
    try:
        target = os.stat("config.json", dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ConfigError("runtime config path must not be a symlink or missing") from error
    if stat.S_ISLNK(target.st_mode):
        raise ConfigError("runtime config path must not be a symlink or missing")

    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        for _ in range(100):
            candidate = f".config-{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if descriptor is None or temporary_name is None:
            raise OSError("unable to create temporary config")
        data = serialized.encode("utf-8")
        while data:
            written = os.write(descriptor, data)
            data = data[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            "config.json",
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass


def update_runtime_model(
    project: str,
    skill: str,
    model: str,
    env: Mapping[str, str] | None = None,
) -> None:
    if skill not in DEFAULT_MODELS:
        raise ConfigError(f"skill is unsupported: {skill}")
    if model not in MODEL_CATALOG:
        raise ConfigError(f"model is unsupported: {model}")
    values = os.environ if env is None else env
    project_fd = _open_runtime_project_for_read(project, values)
    lock_fd: int | None = None
    try:
        lock_fd = _lock_runtime_config(project_fd)
        config = _load_runtime_config_from_fd(project_fd)
        agents = dict(config.get("agents", {}))
        agent = dict(agents.get(skill, {}))
        agent["model"] = model
        agents[skill] = agent
        updated = dict(config)
        updated["agents"] = agents
        validate(updated)
        serialized = json.dumps(updated, indent=2) + "\n"
        _replace_runtime_config(project_fd, serialized)
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(project_fd)


def update_runtime_github_binding(
    project: str,
    binding: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> None:
    if not isinstance(binding, Mapping):
        raise ConfigError("github binding must be an object")
    values = os.environ if env is None else env
    project_fd = _open_runtime_project_for_read(project, values)
    lock_fd: int | None = None
    try:
        lock_fd = _lock_runtime_config(project_fd)
        config = _load_runtime_config_from_fd(project_fd)
        updated = dict(config)
        updated["providers"] = {"forge": "github", "tracker": "github"}
        updated["github"] = dict(binding)
        validate(updated)
        _replace_runtime_config(
            project_fd,
            json.dumps(updated, indent=2) + "\n",
        )
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(project_fd)


def update_runtime_fix_autonomy(
    project: str,
    mode: str,
    env: Mapping[str, str] | None = None,
) -> None:
    if mode not in FIX_AUTONOMY_VALUES:
        raise ConfigError("delivery.fix_autonomy must be off or on")
    values = os.environ if env is None else env
    project_fd = _open_runtime_project_for_read(project, values)
    lock_fd: int | None = None
    try:
        lock_fd = _lock_runtime_config(project_fd)
        config = _load_runtime_config_from_fd(project_fd)
        delivery = dict(config.get("delivery", {}))
        delivery["fix_autonomy"] = mode
        updated = dict(config)
        updated["delivery"] = delivery
        validate(updated)
        _replace_runtime_config(project_fd, json.dumps(updated, indent=2) + "\n")
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(project_fd)


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
    github_parser = subparsers.add_parser("bind-github")
    github_parser.add_argument("--project", required=True)
    github_parser.add_argument("--binding", type=Path, required=True)
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--project", required=True)
    repo_parser = subparsers.add_parser("repo")
    repo_parser.add_argument("--project", required=True)
    model_parser = subparsers.add_parser("model")
    model_parser.add_argument("--project", required=True)
    model_parser.add_argument("--skill", required=True)
    reasoning_parser = subparsers.add_parser("reasoning")
    reasoning_parser.add_argument("--project", required=True)
    reasoning_parser.add_argument("--skill", required=True)
    reasoning_effort_parser = subparsers.add_parser("reasoning-effort")
    reasoning_effort_parser.add_argument("--project", required=True)
    reasoning_effort_parser.add_argument("--skill", required=True)
    routing_parser = subparsers.add_parser("routing-mode")
    routing_parser.add_argument("--project", required=True)
    routing_parser.add_argument("--skill", required=True)
    subparsers.add_parser("project")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.command == "validate":
        validate(json.loads(args.config.read_text(encoding="utf-8")))
        print(f"Valid config: {args.config}")
        return 0
    if args.command == "bind-github":
        binding = json.loads(args.binding.read_text(encoding="utf-8"))
        update_runtime_github_binding(args.project, binding)
        print(f"Configured native GitHub binding for {args.project}")
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
    if args.command == "model":
        print(resolve_model(load_runtime_config(args.project), args.skill))
        return 0
    if args.command in {"reasoning", "reasoning-effort"}:
        print(resolve_reasoning_effort(load_runtime_config(args.project), args.skill))
        return 0
    if args.command == "routing-mode":
        print(resolve_routing_mode(load_runtime_config(args.project), args.skill))
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
