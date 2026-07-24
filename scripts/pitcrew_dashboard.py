#!/usr/bin/env python3
"""Local dashboard aggregation for Pitcrew scheduler, history, and GitLab data."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from scripts.pitcrew_config import ConfigError, validate
from scripts.pitcrew_history import HistoryStore, classify_record


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "bin/pitcrew-schedule.py"
RUNNER = ROOT / "bin/pitcrew-codex.sh"
LIFECYCLES = ("todo", "processing", "review", "blocked", "done")
ENABLED_SKILLS = {
    "research-run",
    "manager-run",
    "implementer-run",
    "reviewer-run",
    "validator-run",
    "investigate-run",
    "stale-sweep",
}
DISABLED_SKILLS = {
    "qa-run",
    "coverage-run",
    "dev-verify-run",
    "ops-run",
    "unblock",
    "releaser-run",
}
CONTROL_ACTIONS = {"trigger", "stop", "restart"}
MR_URL = re.compile(r"https?://[^\s<>'\"]+/-/merge_requests/\d+")
MR_REFERENCE = re.compile(r"(?<![\w!])!(\d+)\b")


class DashboardError(RuntimeError):
    pass


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _redacted_error(message: str, fallback: str) -> str:
    if not message.strip():
        return fallback
    scrubbed = re.sub(
        r"(?im)(\bAuthorization\s*:\s*)[^\r\n]+",
        r"\1[REDACTED]",
        message,
    )
    scrubbed = re.sub(
        (
            r'(?i)("?\b(?:token|authorization|password|secret)\b"?\s*'
            r'(?:=|:)\s*)(?:Bearer\s+)?(?:"[^"]*"|\'[^\']*\'|[^\s,}]+)'
        ),
        r"\1[REDACTED]",
        scrubbed,
    )
    return " ".join(scrubbed.split())[:2048]


def _label_value(labels: object, prefix: str) -> str | None:
    if not isinstance(labels, list):
        return None
    matches = [
        label.removeprefix(prefix)
        for label in labels
        if isinstance(label, str) and label.startswith(prefix)
    ]
    return matches[0] if len(matches) == 1 and matches[0] else None


class DashboardService:
    def __init__(
        self,
        project: str,
        runtime_dir: Path,
        command_runner: Callable,
        now: Callable[[], datetime],
    ):
        self.project = project
        self.runtime_dir = Path(runtime_dir)
        self.command_runner = command_runner
        self.now = now
        self.history_store = HistoryStore(self.runtime_dir / "history.jsonl")
        self.config = self._load_config()
        if self.config.get("project_name") != project:
            raise DashboardError("runtime config project does not match dashboard project")
        self.gitlab_project = self.config["gitlab"]["project_path"]
        self._gitlab_cache: dict | None = None
        self._gitlab_cached_at: datetime | None = None
        self._last_successful_refresh: str | None = None

    def _load_config(self) -> dict:
        try:
            value = json.loads(
                (self.runtime_dir / "config.json").read_text(encoding="utf-8")
            )
            validate(value)
        except (OSError, json.JSONDecodeError, ConfigError, TypeError) as error:
            raise DashboardError("invalid runtime configuration") from error
        return dict(value)

    def _now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DashboardError("dashboard clock must be timezone-aware")
        return value.astimezone(UTC)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.command_runner(
                args,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DashboardError(str(error) or "command unavailable") from error

    def history(
        self,
        skill: str | None,
        outcome: str | None,
    ) -> list[dict]:
        return self.history_store.read(
            now=self._now().isoformat(),
            skill=skill,
            outcome=outcome,
        )

    def snapshot(self) -> dict:
        args = [
            "python3",
            str(SCHEDULER),
            "status",
            "--project",
            self.project,
        ]
        result = self._run(args)
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, "scheduler status failed")
            )
        try:
            schedule = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise DashboardError("scheduler returned invalid status") from error
        if not isinstance(schedule, list):
            raise DashboardError("scheduler returned invalid status")

        records = self.history(None, None)
        latest_by_skill: dict[str, dict] = {}
        for record in records:
            latest_by_skill.setdefault(record["skill"], record)

        agents = []
        disabled_roles = []
        for entry in schedule:
            if not isinstance(entry, dict) or not isinstance(entry.get("skill"), str):
                raise DashboardError("scheduler returned invalid status")
            skill = entry["skill"]
            latest = latest_by_skill.get(skill)
            loaded = bool(entry.get("loaded"))
            interval = int(entry.get("interval_seconds", 0))
            estimated = None
            if latest is not None and interval > 0:
                try:
                    estimated = (
                        _timestamp(latest["finished_at"])
                        + timedelta(seconds=interval)
                    ).isoformat()
                except (KeyError, TypeError, ValueError):
                    estimated = None
            normalized = {
                **entry,
                "loaded": loaded,
                "running": bool(entry.get("running")),
                "interval_seconds": interval,
                "latest_history": latest,
                "health": "stopped" if not loaded else classify_record(latest),
                "estimated_next_pass": estimated,
            }
            if entry.get("enabled"):
                agents.append(normalized)
            else:
                disabled_roles.append(normalized)

        return {
            "project": self.project,
            "generated_at": self._now().isoformat(),
            "counts": {
                "enabled": len(agents),
                "disabled": len(disabled_roles),
            },
            "agents": agents,
            "disabled_roles": disabled_roles,
        }

    def _degraded_gitlab(self, error: str) -> dict:
        return {
            "degraded": True,
            "error": error,
            "last_successful_refresh": self._last_successful_refresh,
            "groups": {state: [] for state in LIFECYCLES},
            "merge_requests": [],
        }

    def _gitlab_api(self, path: str) -> list[dict]:
        try:
            result = self.command_runner(
                ["glab", "api", path],
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as error:
            raise DashboardError("glab unavailable") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise DashboardError("GitLab command failed") from error
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, "GitLab authentication failed")
            )
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise DashboardError("GitLab returned invalid JSON") from error
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise DashboardError("GitLab returned invalid JSON")
        return payload

    def gitlab_work(self, force_refresh: bool = False) -> dict:
        current = self._now()
        if (
            not force_refresh
            and self._gitlab_cache is not None
            and self._gitlab_cached_at is not None
            and current - self._gitlab_cached_at < timedelta(seconds=60)
        ):
            return self._gitlab_cache

        encoded = quote(self.gitlab_project, safe="")
        try:
            issues = self._gitlab_api(
                f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100"
            )
            merge_requests = self._gitlab_api(
                f"projects/{encoded}/merge_requests?scope=all&per_page=100"
            )
            groups = {state: [] for state in LIFECYCLES}
            mr_urls = {
                int(mr["iid"]): str(mr["web_url"])
                for mr in merge_requests
                if isinstance(mr.get("iid"), int)
                and isinstance(mr.get("web_url"), str)
            }
            for issue in issues:
                lifecycle = _label_value(issue.get("labels"), "pitcrew-state::")
                if lifecycle not in groups:
                    continue
                related = self._related_merge_requests(issue, merge_requests, mr_urls)
                groups[lifecycle].append(
                    {
                        **issue,
                        "lifecycle": lifecycle,
                        "route": _label_value(
                            issue.get("labels"),
                            "pitcrew-route::",
                        ),
                        "source": _label_value(
                            issue.get("labels"),
                            "pitcrew-source::",
                        ),
                        "related_merge_requests": related,
                    }
                )
            self._last_successful_refresh = current.isoformat()
            payload = {
                "degraded": False,
                "error": None,
                "last_successful_refresh": self._last_successful_refresh,
                "groups": groups,
                "merge_requests": merge_requests,
            }
        except DashboardError as error:
            payload = self._degraded_gitlab(str(error)[:2048])

        self._gitlab_cache = payload
        self._gitlab_cached_at = current
        return payload

    def _related_merge_requests(
        self,
        issue: dict,
        merge_requests: list[dict],
        mr_urls: dict[int, str],
    ) -> list[str]:
        issue_text = " ".join(
            (
                str(issue.get("web_url", "")),
                str(issue.get("references", "")),
                str(issue.get("description", "")),
            )
        )
        related = set(MR_URL.findall(issue_text))
        for iid in MR_REFERENCE.findall(issue_text):
            if int(iid) in mr_urls:
                related.add(mr_urls[int(iid)])

        issue_iid = issue.get("iid")
        issue_url = issue.get("web_url")
        if isinstance(issue_iid, int):
            issue_reference = re.compile(rf"(?<![\w#])#{issue_iid}\b")
            for merge_request in merge_requests:
                mr_text = " ".join(
                    (
                        str(merge_request.get("web_url", "")),
                        str(merge_request.get("references", "")),
                        str(merge_request.get("description", "")),
                    )
                )
                if (
                    isinstance(issue_url, str)
                    and issue_url
                    and issue_url in mr_text
                ) or issue_reference.search(mr_text):
                    url = merge_request.get("web_url")
                    if isinstance(url, str):
                        related.add(url)
        return sorted(related)

    def control(self, action: str, skill: str) -> dict:
        if action not in CONTROL_ACTIONS:
            raise DashboardError(f"unknown action: {action}")
        if skill in DISABLED_SKILLS:
            raise DashboardError(f"disabled role: {skill}")
        if skill not in ENABLED_SKILLS:
            raise DashboardError(f"unknown role: {skill}")

        if action == "trigger":
            try:
                process = subprocess.Popen(
                    [str(RUNNER), skill, self.project, "--scheduled"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as error:
                raise DashboardError("failed to trigger agent") from error
            return {"accepted": True, "pid": process.pid}

        scheduler_action = "stop" if action == "stop" else "install"
        result = self._run(
            [
                "python3",
                str(SCHEDULER),
                scheduler_action,
                "--project",
                self.project,
                "--skill",
                skill,
            ]
        )
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, f"failed to {action} agent")
            )
        return {"accepted": True}
