#!/usr/bin/env python3
"""Local dashboard aggregation for Pitcrew scheduler, history, and forge data."""

from __future__ import annotations

import json
import os
import re
import signal
import shlex
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

from scripts.pitcrew_config import (
    ConfigError,
    EVENT_DRIVEN_ROLES,
    fix_autonomy,
    update_runtime_fix_autonomy,
    update_runtime_model,
    validate,
)
from scripts.pitcrew_config import max_concurrent_for
from scripts.pitcrew_history import HistoryStore, classify_record
from scripts.pitcrew_proposals import ProposalError, ProposalStore
from scripts.pitcrew_preprod_review import PreprodReviewError, ReportStore
from scripts.pitcrew_run_dispatcher import RunDispatcher
from scripts.pitcrew_run_store import RunStore, RunStoreError
from scripts.pitcrew_forge_work import (
    GitHubForgeWork,
    GitLabForgeWork,
    canonical_issue_target,
    ticket_agent_action,
)
try:
    from scripts.pitcrew_models import (
        PRICING_CURRENCY,
        PRICING_EFFECTIVE_DATE,
        aggregate_usage,
        public_catalog,
        resolve_model,
        resolve_reasoning_effort,
        MODEL_CATALOG,
    )
except ModuleNotFoundError:
    from pitcrew_models import (
        PRICING_CURRENCY,
        PRICING_EFFECTIVE_DATE,
        aggregate_usage,
        public_catalog,
        resolve_model,
        resolve_reasoning_effort,
        MODEL_CATALOG,
    )


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"
SCHEDULER = ROOT / "bin/pitcrew-schedule.py"
RUNNER = ROOT / "bin/pitcrew-codex.sh"
LIFECYCLES = ("todo", "processing", "review", "blocked", "done")
CONTROL_ACTIONS = {"trigger", "stop", "restart"}
GLOBAL_CONTROL_ACTIONS = {"stop-all", "resume-all"}
TICKET_AGENT_ACTIONS = {
    "todo": ("implementer-run", "Lancer l’implémentation"),
    "blocked": ("unblock", "Débloquer ce ticket"),
    "done": ("stale-sweep", "Vérifier la clôture"),
}
MR_URL = re.compile(r"https?://[^\s<>'\"]+/-/merge_requests/\d+")
MR_REFERENCE = re.compile(r"(?<![\w!])!(\d+)\b")
TICKET_REFERENCE = re.compile(r"#(\d+)$")
STATE_LABEL_PREFIX = "pitcrew-state::"


class DashboardError(RuntimeError):
    pass


def _forge_work_adapter(config: dict, command_runner: Callable):
    providers = config.get("providers")
    provider = providers.get("forge") if isinstance(providers, dict) else None
    if provider == "github":
        return GitHubForgeWork(config, command_runner)
    if provider == "gitlab":
        return GitLabForgeWork(config, command_runner)
    else:
        raise DashboardError("configured forge work adapter is unavailable")


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
        *,
        run_store: RunStore | None = None,
        run_dispatcher: RunDispatcher | None = None,
        forge_work_factory: Callable | None = None,
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
        review_config = self.config.get("preprod_review", {})
        history_limit = review_config.get("history_limit", 10) if isinstance(review_config, dict) else 10
        try:
            self.preprod_reports = ReportStore(
                self.runtime_dir / "preprod-review-reports.json", history_limit
            )
        except PreprodReviewError as error:
            raise DashboardError("invalid preprod review configuration") from error
        self._gitlab_cache: dict | None = None
        self._gitlab_cached_at: datetime | None = None
        self._forge_cache: dict | None = None
        self._forge_cached_at: datetime | None = None
        self.forge_work_factory = forge_work_factory or _forge_work_adapter
        self._last_successful_refresh: str | None = None
        self._schedule_cache: list[dict] | None = None
        self._control_lock = threading.RLock()
        self.run_store = run_store
        self.run_dispatcher = run_dispatcher
        self._run_store_error: Exception | None = None
        if self.run_store is None:
            try:
                self.run_store = RunStore(
                    self.runtime_dir / "runs.sqlite3",
                    now=self.now,
                )
            except (OSError, RunStoreError) as error:
                self._run_store_error = error
        if self.run_store is not None and self.run_dispatcher is None:
            self.run_dispatcher = RunDispatcher(
                store=self.run_store,
                runner=str(RUNNER),
            )
        self._preprod_process: subprocess.Popen | None = None

    @property
    def _unblock_state_path(self) -> Path:
        return self.runtime_dir / "state" / "unblock-state.json"

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
        state_path = self._unblock_state_path
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
        if not isinstance(ticket, dict):
            raise DashboardError("pending decision context is invalid")
        ticket_lifecycle = _label_value(ticket.get("labels"), STATE_LABEL_PREFIX)
        if ticket_lifecycle == "done" or ticket.get("state") == "closed":
            state["pending_question"] = None
            temporary_path = state_path.with_name(f"{state_path.name}.tmp")
            try:
                temporary_path.write_text(
                    json.dumps(state, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary_path.replace(state_path)
            except OSError as error:
                raise DashboardError(
                    _redacted_error(str(error), "decision state unavailable")
                ) from error
            return {"pending": None}
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
                    "resource_type": "issue",
                    "canonical_url": ticket.get("web_url"),
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
            manager_queued = current.get("category") == "architecture" and status in {"approved", "investigate"}
            metadata = None
            if status in {"approved", "investigate"} and not manager_queued:
                issue = self._create_proposal_issue(current, investigate=status == "investigate")
                metadata = {"tracker": {
                    "iid": issue.get("iid"),
                    "web_url": issue.get("web_url"),
                }}
            updated = self.proposals.transition(
                proposal_id, status, actor="dashboard", reason=reason, metadata=metadata
            )
            return {"accepted": True, "proposal": updated, "manager_queued": manager_queued}
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
        state_path = self._unblock_state_path
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

    def _preprod_process_running(self) -> bool:
        if self._preprod_process is None:
            return False
        try:
            if self._preprod_process.poll() is None:
                return True
        except (AttributeError, OSError):
            return True
        self._preprod_process = None
        return False

    def _verified_preprod_live_status(self) -> dict | None:
        live = self._live_status("preprod-review-run")
        if live is None:
            return None
        result = self._run(["ps", "-p", str(live["pid"]), "-o", "command="])
        if result.returncode:
            return None
        try:
            command = shlex.split(result.stdout.strip())
        except ValueError:
            return None
        live_path = str(self.runtime_dir / "live" / "preprod-review-run.json")
        def has_pair(flag: str, value: str) -> bool:
            return any(command[index:index + 2] == [flag, value] for index in range(len(command) - 1))
        if (
            not any(Path(item).name == "pitcrew_locked_exec.py" for item in command)
            or not has_pair("--project", self.project)
            or not has_pair("--skill", "preprod-review-run")
            or not has_pair("--live-file", live_path)
        ):
            return None
        return live

    def preprod_review_snapshot(self) -> dict:
        review_config = self.config.get("preprod_review")
        if not isinstance(review_config, dict):
            raise DashboardError("preprod review is not configured")
        try:
            reports = self.preprod_reports.read()["reports"]
        except PreprodReviewError as error:
            raise DashboardError("preprod review reports are unavailable") from error
        history = self.history("preprod-review-run", None)
        latest = reports[0] if reports else None
        latest_run = history[0] if history else None
        stale = False
        if latest_run and latest_run.get("outcome") in {"failed", "interrupted"}:
            try:
                run_at = _timestamp(latest_run["finished_at"])
                report_at = _timestamp(latest["completed_at"]) if latest else None
                stale = report_at is None or run_at > report_at
            except (KeyError, TypeError, ValueError):
                stale = True
        live_status = self._verified_preprod_live_status()
        schedule = self._schedule_entries(force_refresh=True)
        return {
            "skill": "preprod-review-run",
            "base_ref": review_config["base_ref"],
            "compare_ref": review_config["compare_ref"],
            "configured_model": resolve_model(self.config, "preprod-review-run"),
            "reasoning_effort": resolve_reasoning_effort(self.config, "preprod-review-run"),
            "running": live_status is not None or self._preprod_process_running(),
            "live_status": live_status,
            "global_state": self._global_state(schedule),
            "latest": latest,
            "history": reports,
            "latest_run": latest_run,
            "report_stale": stale,
        }

    def trigger_preprod_review(self) -> dict:
        with self._control_lock:
            snapshot = self.preprod_review_snapshot()
            if snapshot["global_state"] == "stopped":
                raise DashboardError("global execution is stopped")
            if snapshot["configured_model"] != "gpt-5.6-sol" or snapshot["reasoning_effort"] != "xhigh":
                raise DashboardError("preprod review policy is invalid")
            if snapshot["running"]:
                raise DashboardError("preprod review is already running")
            try:
                process = subprocess.Popen(
                    [str(RUNNER), "preprod-review-run", self.project],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as error:
                raise DashboardError(_redacted_error(str(error), "failed to trigger preprod review")) from error
            self._preprod_process = process
            return {"accepted": True, "pid": process.pid, "skill": "preprod-review-run"}

    def _stop_preprod_review(self, *, allow_absent: bool) -> dict | None:
        if self._preprod_process_running():
            process = self._preprod_process
            assert process is not None
            try:
                process.terminate()
            except OSError as error:
                raise DashboardError(_redacted_error(str(error), "failed to stop preprod review")) from error
            pid = process.pid
            self._preprod_process = None
            return {"accepted": True, "skill": "preprod-review-run", "pid": pid}
        live = self._verified_preprod_live_status()
        if live is None:
            if allow_absent:
                return None
            raise DashboardError("preprod review is not running")
        try:
            os.kill(live["pid"], signal.SIGTERM)
        except (OSError, ValueError) as error:
            raise DashboardError(_redacted_error(str(error), "failed to stop preprod review")) from error
        return {"accepted": True, "skill": "preprod-review-run", "pid": live["pid"]}

    def stop_preprod_review(self) -> dict:
        with self._control_lock:
            stopped = self._stop_preprod_review(allow_absent=False)
            assert stopped is not None
            return stopped

    def snapshot(self) -> dict:
        schedule = self._schedule_entries(force_refresh=True)
        scheduled_skills = {
            entry.get("skill")
            for entry in schedule
            if isinstance(entry, dict)
        }
        event_entries = [
            {
                "skill": skill,
                "interval_seconds": 0,
                "enabled": True,
                "reason": "",
                "label": None,
                "loaded": True,
                "running": False,
                "pid": None,
            }
            for skill in sorted(EVENT_DRIVEN_ROLES)
            if skill not in scheduled_skills
        ]
        records = self.history(None, None)
        latest_by_skill: dict[str, dict] = {}
        records_by_skill: dict[str, list[dict]] = {}
        for record in records:
            latest_by_skill.setdefault(record["skill"], record)
            records_by_skill.setdefault(record["skill"], []).append(record)

        agents = []
        disabled_roles = []
        for entry in [*schedule, *event_entries]:
            skill = entry["skill"]
            event_driven = skill in EVENT_DRIVEN_ROLES
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
            interval = None if event_driven else int(entry.get("interval_seconds", 0))
            live_status = self._live_status(skill)
            running = bool(entry.get("running")) or live_status is not None
            estimated = None
            if loaded and latest is not None and interval is not None and interval > 0:
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
                "trigger_mode": "event" if event_driven else "schedule",
                "restartable": not event_driven,
                "latest_history": latest,
                "health": "stopped" if not loaded else classify_record(latest),
                "estimated_next_pass": estimated,
                "configured_model": resolve_model(self.config, skill),
                "configured_reasoning_effort": resolve_reasoning_effort(self.config, skill),
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
            "fix_autonomy": fix_autonomy(self.config),
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
            "usage_total": _present_usage(self.history_store.usage_total(now=self._now().isoformat())),
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

    def _capacity_map(self) -> dict[str, int]:
        """Return the configured coordinator capacity for every enabled role."""
        return {
            entry["skill"]: max_concurrent_for(self.config, entry["skill"])
            for entry in self._schedule_entries()
            if entry.get("enabled")
        }

    def _claim_capacity_map(self) -> dict[str, int]:
        """Keep one configured slot occupied while a legacy worker is live."""
        capacities = self._capacity_map()
        for skill in tuple(capacities):
            if self._live_status(skill) is not None:
                capacities[skill] = max(0, capacities[skill] - 1)
        return capacities

    def runs_snapshot(self) -> dict:
        """Expose durable runs and account for the legacy single-worker files."""
        capacities = self._capacity_map()
        if self.run_store is None:
            raise DashboardError("ticket runs are unavailable") from self._run_store_error
        try:
            snapshot = self.run_store.snapshot(self.project, capacities)
        except RunStoreError as error:
            raise DashboardError("ticket runs are unavailable") from error
        for skill, capacity in snapshot["capacity"].items():
            if self._live_status(skill) is not None:
                capacity["running"] += 1
                capacity["legacy_running"] = 1
                snapshot["has_active"] = True
        return snapshot

    def _with_run_overlay(self, payload: dict) -> dict:
        """Apply volatile coordinator state without making cached GitLab stale."""
        groups = {
            lifecycle: [dict(issue) for issue in issues]
            for lifecycle, issues in payload["groups"].items()
        }
        result = {**payload, "groups": groups, "runs_degraded": False}
        try:
            active_by_target = {
                run["target"]: run
                for run in self.runs_snapshot()["runs"]
                if run["state"] in {"queued", "running"}
                and isinstance(run.get("target"), str)
            }
        except DashboardError:
            result["runs_degraded"] = True
            for issues in groups.values():
                for issue in issues:
                    action = issue.get("agent_action")
                    if isinstance(action, dict):
                        issue["active_run"] = None
                        issue["agent_action"] = {
                            **action,
                            "available": False,
                            "unavailable_reason": "État des tickets indisponible",
                        }
            return result
        for issues in groups.values():
            for issue in issues:
                target = issue.get("web_url")
                if isinstance(target, str):
                    try:
                        target, _ = self._canonical_ticket_target(target)
                    except DashboardError:
                        target = None
                active_run = active_by_target.get(target) if isinstance(target, str) else None
                issue["active_run"] = active_run
                action = issue.get("agent_action")
                if isinstance(action, dict) and active_run is not None:
                    issue["agent_action"] = {
                        **action,
                        "available": False,
                        "unavailable_reason": "Ticket en attente ou en cours",
                    }
        return result

    def _canonical_ticket_target(self, target: str) -> tuple[str, int]:
        parsed = urlsplit(target)
        configured_host = str(self.config["gitlab"].get("host", ""))
        prefixes = (
            f"/{self.gitlab_project}/-/issues/",
            f"/{self.gitlab_project}/-/work_items/",
        )
        if (
            parsed.scheme != "https"
            or parsed.netloc != configured_host
            or parsed.query
            or parsed.fragment
        ):
            raise DashboardError("invalid ticket target")
        prefix = next(
            (candidate for candidate in prefixes if parsed.path.startswith(candidate)),
            None,
        )
        if prefix is None:
            raise DashboardError("invalid ticket target")
        raw_iid = parsed.path.removeprefix(prefix)
        if not raw_iid.isdigit() or int(raw_iid) <= 0:
            raise DashboardError("invalid ticket target")
        iid = int(raw_iid)
        return f"https://{configured_host}/{self.gitlab_project}/-/issues/{iid}", iid

    def _ticket_lifecycle(self, issue: object, iid: int) -> str:
        if not isinstance(issue, dict) or issue.get("iid") != iid or issue.get("state") != "opened":
            raise DashboardError("ticket is not eligible")
        lifecycle = _label_value(issue.get("labels"), STATE_LABEL_PREFIX)
        if lifecycle not in TICKET_AGENT_ACTIONS:
            raise DashboardError("ticket is not eligible")
        return lifecycle

    def _validate_ticket_run(self, skill: str, target: str) -> tuple[str, dict]:
        canonical, iid = self._canonical_ticket_target(target)
        encoded = quote(self.gitlab_project, safe="")
        issue = self._gitlab_document(f"projects/{encoded}/issues/{iid}")
        lifecycle = self._ticket_lifecycle(issue, iid)
        if TICKET_AGENT_ACTIONS[lifecycle][0] != skill:
            raise DashboardError("ticket is not eligible")
        return canonical, issue

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

    def _forge_cache_is_fresh(self, force_refresh: bool) -> bool:
        return bool(
            not force_refresh
            and self._forge_cache is not None
            and self._forge_cached_at is not None
            and self._now() - self._forge_cached_at < timedelta(seconds=60)
        )

    def _cache_forge_work(self, payload: dict) -> dict:
        self._forge_cache = payload
        self._forge_cached_at = self._now()
        return payload

    def _forge_tracker_labels(self) -> dict[str, str]:
        providers = self.config.get("providers")
        if not isinstance(providers, dict):
            raise DashboardError("configured forge work is invalid")
        tracker = providers.get("tracker")
        binding = self.config.get(tracker) if isinstance(tracker, str) else None
        tracker_config = binding.get("tracker") if isinstance(binding, dict) else None
        labels = tracker_config.get("labels") if isinstance(tracker_config, dict) else None
        if not isinstance(labels, dict):
            raise DashboardError("configured forge work is invalid")
        return {key: value for key, value in labels.items() if isinstance(value, str)}

    def _with_normalized_actions(self, payload: dict) -> dict:
        groups = {
            lifecycle: [dict(issue) for issue in issues]
            for lifecycle, issues in payload.get("groups", {}).items()
            if isinstance(issues, list)
        }
        result = {**payload, "groups": groups, "runs_degraded": False}
        try:
            schedule = self._schedule_entries(force_refresh=True)
            enabled_skills = {entry["skill"] for entry in schedule if entry.get("enabled")}
            globally_stopped = self._global_state(schedule) == "stopped"
            active_by_target = {
                run["target"]: run
                for run in self.runs_snapshot()["runs"]
                if run.get("state") in {"queued", "running"}
                and isinstance(run.get("target"), str)
            }
        except DashboardError:
            enabled_skills = set()
            globally_stopped = True
            active_by_target = {}
            result["runs_degraded"] = True
        labels = self._forge_tracker_labels()
        for issues in groups.values():
            for issue in issues:
                try:
                    canonical = canonical_issue_target(self.config, issue.get("canonical_url"))
                except Exception:
                    issue["agent_action"] = None
                    continue
                issue["canonical_url"] = canonical
                active_run = active_by_target.get(canonical)
                issue["active_run"] = active_run
                issue["agent_action"] = ticket_agent_action(
                    issue=issue,
                    labels=labels,
                    enabled_skills=enabled_skills,
                    active_run=active_run,
                    globally_stopped=globally_stopped,
                )
        return result

    def forge_work(self, force_refresh: bool = False) -> dict:
        if self._forge_cache_is_fresh(force_refresh):
            return self._with_normalized_actions(self._forge_cache)
        adapter = self.forge_work_factory(self.config, self.command_runner)
        try:
            collected = adapter.collect()
        except Exception as error:
            raise DashboardError("configured forge work is unavailable") from error
        if not isinstance(collected, dict):
            raise DashboardError("configured forge work is invalid")
        return self._with_normalized_actions(self._cache_forge_work(collected))

    def gitlab_work(self, force_refresh: bool = False) -> dict:
        current = self._now()
        if (
            not force_refresh
            and self._gitlab_cache is not None
            and self._gitlab_cached_at is not None
            and current - self._gitlab_cached_at < timedelta(seconds=60)
        ):
            return self._with_run_overlay(self._gitlab_cache)

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
            schedule_by_skill = {
                entry["skill"]: entry
                for entry in self._schedule_entries(force_refresh=True)
            }
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
                reconciliation_candidate = self._merged_reconciliation_candidate(
                    issue,
                    related,
                    all_merge_requests,
                )
                agent_action = None
                action_spec = TICKET_AGENT_ACTIONS.get(lifecycle)
                if action_spec is not None and isinstance(issue.get("web_url"), str):
                    skill, label = action_spec
                    entry = schedule_by_skill.get(skill)
                    available = bool(
                        entry
                        and entry.get("enabled")
                        and self._global_state(list(schedule_by_skill.values())) != "stopped"
                    )
                    agent_action = {
                        "skill": skill,
                        "label": label,
                        "target": issue["web_url"],
                        "available": available,
                        "unavailable_reason": None if available else "Agent indisponible",
                    }
                groups[lifecycle].append(
                    {
                        **issue,
                        "resource_type": "issue",
                        "canonical_url": issue.get("web_url"),
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
                        "reconciliation_candidate": reconciliation_candidate,
                        "active_run": None,
                        "agent_action": agent_action,
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
        return self._with_run_overlay(payload)

    def _merged_reconciliation_candidate(
        self,
        issue: dict,
        related_urls: list[str],
        merge_requests: list[dict],
    ) -> dict | None:
        target = issue.get("web_url")
        issue_iid = issue.get("iid")
        if (
            issue.get("state") != "opened"
            or not isinstance(target, str)
            or not isinstance(issue_iid, int)
            or isinstance(issue_iid, bool)
            or issue_iid <= 0
        ):
            return None
        merged = next(
            (
                merge_request
                for merge_request in merge_requests
                if merge_request.get("state") == "merged"
                and isinstance(merge_request.get("merged_at"), str)
                and isinstance(merge_request.get("iid"), int)
                and not isinstance(merge_request.get("iid"), bool)
                and merge_request.get("iid") > 0
                and isinstance(merge_request.get("web_url"), str)
                and merge_request.get("web_url") in related_urls
            ),
            None,
        )
        if merged is None:
            return None
        try:
            _timestamp(merged["merged_at"])
        except (TypeError, ValueError):
            return None
        return {
            "skill": "stale-sweep",
            "target": target,
            "reason": "merged_merge_request",
            "evidence": {
                "provider": "gitlab",
                "project_path": self.gitlab_project,
                "issue_iid": issue_iid,
                "merge_request_iid": merged["iid"],
                "merge_request_url": merged["web_url"],
                "merged_at": merged["merged_at"],
            },
        }

    def _validated_merged_reconciliation_candidate(
        self,
        candidate: object,
    ) -> str:
        if not isinstance(candidate, dict) or set(candidate) != {
            "skill",
            "target",
            "reason",
            "evidence",
        }:
            raise DashboardError("invalid reconciliation candidate")
        if (
            candidate.get("skill") != "stale-sweep"
            or candidate.get("reason") != "merged_merge_request"
        ):
            raise DashboardError("invalid reconciliation candidate")
        target = candidate.get("target")
        if not isinstance(target, str):
            raise DashboardError("invalid reconciliation candidate")
        canonical, issue_iid = self._canonical_ticket_target(target)
        evidence = candidate.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != {
            "provider",
            "project_path",
            "issue_iid",
            "merge_request_iid",
            "merge_request_url",
            "merged_at",
        }:
            raise DashboardError("invalid reconciliation candidate")
        merge_request_iid = evidence.get("merge_request_iid")
        if (
            evidence.get("provider") != "gitlab"
            or evidence.get("project_path") != self.gitlab_project
            or evidence.get("issue_iid") != issue_iid
            or not isinstance(merge_request_iid, int)
            or isinstance(merge_request_iid, bool)
            or merge_request_iid <= 0
        ):
            raise DashboardError("invalid reconciliation candidate")
        expected_mr_url = (
            f"https://{self.config['gitlab']['host']}/{self.gitlab_project}"
            f"/-/merge_requests/{merge_request_iid}"
        )
        if evidence.get("merge_request_url") != expected_mr_url:
            raise DashboardError("invalid reconciliation candidate")
        merged_at = evidence.get("merged_at")
        if not isinstance(merged_at, str):
            raise DashboardError("invalid reconciliation candidate")
        try:
            _timestamp(merged_at)
        except ValueError as error:
            raise DashboardError("invalid reconciliation candidate") from error
        return canonical

    def reconcile_merged_ticket_candidates(self) -> dict:
        """Admit read-side merged drift as coordinated stale-sweep work."""
        payload = self.gitlab_work(force_refresh=True)
        if not isinstance(payload, dict) or payload.get("degraded") is not False:
            raise DashboardError("GitLab reconciliation unavailable")
        groups = payload.get("groups")
        if not isinstance(groups, dict):
            raise DashboardError("GitLab reconciliation unavailable")
        candidates = []
        for lifecycle in LIFECYCLES:
            issues = groups.get(lifecycle)
            if not isinstance(issues, list):
                raise DashboardError("GitLab reconciliation unavailable")
            for issue in issues:
                if isinstance(issue, dict) and issue.get("reconciliation_candidate") is not None:
                    candidates.append(issue["reconciliation_candidate"])

        with self._control_lock:
            if self.run_store is None or self.run_dispatcher is None:
                raise DashboardError("ticket runs are unavailable") from self._run_store_error
            admissions: dict[str, dict] = {}
            deferred = []
            rejected = []
            should_drain = False
            try:
                for candidate in candidates:
                    try:
                        canonical = self._validated_merged_reconciliation_candidate(
                            candidate
                        )
                    except DashboardError:
                        rejected.append(
                            {
                                "target": (
                                    candidate.get("target")
                                    if isinstance(candidate, dict)
                                    and isinstance(candidate.get("target"), str)
                                    else None
                                ),
                                "reason": "invalid_candidate",
                            }
                        )
                        continue
                    run = self.run_store.enqueue(
                        project=self.project,
                        skill="stale-sweep",
                        source="reconcile",
                        target=canonical,
                    )
                    current = self.run_store.get(run["run_id"])
                    if current is None:
                        raise DashboardError("ticket run is unavailable")
                    if current["skill"] != "stale-sweep":
                        deferred.append(
                            {
                                "target": canonical,
                                "run_id": current["run_id"],
                                "skill": current["skill"],
                                "state": current["state"],
                                "reason": "active_ticket_run",
                            }
                        )
                        continue
                    existing = admissions.get(current["run_id"])
                    admissions[current["run_id"]] = {
                        "target": canonical,
                        "created": (
                            run["created"]
                            if existing is None
                            else existing["created"] or run["created"]
                        ),
                    }
                    should_drain = should_drain or current["state"] == "queued"
                if should_drain:
                    self.run_dispatcher.reconcile_and_drain(
                        project=self.project,
                        capacities=self._claim_capacity_map(),
                    )
                runs = []
                for run_id, admission in admissions.items():
                    current = self.run_store.get(run_id)
                    if current is None:
                        raise DashboardError("ticket run is unavailable")
                    runs.append(
                        {
                            "run_id": current["run_id"],
                            "skill": current["skill"],
                            "target": admission["target"],
                            "state": current["state"],
                            "queue_position": current["queue_position"],
                            "created": admission["created"],
                        }
                    )
            except RunStoreError as error:
                raise DashboardError("ticket run is unavailable") from error
            return {
                "runs": runs,
                "deferred": deferred,
                "rejected": rejected,
                "drained": should_drain,
            }

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
        normalized["has_conflicts"] = merge_request.get("has_conflicts") is True
        normalized["detailed_merge_status"] = (
            merge_request.get("detailed_merge_status")
            if isinstance(merge_request.get("detailed_merge_status"), str)
            else None
        )
        normalized["resource_type"] = "merge_request"
        normalized["canonical_url"] = (
            merge_request.get("web_url")
            if isinstance(merge_request.get("web_url"), str)
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

    def launch_ticket_agent(self, skill: str, target: str) -> dict:
        with self._control_lock:
            try:
                canonical = canonical_issue_target(self.config, target)
            except Exception as error:
                raise DashboardError("invalid ticket target") from error
            work = self.forge_work(force_refresh=True)
            issue = next(
                (
                    issue
                    for issues in work.get("groups", {}).values()
                    if isinstance(issues, list)
                    for issue in issues
                    if isinstance(issue, dict) and issue.get("canonical_url") == canonical
                ),
                None,
            )
            action = issue.get("agent_action") if isinstance(issue, dict) else None
            if (
                not isinstance(action, dict)
                or action.get("skill") != skill
                or action.get("target") != canonical
                or (
                    not action.get("available")
                    and action.get("run_state") not in {"queued", "running"}
                )
            ):
                raise DashboardError("ticket is not eligible")
            if self.run_store is None or self.run_dispatcher is None:
                raise DashboardError("ticket runs are unavailable") from self._run_store_error
            try:
                run = self.run_store.enqueue(
                    project=self.project,
                    skill=skill,
                    source="dashboard",
                    target=canonical,
                )
                current = self.run_store.get(run["run_id"])
                if current is None:
                    raise DashboardError("ticket run is unavailable")
                if run["created"] or current["state"] == "queued":
                    self.run_dispatcher.reconcile_and_drain(
                        project=self.project,
                        capacities=self._claim_capacity_map(),
                    )
                    current = self.run_store.get(run["run_id"])
            except RunStoreError as error:
                raise DashboardError("ticket run is unavailable") from error
            if current is None:
                raise DashboardError("ticket run is unavailable")
            return {
                "accepted": True,
                "run_id": current["run_id"],
                "state": current["state"],
                "skill": skill,
                "target": canonical,
            }

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
            if action == "stop-all":
                self._stop_preprod_review(allow_absent=True)
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

    def set_fix_autonomy(self, mode: str) -> dict:
        with self._control_lock:
            try:
                update_runtime_fix_autonomy(self.project, mode)
                self.config = self._load_config()
            except (ConfigError, OSError) as error:
                raise DashboardError(
                    _redacted_error(str(error), "failed to update autonomous fixes policy")
                ) from error
            return {"accepted": True, "fix_autonomy": fix_autonomy(self.config)}
