#!/usr/bin/env python3
"""Local dashboard aggregation for Pitcrew scheduler, history, and GitLab data."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from scripts.pitcrew_config import ConfigError, update_runtime_model, validate
from scripts.pitcrew_history import HistoryStore, classify_record
from scripts.pitcrew_proposals import ProposalError, ProposalStore
try:
    from scripts.pitcrew_models import (
        PRICING_CURRENCY,
        PRICING_EFFECTIVE_DATE,
        aggregate_usage,
        public_catalog,
        resolve_model,
        MODEL_CATALOG,
    )
except ModuleNotFoundError:
    from pitcrew_models import (
        PRICING_CURRENCY,
        PRICING_EFFECTIVE_DATE,
        aggregate_usage,
        public_catalog,
        resolve_model,
        MODEL_CATALOG,
    )


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"
SCHEDULER = ROOT / "bin/pitcrew-schedule.py"
RUNNER = ROOT / "bin/pitcrew-codex.sh"
LIFECYCLES = ("todo", "processing", "review", "blocked", "done")
CONTROL_ACTIONS = {"trigger", "stop", "restart"}
GLOBAL_CONTROL_ACTIONS = {"stop-all", "resume-all"}
MR_URL = re.compile(r"https?://[^\s<>'\"]+/-/merge_requests/\d+")
MR_REFERENCE = re.compile(r"(?<![\w!])!(\d+)\b")
TICKET_REFERENCE = re.compile(r"#(\d+)$")


class DashboardError(RuntimeError):
    pass


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _present_usage(usage: dict) -> dict:
    """Keep API token counts JSON-safe without changing aggregation semantics."""
    tokens = usage.get("tokens", {})
    return {
        **usage,
        "tokens": {
            field: str(value)
            for field, value in tokens.items()
        },
    }


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


def _skill_description(skill: str) -> str | None:
    """Read the short role description from an agent skill's frontmatter."""
    try:
        content = (SKILLS_ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter = re.search(r"\A---\n(.*?)\n---\n", content, re.DOTALL)
    if frontmatter is None:
        return None
    match = re.search(r"^description:\s*(.+)$", frontmatter.group(1), re.MULTILINE)
    if match is None:
        return None
    description = match.group(1).strip()
    if len(description) >= 2 and description[0] == description[-1] and description[0] in "'\"":
        description = description[1:-1]
    return description or None


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
        proposal_config = self.config.get("proposals", {})
        proposal_path = proposal_config.get("ledger") if isinstance(proposal_config, dict) else None
        self.proposals = ProposalStore(
            Path(proposal_path).expanduser()
            if isinstance(proposal_path, str) and proposal_path
            else self.runtime_dir / "proposals.json"
        )
        self._gitlab_cache: dict | None = None
        self._gitlab_cached_at: datetime | None = None
        self._last_successful_refresh: str | None = None
        self._schedule_cache: list[dict] | None = None
        self._control_lock = threading.RLock()

    def _load_config(self) -> dict:
        try:
            value = json.loads(
                (self.runtime_dir / "config.json").read_text(encoding="utf-8")
            )
            validate(value)
        except OSError as error:
            raise DashboardError(
                _redacted_error(str(error), "invalid runtime configuration")
            ) from error
        except (json.JSONDecodeError, ConfigError, TypeError) as error:
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
            raise DashboardError(
                _redacted_error(str(error), "command unavailable")
            ) from error

    def history(
        self,
        skill: str | None,
        outcome: str | None,
    ) -> list[dict]:
        try:
            return self.history_store.read(
                now=self._now().isoformat(),
                skill=skill,
                outcome=outcome,
            )
        except (OSError, ValueError) as error:
            raise DashboardError(
                _redacted_error(str(error), "history unavailable")
            ) from error

    def _gitlab_document(self, path: str) -> object:
        result = self.command_runner(
            ["glab", "api", path],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, "GitLab request failed")
            )
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise DashboardError("GitLab returned invalid JSON") from error

    def _gitlab_mutation(
        self,
        path: str,
        method: str,
        fields: dict[str, str] | None = None,
    ) -> str:
        args = ["glab", "api", path, "-X", method]
        for key, value in (fields or {}).items():
            args.extend(["-f", f"{key}={value}"])
        try:
            result = self.command_runner(
                args,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DashboardError(
                _redacted_error(str(error), "GitLab command failed")
            ) from error
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, "GitLab mutation failed")
            )
        return result.stdout

    def merge_merge_request(self, iid: int) -> dict:
        with self._control_lock:
            if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
                raise DashboardError("invalid merge request IID")
            encoded = quote(self.gitlab_project, safe="")
            request = self._gitlab_document(
                f"projects/{encoded}/merge_requests/{iid}"
            )
            if not isinstance(request, dict) or request.get("state") != "opened":
                raise DashboardError("merge request is no longer open")
            source_branch = request.get("source_branch")
            target_branch = request.get("target_branch")
            head_sha = request.get("sha")
            if not isinstance(head_sha, str) or not head_sha:
                diff_refs = request.get("diff_refs")
                head_sha = diff_refs.get("head_sha") if isinstance(diff_refs, dict) else None
            if not isinstance(source_branch, str) or not source_branch:
                raise DashboardError("merge request source branch is invalid")
            if not isinstance(head_sha, str) or not head_sha:
                raise DashboardError("merge request head SHA is unavailable")
            if target_branch in {"preprod", "prod"}:
                raise DashboardError("deployment merge requests require release workflow")

            self._gitlab_mutation(
                f"projects/{encoded}/merge_requests/{iid}/merge",
                "PUT",
                {"sha": head_sha},
            )
            try:
                self._gitlab_mutation(
                    f"projects/{encoded}/repository/branches/"
                    f"{quote(source_branch, safe='')}",
                    "DELETE",
                )
            except DashboardError as error:
                self._gitlab_cache = None
                self._gitlab_cached_at = None
                return {
                    "accepted": True,
                    "partial": True,
                    "iid": iid,
                    "source_branch": source_branch,
                    "warning": str(error),
                }
            self._gitlab_cache = None
            self._gitlab_cached_at = None
            return {
                "accepted": True,
                "partial": False,
                "iid": iid,
                "source_branch": source_branch,
                "target_branch": target_branch,
            }

    def decisions(self) -> dict:
        state_path = self.runtime_dir / "unblock-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"pending": None}
        except (OSError, json.JSONDecodeError) as error:
            raise DashboardError(
                _redacted_error(str(error), "decision state unavailable")
            ) from error
        pending = state.get("pending_question") if isinstance(state, dict) else None
        if not isinstance(pending, dict) or pending.get("status") != "blocked":
            return {"pending": None}
        ticket_id = pending.get("ticket_id")
        match = TICKET_REFERENCE.search(ticket_id) if isinstance(ticket_id, str) else None
        if match is None:
            raise DashboardError("pending decision has invalid ticket reference")
        encoded_project = quote(self.gitlab_project, safe="")
        iid = match.group(1)
        ticket = self._gitlab_document(f"projects/{encoded_project}/issues/{iid}")
        notes = self._gitlab_document(
            f"projects/{encoded_project}/issues/{iid}/notes?per_page=100"
        )
        if not isinstance(ticket, dict) or not isinstance(notes, list):
            raise DashboardError("pending decision context is invalid")
        findings = "\n\n".join(
            note.get("body", "")[:12000]
            for note in notes
            if isinstance(note, dict) and isinstance(note.get("body"), str)
        )[-24000:]
        return {
            "pending": {
                "ticket_id": ticket_id,
                "asked_at": pending.get("asked_at"),
                "question": pending.get("question", ""),
                "choices": pending.get("choices", []),
                "shape": pending.get("shape"),
                "context": pending.get("context", {}),
                "ticket": {
                    "title": ticket.get("title", "Ticket sans titre"),
                    "description": str(ticket.get("description", ""))[:12000],
                    "web_url": ticket.get("web_url"),
                },
                "findings": findings,
            }
        }

    def proposals_snapshot(self) -> dict:
        try:
            records = self.proposals.list()
        except ProposalError as error:
            raise DashboardError(str(error)) from error
        return {
            "proposals": [record for record in records if record.get("status") == "suggested"],
            "history": [record for record in records if record.get("status") != "suggested"],
        }

    def decide_proposal(self, proposal_id: str, action: str, reason: str = "") -> dict:
        if action not in {"approve", "reject", "investigate"}:
            raise DashboardError("invalid proposal action")
        if not isinstance(proposal_id, str) or not proposal_id:
            raise DashboardError("invalid proposal id")
        if action == "reject":
            status = "dismissed"
        elif action == "investigate":
            status = "investigate"
        else:
            status = "approved"
        try:
            current = next(
                proposal for proposal in self.proposals.list()
                if proposal.get("id") == proposal_id
            )
            metadata = None
            if status in {"approved", "investigate"}:
                issue = self._create_proposal_issue(current, investigate=status == "investigate")
                metadata = {"tracker": {
                    "iid": issue.get("iid"),
                    "web_url": issue.get("web_url"),
                }}
            updated = self.proposals.transition(
                proposal_id, status, actor="dashboard", reason=reason, metadata=metadata
            )
            return {"accepted": True, "proposal": updated}
        except ProposalError as error:
            raise DashboardError(str(error)) from error

    def _create_proposal_issue(self, proposal: dict, *, investigate: bool = False) -> dict:
        encoded = quote(self.gitlab_project, safe="")
        labels = [
            "pitcrew-agent",
            f"pitcrew-state::{'blocked' if investigate else 'todo'}",
            "pitcrew-proposal",
            "pitcrew-proposal::approved",
            f"pitcrew-category::{proposal['category']}",
        ]
        if investigate:
            labels.append("pitcrew-route::investigate")
        description = (
            f"## Résumé\n{proposal['summary']}\n\n"
            f"## Preuves\n" + "\n".join(f"- {item}" for item in proposal["evidence"]) +
            f"\n\n## Recommandation\n{proposal['recommendation']}"
        )
        result = self.command_runner(
            [
                "glab", "api", f"projects/{encoded}/issues", "-X", "POST",
                "-f", f"title={proposal['title']}",
                "-f", f"description={description}",
                "-f", f"labels={','.join(labels)}",
            ],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise DashboardError(_redacted_error(result.stderr, "GitLab proposal creation failed"))
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise DashboardError("GitLab returned invalid proposal issue JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("iid"), int):
            raise DashboardError("GitLab returned invalid proposal issue")
        return payload

    def submit_decision(self, ticket_id: str, answer: str, notes: str) -> dict:
        state_path = self.runtime_dir / "unblock-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DashboardError(
                _redacted_error(str(error), "decision state unavailable")
            ) from error
        pending = state.get("pending_question") if isinstance(state, dict) else None
        if (
            not isinstance(pending, dict)
            or pending.get("status") != "blocked"
            or pending.get("ticket_id") != ticket_id
            or not isinstance(answer, str)
            or answer not in pending.get("choices", [])
        ):
            raise DashboardError("decision is no longer available")
        pending.update(
            {
                "status": "answered",
                "answer": answer,
                "notes": notes[:4000],
                "answered_at": self._now().isoformat(),
            }
        )
        temporary = state_path.with_name(f".{state_path.name}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
        return {"accepted": True, "pid": self._trigger("unblock")}

    def _live_status(self, skill: str) -> dict | None:
        path = self.runtime_dir / "live" / f"{skill}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            started_at = value.get("started_at")
            pid = value.get("pid")
            if (
                value.get("project") != self.project
                or value.get("skill") != skill
                or not isinstance(started_at, str)
                or not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid <= 0
                or not isinstance(value.get("phase"), str)
            ):
                return None
            _timestamp(started_at)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return None
            except PermissionError:
                pass
            model = value.get("model")
            return {
                "project": self.project,
                "skill": skill,
                "model": model if isinstance(model, str) else None,
                "started_at": started_at,
                "pid": pid,
                "phase": value["phase"][:200],
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def snapshot(self) -> dict:
        schedule = self._schedule_entries(force_refresh=True)
        records = self.history(None, None)
        latest_by_skill: dict[str, dict] = {}
        records_by_skill: dict[str, list[dict]] = {}
        for record in records:
            latest_by_skill.setdefault(record["skill"], record)
            records_by_skill.setdefault(record["skill"], []).append(record)

        agents = []
        disabled_roles = []
        for entry in schedule:
            skill = entry["skill"]
            latest = latest_by_skill.get(skill)
            latest_measured = next(
                (
                    record
                    for record in records_by_skill.get(skill, [])
                    if aggregate_usage([record])["measured_runs"] == 1
                ),
                None,
            )
            loaded = bool(entry.get("loaded"))
            interval = int(entry.get("interval_seconds", 0))
            live_status = self._live_status(skill)
            running = bool(entry.get("running")) or live_status is not None
            estimated = None
            if loaded and latest is not None and interval > 0:
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
                "running": running,
                "role_description": _skill_description(skill),
                "live_status": live_status,
                "interval_seconds": interval,
                "latest_history": latest,
                "health": "stopped" if not loaded else classify_record(latest),
                "estimated_next_pass": estimated,
                "configured_model": resolve_model(self.config, skill),
                "latest_model": latest_measured["model"] if latest_measured is not None else None,
                "latest_usage": _present_usage(
                    aggregate_usage([latest_measured]) if latest_measured is not None else aggregate_usage([])
                ),
                "usage_7d": _present_usage(aggregate_usage(records_by_skill.get(skill, []))),
            }
            if entry.get("enabled"):
                agents.append(normalized)
            else:
                disabled_roles.append(normalized)

        return {
            "project": self.project,
            "generated_at": self._now().isoformat(),
            "global_state": self._global_state(schedule),
            "counts": {
                "enabled": len(agents),
                "disabled": len(disabled_roles),
            },
            "agents": agents,
            "disabled_roles": disabled_roles,
            "model_catalog": public_catalog(),
            "pricing": {
                "currency": PRICING_CURRENCY,
                "effective_date": PRICING_EFFECTIVE_DATE,
                "basis": "API standard token pricing estimate",
            },
            "usage_7d": _present_usage(aggregate_usage(records)),
        }

    def _schedule_entries(self, force_refresh: bool = False) -> list[dict]:
        if not force_refresh and self._schedule_cache is not None:
            return self._schedule_cache
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
        normalized = []
        seen = set()
        for entry in schedule:
            if not isinstance(entry, dict) or not isinstance(entry.get("skill"), str):
                raise DashboardError("scheduler returned invalid status")
            skill = entry["skill"]
            if skill in seen or not isinstance(entry.get("enabled"), bool):
                raise DashboardError("scheduler returned invalid status")
            seen.add(skill)
            normalized.append(dict(entry))
        self._schedule_cache = normalized
        return normalized

    def _enabled_entry(self, skill: str) -> dict:
        entry = next(
            (
                entry
                for entry in self._schedule_entries(force_refresh=True)
                if entry["skill"] == skill
            ),
            None,
        )
        if entry is None:
            raise DashboardError(f"unknown role: {skill}")
        if not entry["enabled"]:
            raise DashboardError(f"disabled role: {skill}")
        return entry

    def _global_state(self, schedule: list[dict] | None = None) -> str:
        entries = schedule if schedule is not None else self._schedule_entries(force_refresh=True)
        states = {entry.get("global_state", "running") for entry in entries}
        return "stopped" if states == {"stopped"} else "running"

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
            raise DashboardError(
                _redacted_error(str(error), "glab unavailable")
            ) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise DashboardError(
                _redacted_error(str(error), "GitLab command failed")
            ) from error
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

    def _gitlab_collection(self, path: str) -> list[dict]:
        collected = []
        for page in range(1, 101):
            batch = self._gitlab_api(f"{path}&page={page}")
            collected.extend(batch)
            if len(batch) < 100:
                return collected
        raise DashboardError("GitLab pagination limit exceeded")

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
            issues = self._gitlab_collection(
                f"projects/{encoded}/issues?scope=all&labels=pitcrew-agent&per_page=100"
            )
            merge_requests = self._gitlab_collection(
                f"projects/{encoded}/merge_requests?scope=all&per_page=100"
            )
            all_merge_requests = [
                self._normalize_merge_request(merge_request)
                for merge_request in merge_requests
            ]
            merge_requests = [
                merge_request
                for merge_request in all_merge_requests
                if merge_request.get("state") == "opened"
            ]
            groups = {state: [] for state in LIFECYCLES}
            mr_urls = {
                int(mr["iid"]): str(mr["web_url"])
                for mr in all_merge_requests
                if isinstance(mr.get("iid"), int)
                and isinstance(mr.get("web_url"), str)
            }
            for issue in issues:
                lifecycle = _label_value(issue.get("labels"), "pitcrew-state::")
                if lifecycle not in groups:
                    continue
                related = self._related_merge_requests(issue, all_merge_requests, mr_urls)
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

    @staticmethod
    def _normalize_merge_request(merge_request: dict) -> dict:
        author = merge_request.get("author")
        pipeline = merge_request.get("head_pipeline")
        normalized = dict(merge_request)
        normalized["author_username"] = (
            author.get("username")
            if isinstance(author, dict) and isinstance(author.get("username"), str)
            else None
        )
        normalized["pipeline_status"] = (
            pipeline.get("status")
            if isinstance(pipeline, dict) and isinstance(pipeline.get("status"), str)
            else None
        )
        return normalized

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
        allowed_urls = set(mr_urls.values())
        related = {
            url
            for url in MR_URL.findall(issue_text)
            if url in allowed_urls
        }
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

    def _scheduler_control(
        self,
        action: str,
        skill: str,
        *,
        error_action: str | None = None,
    ) -> None:
        result = self._run(
            [
                "python3",
                str(SCHEDULER),
                action,
                "--project",
                self.project,
                "--skill",
                skill,
            ]
        )
        if result.returncode:
            raise DashboardError(
                _redacted_error(
                    result.stderr,
                    f"failed to {error_action or action} agent",
                )
            )
        self._schedule_cache = None

    def _scheduler_global_control(self, action: str) -> None:
        result = self._run(
            [
                "python3",
                str(SCHEDULER),
                action,
                "--project",
                self.project,
            ]
        )
        if result.returncode:
            raise DashboardError(
                _redacted_error(result.stderr, f"failed to {action}")
            )
        self._schedule_cache = None

    def _trigger(self, skill: str) -> int:
        try:
            process = subprocess.Popen(
                [str(RUNNER), skill, self.project, "--scheduled"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            raise DashboardError(
                _redacted_error(str(error), "failed to trigger agent")
            ) from error
        return process.pid

    def control(self, action: str, skill: str) -> dict:
        with self._control_lock:
            if action not in CONTROL_ACTIONS:
                raise DashboardError(f"unknown action: {action}")
            self._enabled_entry(skill)
            if action == "trigger":
                return {"accepted": True, "pid": self._trigger(skill)}
            self._scheduler_control(
                "stop" if action == "stop" else "install",
                skill,
                error_action=action,
            )
            return {"accepted": True}

    def global_control(self, action: str) -> dict:
        with self._control_lock:
            if action not in GLOBAL_CONTROL_ACTIONS:
                raise DashboardError(f"unknown action: {action}")
            self._scheduler_global_control(action)
            return {
                "accepted": True,
                "global_state": "stopped" if action == "stop-all" else "running",
            }

    def change_model(self, skill: str, model: str) -> dict:
        with self._control_lock:
            self._enabled_entry(skill)
            if not isinstance(model, str) or model not in MODEL_CATALOG:
                raise DashboardError("unsupported model")
            self._scheduler_control("stop", skill)
            try:
                update_runtime_model(self.project, skill, model)
                self.config = self._load_config()
            except (ConfigError, OSError) as error:
                raise DashboardError(
                    _redacted_error(str(error), "failed to update agent model")
                ) from error
            self._scheduler_control("install", skill)
            return {
                "accepted": True,
                "pid": self._trigger(skill),
                "model": model,
            }
