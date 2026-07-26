"""Prepare a deterministic, read-only Git delta manifest for preprod review."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import secrets
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


class PreprodReviewError(RuntimeError):
    """Raised when a review manifest cannot be safely prepared."""


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_FORBIDDEN = re.compile(r"[\\\\\s\x00-\x1f\x7f~^:?*\[]|@\{")
_GENERATED_COMPONENTS = {"generated", "dist", "build", "coverage", "graphify-out"}
_FIXED_MODEL = "gpt-5.6-sol"
_FIXED_REASONING_EFFORT = "xhigh"
_MAX_TEXT = 2048
_MISSING = object()
_MAX_FILES = 10_000
_MAX_COMMITS = 10_000
_MAX_FINDINGS = 200
_MAX_EVIDENCE = 100


def _bounded_text(value: Any, *, required: bool = False, limit: int = _MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (required and not value) or len(value) > limit:
        return None
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_remote_ref(value: Any) -> str:
    """Accept only a concrete ``origin/<branch>`` remote-tracking reference."""
    if not isinstance(value, str) or not value or not value.startswith("origin/"):
        raise PreprodReviewError("invalid remote ref")
    branch = value[len("origin/"):]
    if not branch or branch.startswith("/") or branch.endswith("/") or "//" in branch:
        raise PreprodReviewError("invalid remote ref")
    if _REF_FORBIDDEN.search(value) or ".." in value or value.endswith(".lock") or value.endswith("."):
        raise PreprodReviewError("invalid remote ref")
    parts = branch.split("/")
    if any(not part or part in {".", ".."} or part.startswith((".", "-")) or part.endswith(".") for part in parts):
        raise PreprodReviewError("invalid remote ref")
    return value


def _run_git(repo: Path, arguments: list[str], runner: Callable[..., Any]) -> bytes:
    try:
        result = runner(
            ["git", "-C", str(repo), *arguments],
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError, TypeError) as error:
        raise PreprodReviewError("git command failed") from error
    if getattr(result, "returncode", 1) != 0:
        raise PreprodReviewError("git command failed")
    stdout = getattr(result, "stdout", b"")
    return stdout.encode() if isinstance(stdout, str) else stdout


def _sha(repo: Path, revision: str, runner: Callable[..., Any]) -> str:
    value = _run_git(repo, ["rev-parse", "--verify", f"{revision}^{{commit}}"], runner).decode("ascii", "replace").strip()
    if not _SHA_RE.fullmatch(value):
        raise PreprodReviewError("git returned an invalid commit id")
    return value


def _parse_commits(raw: bytes) -> list[dict[str, str]]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        raise PreprodReviewError("malformed git commit output")
    commits = []
    for index in range(0, len(fields), 3):
        sha = fields[index].decode("ascii", "replace")
        if not _SHA_RE.fullmatch(sha):
            raise PreprodReviewError("malformed git commit output")
        commits.append({
            "sha": sha,
            "authored_at": fields[index + 1].decode("utf-8", "replace"),
            "subject": fields[index + 2].decode("utf-8", "replace"),
        })
    return commits


def _parse_name_status(raw: bytes) -> list[dict[str, str]]:
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    files: list[dict[str, str]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", "replace")
        index += 1
        if not status or status[0] not in "AMDRCT":
            raise PreprodReviewError("malformed git file output")
        if status[0] in "RC":
            if index + 1 >= len(tokens):
                raise PreprodReviewError("malformed git file output")
            old_path, path = tokens[index], tokens[index + 1]
            index += 2
            files.append({"status": status, "old_path": old_path.decode("utf-8", "replace"), "path": path.decode("utf-8", "replace")})
        else:
            if index >= len(tokens):
                raise PreprodReviewError("malformed git file output")
            files.append({"status": status[0], "path": tokens[index].decode("utf-8", "replace")})
            index += 1
    return files


def _binary_paths(raw: bytes) -> set[str]:
    """Extract paths whose numstat has Git's binary ``-\t-`` marker."""
    tokens = raw.split(b"\0")
    binary: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        parts = token.split(b"\t", 2)
        if len(parts) != 3:
            raise PreprodReviewError("malformed git numstat output")
        added, deleted, path = parts
        is_binary = added == b"-" and deleted == b"-"
        if path:
            if is_binary:
                binary.add(path.decode("utf-8", "replace"))
            continue
        if index + 1 >= len(tokens):
            raise PreprodReviewError("malformed git numstat output")
        old_path, new_path = tokens[index], tokens[index + 1]
        index += 2
        if is_binary:
            binary.update({old_path.decode("utf-8", "replace"), new_path.decode("utf-8", "replace")})
    return binary


def _generated(path: str) -> bool:
    return any(component in _GENERATED_COMPONENTS for component in path.split("/"))


def build_manifest(repo: str | Path, base_ref: str, compare_ref: str, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Fetch two origin branches and return their deterministic review delta."""
    repository = Path(repo)
    base_ref = validate_remote_ref(base_ref)
    compare_ref = validate_remote_ref(compare_ref)
    if base_ref == compare_ref:
        raise PreprodReviewError("base and compare refs must differ")
    base_branch, compare_branch = base_ref.removeprefix("origin/"), compare_ref.removeprefix("origin/")
    _run_git(
        repository,
        [
            "fetch", "--no-tags", "origin",
            f"+refs/heads/{base_branch}:refs/remotes/origin/{base_branch}",
            f"+refs/heads/{compare_branch}:refs/remotes/origin/{compare_branch}",
        ],
        runner,
    )
    base_sha = _sha(repository, base_ref, runner)
    compare_sha = _sha(repository, compare_ref, runner)
    merge_base_sha = _run_git(repository, ["merge-base", base_sha, compare_sha], runner).decode("ascii", "replace").strip()
    if not _SHA_RE.fullmatch(merge_base_sha):
        raise PreprodReviewError("git returned an invalid commit id")
    commits = _parse_commits(_run_git(repository, ["log", "-z", "--format=%H%x00%aI%x00%s", f"{base_sha}..{compare_sha}"], runner))
    diff_options = ["-z", "--find-renames", "--find-copies", "--find-copies-harder", f"{base_sha}...{compare_sha}"]
    files = _parse_name_status(_run_git(repository, ["diff", "--name-status", *diff_options], runner))
    binary_paths = _binary_paths(_run_git(repository, ["diff", "--numstat", *diff_options], runner))
    for item in files:
        old_path = item.get("old_path")
        item["binary"] = item["path"] in binary_paths or (old_path in binary_paths if old_path else False)
        item["generated"] = _generated(item["path"]) or (bool(old_path) and _generated(old_path))
        item["reviewed"] = False
    files.sort(key=lambda item: (item["path"], item["status"]))
    return {
        "schema_version": 1,
        "prepared_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "base_ref": base_ref,
        "compare_ref": compare_ref,
        "base_sha": base_sha,
        "compare_sha": compare_sha,
        "merge_base_sha": merge_base_sha,
        "commit_count": len(commits),
        "changed_file_count": len(files),
        "commits": commits,
        "files": files,
    }


def _valid_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the immutable portion of a prepared review manifest."""
    expected = {"schema_version", "prepared_at", "base_ref", "compare_ref", "base_sha", "compare_sha",
                "merge_base_sha", "commit_count", "changed_file_count", "commits", "files"}
    if not isinstance(manifest, dict) or set(manifest) != expected or manifest.get("schema_version") != 1:
        raise PreprodReviewError("invalid manifest")
    base_ref = validate_remote_ref(manifest.get("base_ref"))
    compare_ref = validate_remote_ref(manifest.get("compare_ref"))
    if base_ref == compare_ref:
        raise PreprodReviewError("invalid manifest")
    for key in ("base_sha", "compare_sha", "merge_base_sha"):
        value = manifest.get(key)
        if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
            raise PreprodReviewError("invalid manifest")
    try:
        datetime.fromisoformat(manifest["prepared_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise PreprodReviewError("invalid manifest") from None
    files, commits = manifest.get("files"), manifest.get("commits")
    if (not isinstance(files, list) or not isinstance(commits, list) or len(files) > _MAX_FILES or len(commits) > _MAX_COMMITS
            or isinstance(manifest.get("changed_file_count"), bool) or isinstance(manifest.get("commit_count"), bool)):
        raise PreprodReviewError("invalid manifest")
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise PreprodReviewError("invalid manifest")
        status, path = item.get("status"), item.get("path")
        renamed = isinstance(status, str) and re.fullmatch(r"[RC][1-9][0-9]*", status)
        expected_file = {"status", "path", "binary", "generated", "reviewed"} | ({"old_path"} if renamed else set())
        if (set(item) != expected_file or not isinstance(status, str) or not (status in {"A", "M", "D", "T"} or renamed)
                or _bounded_text(path, required=True) is None or path in paths
                or not all(isinstance(item[key], bool) for key in ("binary", "generated", "reviewed"))
                or (renamed and _bounded_text(item.get("old_path"), required=True) is None)):
            raise PreprodReviewError("invalid manifest")
        paths.add(path)
    for commit in commits:
        if (not isinstance(commit, dict) or set(commit) != {"sha", "authored_at", "subject"}
                or not isinstance(commit["sha"], str) or not _SHA_RE.fullmatch(commit["sha"])
                or _bounded_text(commit["authored_at"], required=True) is None
                or _bounded_text(commit["subject"], required=False) is None):
            raise PreprodReviewError("invalid manifest")
    if manifest.get("changed_file_count") != len(files) or manifest.get("commit_count") != len(commits):
        raise PreprodReviewError("invalid manifest")
    return manifest


def _incomplete(manifest: dict[str, Any], reason: Any) -> dict[str, Any]:
    """Return a safe, bounded incomplete report without trusting model output."""
    valid = _valid_manifest(manifest)
    text = _bounded_text(reason, required=True) or "review result is invalid"
    return {
        "schema_version": 1,
        "completed_at": _utc_now(),
        "base_ref": valid["base_ref"], "compare_ref": valid["compare_ref"],
        "base_sha": valid["base_sha"], "compare_sha": valid["compare_sha"],
        "merge_base_sha": valid["merge_base_sha"],
        "commit_count": valid["commit_count"], "changed_file_count": valid["changed_file_count"],
        "files": [{**item, "reviewed": False} for item in valid["files"]],
        "reviewed_files": [], "findings": [], "synthesis": "", "verdict": "incomplete",
        "model": _FIXED_MODEL, "reasoning_effort": _FIXED_REASONING_EFFORT,
        "failure_reason": text[:_MAX_TEXT],
    }


def _valid_finding(item: Any, paths: set[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict) or set(item) != {"severity", "title", "evidence", "affected_files", "impact", "recommendation"}:
        return None
    if item.get("severity") not in {"critical", "high", "medium", "low"}:
        return None
    fields = ("title", "impact", "recommendation")
    if any(_bounded_text(item.get(key), required=True) is None for key in fields):
        return None
    evidence, affected = item.get("evidence"), item.get("affected_files")
    if (not isinstance(evidence, list) or not evidence or len(evidence) > _MAX_EVIDENCE or not all(_bounded_text(value, required=True) is not None for value in evidence)
            or not isinstance(affected, list) or not affected or len(affected) > len(paths)
            or not all(isinstance(value, str) and value in paths for value in affected)
            or len(affected) != len(set(affected))):
        return None
    return item


def finalize_report(manifest: dict[str, Any], result: Any, model: Any, reasoning_effort: Any) -> dict[str, Any]:
    """Validate a Sol review result and produce an exhaustive final report."""
    valid = _valid_manifest(manifest)
    if model != _FIXED_MODEL or reasoning_effort != _FIXED_REASONING_EFFORT:
        return _incomplete(valid, "unexpected review model or reasoning effort")
    if not isinstance(result, dict) or set(result) != {"reviewed_files", "findings", "synthesis"}:
        return _incomplete(valid, "review result schema is invalid")
    manifest_paths = {item["path"] for item in valid["files"]}
    reviewed = result.get("reviewed_files")
    if (not isinstance(reviewed, list) or len(reviewed) > _MAX_FILES or not all(isinstance(path, str) for path in reviewed)
            or len(reviewed) != len(set(reviewed)) or set(reviewed) != manifest_paths):
        return _incomplete(valid, "reviewed files do not exactly cover the manifest")
    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) > _MAX_FINDINGS:
        return _incomplete(valid, "findings schema is invalid")
    checked = [_valid_finding(item, manifest_paths) for item in findings]
    if any(item is None for item in checked):
        return _incomplete(valid, "finding schema is invalid")
    synthesis = _bounded_text(result.get("synthesis"), required=bool(manifest_paths))
    if not manifest_paths and result.get("synthesis") in ("", None):
        synthesis = "No changes between configured refs."
    elif synthesis is None:
        if not manifest_paths and result.get("synthesis") in ("", None):
            synthesis = "No changes between configured refs."
        else:
            return _incomplete(valid, "synthesis is invalid")
    verdict = "changes_required" if any(item["severity"] in {"critical", "high"} for item in checked if item) else "ready"
    return {
        "schema_version": 1, "completed_at": _utc_now(),
        "base_ref": valid["base_ref"], "compare_ref": valid["compare_ref"],
        "base_sha": valid["base_sha"], "compare_sha": valid["compare_sha"],
        "merge_base_sha": valid["merge_base_sha"], "commit_count": valid["commit_count"],
        "changed_file_count": valid["changed_file_count"],
        "files": [{**item, "reviewed": True} for item in valid["files"]], "reviewed_files": sorted(reviewed),
        "findings": checked, "synthesis": synthesis, "verdict": verdict,
        "model": _FIXED_MODEL, "reasoning_effort": _FIXED_REASONING_EFFORT, "failure_reason": None,
    }


def _safe_parent(path: Path, *, create: bool = True) -> tuple[int, str]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise PreprodReviewError("safe storage requires O_NOFOLLOW")
    original = Path(os.path.abspath(path))
    # macOS exposes its temporary directory through this one system symlink.
    absolute = Path("/private/var") / original.relative_to("/var") if original == Path("/var") or Path("/var") in original.parents else original
    fd = os.open(absolute.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in absolute.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow, dir_fd=fd)
            os.close(fd)
            fd = child
        if create:
            os.fchmod(fd, 0o700)
        return fd, absolute.name
    except FileNotFoundError:
        os.close(fd)
        raise
    except (OSError, ValueError) as error:
        os.close(fd)
        raise PreprodReviewError("unsafe storage path") from error


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    fd, name = _safe_parent(path)
    temporary: str | None = None
    try:
        try:
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise PreprodReviewError("unsafe storage path")
        except FileNotFoundError:
            pass
        for _ in range(10):
            candidate = f".{name}.{secrets.token_hex(16)}"
            try:
                output = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd)
                temporary = candidate
                break
            except FileExistsError:
                continue
        else:
            raise PreprodReviewError("unable to write storage")
        os.fchmod(output, 0o600)
        with os.fdopen(output, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        temporary = None
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
    except OSError as error:
        raise PreprodReviewError("unable to write storage") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.close(fd)


def _read_json(path: Path, *, absent: Any = None) -> Any:
    try:
        directory_fd, name = _safe_parent(path, create=False)
    except FileNotFoundError:
        return absent
    try:
        try:
            input_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
        except FileNotFoundError:
            return absent
        metadata = os.fstat(input_fd)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(input_fd)
            raise PreprodReviewError("unsafe storage path")
        with os.fdopen(input_fd, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreprodReviewError("invalid stored JSON") from error
    finally:
        os.close(directory_fd)


def write_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    _valid_manifest(manifest)
    target = Path(path)
    existing = _read_json(target, absent=_MISSING)
    if existing is not _MISSING:
        _valid_manifest(existing)
    _atomic_json(target, manifest)


def read_manifest(path: str | Path) -> dict[str, Any]:
    value = _read_json(Path(path))
    return _valid_manifest(value)


class ReportStore:
    """A bounded private JSON history of immutable preprod review reports."""
    def __init__(self, path: str | Path, history_limit: int = 10):
        if isinstance(history_limit, bool) or not isinstance(history_limit, int) or not 1 <= history_limit <= 100:
            raise PreprodReviewError("invalid history limit")
        self.path, self.history_limit = Path(path), history_limit

    def read(self) -> dict[str, Any]:
        value = _read_json(self.path, absent={"schema_version": 1, "reports": []})
        if (not isinstance(value, dict) or set(value) != {"schema_version", "reports"}
                or value["schema_version"] != 1 or not isinstance(value["reports"], list)
                or len(value["reports"]) > self.history_limit or not all(self._valid_report(item) for item in value["reports"])
                or len({self._key(item) for item in value["reports"]}) != len(value["reports"])):
            raise PreprodReviewError("invalid report store")
        return value

    @staticmethod
    def _valid_report(report: Any) -> bool:
        required = {"schema_version", "completed_at", "base_ref", "compare_ref", "base_sha", "compare_sha",
                    "merge_base_sha", "commit_count", "changed_file_count", "files", "reviewed_files",
                    "findings", "synthesis", "verdict", "model", "reasoning_effort", "failure_reason"}
        if not isinstance(report, dict) or set(report) != required or report.get("schema_version") != 1:
            return False
        if report.get("verdict") not in {"ready", "changes_required", "incomplete"}:
            return False
        if report.get("model") != _FIXED_MODEL or report.get("reasoning_effort") != _FIXED_REASONING_EFFORT:
            return False
        try:
            base_ref = validate_remote_ref(report.get("base_ref"))
            compare_ref = validate_remote_ref(report.get("compare_ref"))
            if base_ref == compare_ref:
                return False
            datetime.fromisoformat(report["completed_at"].replace("Z", "+00:00"))
        except (PreprodReviewError, AttributeError, TypeError, ValueError):
            return False
        if (isinstance(report["commit_count"], bool) or not isinstance(report["commit_count"], int) or not 0 <= report["commit_count"] <= _MAX_COMMITS
                or isinstance(report["changed_file_count"], bool) or not isinstance(report["changed_file_count"], int) or not 0 <= report["changed_file_count"] <= _MAX_FILES):
            return False
        files = report["files"]
        if not isinstance(files, list) or len(files) != report["changed_file_count"] or len(files) > _MAX_FILES:
            return False
        paths: set[str] = set()
        for item in files:
            path = item.get("path") if isinstance(item, dict) else None
            status = item.get("status") if isinstance(item, dict) else None
            renamed = isinstance(status, str) and re.fullmatch(r"[RC][1-9][0-9]*", status)
            expected_file = {"status", "path", "binary", "generated", "reviewed"} | ({"old_path"} if renamed else set())
            if (not isinstance(item, dict) or set(item) != expected_file or not isinstance(status, str)
                    or not (status in {"A", "M", "D", "T"} or renamed)
                    or _bounded_text(path, required=True) is None or path in paths
                    or not all(isinstance(item.get(key), bool) for key in ("binary", "generated", "reviewed"))
                    or (renamed and _bounded_text(item.get("old_path"), required=True) is None)):
                return False
            paths.add(path)
        sha_values = (report["base_sha"], report["compare_sha"], report["merge_base_sha"])
        valid_shas = all(isinstance(value, str) and _SHA_RE.fullmatch(value) for value in sha_values)
        null_shas = all(value is None for value in sha_values)
        verdict = report["verdict"]
        if verdict == "incomplete":
            return ((valid_shas or (null_shas and not files and report["commit_count"] == 0))
                    and report["reviewed_files"] == [] and report["findings"] == [] and report["synthesis"] == ""
                    and all(not item["reviewed"] for item in files)
                    and _bounded_text(report["failure_reason"], required=True) is not None)
        if not valid_shas or report["failure_reason"] is not None:
            return False
        if (not isinstance(report["reviewed_files"], list) or not all(isinstance(path, str) for path in report["reviewed_files"])
                or len(report["reviewed_files"]) != len(set(report["reviewed_files"])) or set(report["reviewed_files"]) != paths):
            return False
        if (not all(item["reviewed"] for item in files) or not isinstance(report["findings"], list)
                or len(report["findings"]) > _MAX_FINDINGS):
            return False
        findings = [_valid_finding(item, paths) for item in report["findings"]]
        if any(item is None for item in findings):
            return False
        synthesis = report["synthesis"]
        if paths:
            if _bounded_text(synthesis, required=True) is None:
                return False
        elif synthesis != "No changes between configured refs.":
            return False
        has_blocker = any(item["severity"] in {"critical", "high"} for item in findings if item)
        return (report["verdict"] == "changes_required") == has_blocker

    @staticmethod
    def _key(report: dict[str, Any]) -> tuple[Any, ...]:
        # Failed pre-fetches have no SHA: refs distinguish them deterministically.
        if isinstance(report.get("base_sha"), str) and isinstance(report.get("compare_sha"), str):
            return ("sha", report["base_sha"], report["compare_sha"])
        return ("refs", report.get("base_ref"), report.get("compare_ref"))

    def save(self, report: dict[str, Any]) -> dict[str, Any]:
        if not self._valid_report(report):
            raise PreprodReviewError("invalid report")
        state = self.read()
        key = self._key(report)
        reports = [item for item in state["reports"] if not isinstance(item, dict) or self._key(item) != key]
        state["reports"] = [report, *reports][:self.history_limit]
        _atomic_json(self.path, state)
        return report


def _failure_report(base_ref: Any, compare_ref: Any, reason: Any) -> dict[str, Any]:
    base_ref = validate_remote_ref(base_ref)
    compare_ref = validate_remote_ref(compare_ref)
    if base_ref == compare_ref:
        raise PreprodReviewError("base and compare refs must differ")
    return {
        "schema_version": 1, "completed_at": _utc_now(),
        "base_ref": base_ref, "compare_ref": compare_ref,
        "base_sha": None, "compare_sha": None, "merge_base_sha": None,
        "commit_count": 0, "changed_file_count": 0, "files": [], "reviewed_files": [], "findings": [],
        "synthesis": "", "verdict": "incomplete", "model": _FIXED_MODEL,
        "reasoning_effort": _FIXED_REASONING_EFFORT,
        "failure_reason": (_bounded_text(reason, required=True) or "review failed")[:_MAX_TEXT],
    }


def _compact_print(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prepare and finalize manual preprod review reports")
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--base-ref", required=True)
    prepare.add_argument("--compare-ref", required=True)
    prepare.add_argument("--manifest", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--manifest", required=True)
    finalize.add_argument("--result", required=True)
    finalize.add_argument("--store", required=True)
    finalize.add_argument("--history-limit", type=int, default=10)
    fail = commands.add_parser("fail")
    fail.add_argument("--base-ref", required=True)
    fail.add_argument("--compare-ref", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--store", required=True)
    fail.add_argument("--history-limit", type=int, default=10)
    show = commands.add_parser("show")
    show.add_argument("--store", required=True)
    show.add_argument("--history-limit", type=int, default=10)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "prepare":
            manifest = build_manifest(args.repo, args.base_ref, args.compare_ref)
            write_manifest(manifest, args.manifest)
            _compact_print(manifest)
        elif args.command == "finalize":
            manifest = read_manifest(args.manifest)
            try:
                raw_result = _read_json(Path(args.result))
                report = finalize_report(manifest, raw_result, _FIXED_MODEL, _FIXED_REASONING_EFFORT)
            except PreprodReviewError as error:
                report = _incomplete(manifest, str(error))
            _compact_print(ReportStore(args.store, args.history_limit).save(report))
        elif args.command == "fail":
            report = _failure_report(args.base_ref, args.compare_ref, args.reason)
            _compact_print(ReportStore(args.store, args.history_limit).save(report))
        else:
            state = ReportStore(args.store, args.history_limit).read()
            _compact_print({"latest": state["reports"][0] if state["reports"] else None, "history": state["reports"]})
        return 0
    except (PreprodReviewError, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)[:_MAX_TEXT]}, separators=(",", ":")), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
