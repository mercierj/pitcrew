"""Deterministic repository-area rotation for discovery agents."""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXCLUDED_DIRS = {
    ".git", ".cache", ".codex", ".next", ".pytest_cache", ".venv",
    "build", "dist", "coverage", "vendor", "node_modules", "target",
    "graphify-out", "converted", "generated", "tmp", "var",
}
RELEVANT_DIRS = {"app", "apps", "cmd", "config", "docs", "lib", "packages", "scripts", "src", "test", "tests"}
RELEVANT_SUFFIXES = {".c", ".cpp", ".go", ".java", ".js", ".jsx", ".md", ".php", ".py", ".rb", ".rs", ".sh", ".sql", ".ts", ".tsx", ".vue", ".yaml", ".yml"}


def _top_level(path: str) -> str | None:
    first = Path(path).parts[0] if Path(path).parts else ""
    return first if first and first not in EXCLUDED_DIRS and not first.startswith(".") else None


def _tracked_paths(repo_path: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [item.decode("utf-8", errors="replace") for item in result.stdout.split(b"\0") if item]


def discover_areas(repo_path: Path) -> list[str]:
    """Return stable, bounded top-level areas with relevant tracked content."""
    paths = _tracked_paths(repo_path)
    if not paths:
        paths = [str(path.relative_to(repo_path)) for path in repo_path.rglob("*") if path.is_file()]
    areas: set[str] = set()
    for relative in paths:
        parts = Path(relative).parts
        if len(parts) < 2:
            continue
        area = _top_level(relative)
        if area is None:
            continue
        if area in RELEVANT_DIRS or Path(relative).suffix.lower() in RELEVANT_SUFFIXES:
            areas.add(area)
    return sorted(areas) or ["."]


def _git_fingerprint(repo_path: Path, area: str) -> str:
    tree = "HEAD^{tree}" if area == "." else f"HEAD:{area}"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", tree],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def discover_fingerprints(repo_path: Path, areas: list[str]) -> dict[str, str]:
    """Return Git tree fingerprints; empty values mean Git is unavailable."""
    return {area: fingerprint for area in areas if (fingerprint := _git_fingerprint(repo_path, area))}


def _reject_symlinked_path(path: Path) -> None:
    """Refuse state paths containing a symlinked component."""
    absolute_path = Path(os.path.abspath(path))
    component = Path(absolute_path.anchor)
    for part in absolute_path.parts[1:]:
        component /= part
        try:
            mode = os.lstat(component).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise OSError(f"cannot safely inspect state path: {path}") from error
        if stat.S_ISLNK(mode):
            raise OSError(f"refusing symlinked state path: {path}")


def _open_state_parent(path: Path) -> tuple[int, str]:
    """Open or create the state parent without following path-component symlinks."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("O_NOFOLLOW is required for safe state writes")
    absolute_path = Path(os.path.abspath(path))
    parts = absolute_path.parts
    if len(parts) < 2:
        raise OSError(f"invalid state path: {path}")
    directory_fd = os.open(absolute_path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[1:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
    except BaseException:
        os.close(directory_fd)
        raise
    try:
        os.fchmod(directory_fd, 0o700)
    except BaseException:
        os.close(directory_fd)
        raise
    return directory_fd, parts[-1]


def _atomic_write_json(state: dict[str, Any], path: Path) -> None:
    """Atomically persist state without following symlinks during path traversal."""
    directory_fd, final_name = _open_state_parent(path)
    temporary_name: str | None = None
    try:
        try:
            final_mode = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(final_mode):
                raise OSError(f"refusing symlinked state path: {path}")

        for _ in range(10):
            candidate = f".{final_name}.{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        else:
            raise OSError("unable to allocate private state temporary file")

        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, final_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_name = None
        try:
            os.fsync(directory_fd)
        except OSError as error:
            unsupported_fsync_errors = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
            if error.errno not in unsupported_fsync_errors:
                raise
    finally:
        if temporary_name is not None:
            os.unlink(temporary_name, dir_fd=directory_fd)
        os.close(directory_fd)


def _valid_coverage(value: Any, areas: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"areas": areas[:], "next_area": areas[0], "epoch": 0, "visited": {}, "fingerprints": {}}
    epoch = value.get("epoch")
    visited = value.get("visited")
    fingerprints = value.get("fingerprints")
    old_areas = value.get("areas")
    if not isinstance(epoch, int) or epoch < 0 or not isinstance(visited, dict):
        return {"areas": areas[:], "next_area": areas[0], "epoch": 0, "visited": {}, "fingerprints": {}}
    visited = {area: stamp for area, stamp in visited.items() if area in areas and isinstance(stamp, str)}
    fingerprints = fingerprints if isinstance(fingerprints, dict) else {}
    fingerprints = {area: value for area, value in fingerprints.items() if area in areas and isinstance(value, str)}
    return {
        "areas": areas[:],
        "next_area": old_areas[0] if isinstance(old_areas, list) and old_areas and old_areas[0] in areas else areas[0],
        "epoch": epoch,
        "visited": visited,
        "fingerprints": fingerprints,
    }


def select_area(
    cell_state: dict[str, Any], areas: list[str], now: str,
    current_fingerprints: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Select the least recently visited area without advancing persisted state."""
    coverage = _valid_coverage(cell_state.get("coverage"), areas)
    current_fingerprints = current_fingerprints or {}
    changed = [
        area for area in areas
        if current_fingerprints.get(area)
        and current_fingerprints.get(area) != coverage["fingerprints"].get(area)
    ]
    if changed:
        area = min(changed, key=lambda candidate: (coverage["visited"].get(candidate, ""), candidate))
    else:
        if len(coverage["visited"]) >= len(areas):
            coverage["epoch"] += 1
            coverage["visited"] = {}
        area = min(areas, key=lambda candidate: (coverage["visited"].get(candidate, ""), candidate))
    coverage["next_area"] = area
    coverage["selected_at"] = now
    return area, coverage


def update_cell_state(
    state: dict[str, Any], cell_key: str, areas: list[str], selected_area: str,
    epoch: int, timestamp: str, path: Path | None = None,
    current_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Record a completed scan and optionally atomically persist the state."""
    if path is not None:
        _reject_symlinked_path(path)
    cells = state.setdefault("cells", {})
    cell = cells.setdefault(cell_key, {})
    coverage = _valid_coverage(cell.get("coverage"), areas)
    coverage["epoch"] = epoch
    coverage["visited"][selected_area] = timestamp
    if current_fingerprints and current_fingerprints.get(selected_area):
        coverage["fingerprints"][selected_area] = current_fingerprints[selected_area]
    remaining = [area for area in areas if area not in coverage["visited"]]
    coverage["next_area"] = remaining[0] if remaining else areas[0]
    coverage.pop("selected_at", None)
    cell["coverage"] = coverage
    if path is not None:
        _atomic_write_json(state, path)
    return state


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover")
    discover.add_argument("repo", type=Path)
    select = subparsers.add_parser("select")
    select.add_argument("state", type=Path)
    select.add_argument("cell")
    select.add_argument("repo", type=Path)
    record = subparsers.add_parser("record")
    record.add_argument("state", type=Path)
    record.add_argument("cell")
    record.add_argument("repo", type=Path)
    record.add_argument("area")
    record.add_argument("epoch", type=int)
    args = parser.parse_args()

    areas = discover_areas(args.repo)
    if args.command == "discover":
        print(json.dumps(areas))
        return 0
    try:
        _reject_symlinked_path(args.state)
    except OSError as error:
        parser.error(str(error))
    try:
        state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {"cells": {}, "history": []}
    except json.JSONDecodeError as error:
        parser.error(f"invalid JSON state: {error.msg}")
    except UnicodeDecodeError:
        parser.error("invalid state encoding: expected UTF-8")
    if args.command == "select":
        cell = state.get("cells", {}).get(args.cell, {})
        fingerprints = discover_fingerprints(args.repo, areas)
        area, coverage = select_area(cell, areas, _now(), fingerprints)
        print(json.dumps({"area": area, "epoch": coverage["epoch"], "areas": areas}))
        return 0
    update_cell_state(
        state, args.cell, areas, args.area, args.epoch, _now(), args.state,
        discover_fingerprints(args.repo, areas),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
