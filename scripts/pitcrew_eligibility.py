#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlencode

if __package__:
    from scripts.pitcrew_config import load_runtime_config, runtime_root
else:
    from pitcrew_config import load_runtime_config, runtime_root


ProviderRun = Callable[[list[str]], subprocess.CompletedProcess[str]]
MAX_GITLAB_PAGES = 100
QUEUE_ROLES = {
    "implementer-run",
    "validator-run",
    "investigate-run",
    "unblock",
}


def result(
    decision: str,
    config: Mapping,
    skill: str,
    *,
    target_id: str | None,
    reason: str,
    fingerprint_source: object | None = None,
) -> dict:
    fingerprint = None
    if fingerprint_source is not None:
        serialized = json.dumps(
            fingerprint_source,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()
    return {
        "decision": decision,
        "project": config["project_name"],
        "skill": skill,
        "target_id": target_id,
        "fingerprint": fingerprint,
        "reason": reason,
    }


def default_provider_run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def gitlab_items(
    config: Mapping,
    endpoint: str,
    page: int,
    provider_run: ProviderRun,
) -> list[dict] | None:
    host = config["gitlab"]["host"]
    binary = os.environ.get("GLAB_BIN", "glab")
    paged_endpoint = f"{endpoint}&{urlencode({'page': page})}"
    try:
        completed = provider_run(
            [binary, "api", "--hostname", host, paged_endpoint]
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        return None
    return value


def issue_decision(
    config: Mapping,
    skill: str,
    provider_run: ProviderRun,
) -> dict:
    gitlab = config["gitlab"]
    tracker = gitlab["tracker"]
    labels = tracker["labels"]
    states = tracker["states"]
    endpoint = (
        f"projects/{quote(str(gitlab['project_id']), safe='')}/issues?"
        + urlencode(
            {
                "state": "opened",
                "labels": labels["agent"],
                "per_page": 100,
                "order_by": "iid",
                "sort": "asc",
            }
        )
    )
    required = {
        "implementer-run": (
            {labels["agent"], states["todo"]},
            {labels["agent"], states["review"]},
        ),
        "validator-run": ({labels["agent"], states["review"]},),
        "investigate-run": (
            {
                labels["agent"],
                labels["investigate"],
                states["todo"],
            },
        ),
        "unblock": ({labels["agent"], states["blocked"]},),
    }[skill]
    accumulated = []
    for page_number in range(1, MAX_GITLAB_PAGES + 1):
        page = gitlab_items(config, endpoint, page_number, provider_run)
        if (
            page is None
            or len(page) > 100
            or any(
                not isinstance(issue.get("iid"), int)
                or isinstance(issue.get("iid"), bool)
                or not isinstance(issue.get("labels"), list)
                or any(
                    not isinstance(label, str)
                    for label in issue.get("labels", [])
                )
                for issue in page
            )
        ):
            return result(
                "unavailable",
                config,
                skill,
                target_id=None,
                reason="configured GitLab issue probe failed",
            )
        accumulated.extend(page)
        candidates = []
        for issue in page:
            item_labels = set(issue["labels"])
            if any(
                expected.issubset(item_labels)
                for expected in required
            ):
                candidates.append(issue)
        if candidates:
            candidates.sort(key=lambda issue: issue["iid"])
            return result(
                "eligible",
                config,
                skill,
                target_id=(
                    f"{tracker['ticket_prefix']}{candidates[0]['iid']}"
                ),
                reason="first ascending issue is eligible",
                fingerprint_source=accumulated,
            )
        if len(page) < 100:
            return result(
                "empty",
                config,
                skill,
                target_id=None,
                reason="no eligible configured GitLab issue",
                fingerprint_source=accumulated,
            )
    return result(
        "unavailable",
        config,
        skill,
        target_id=None,
        reason="configured GitLab issue probe failed",
    )


def pending_unblock(
    runtime_dir: Path,
) -> tuple[str, str | None, object | None]:
    try:
        value = json.loads(
            (runtime_dir / "unblock-state.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return "missing", None, None
    except (OSError, json.JSONDecodeError):
        return "unavailable", None, None
    if not isinstance(value, dict):
        return "unavailable", None, None
    pending = value.get("pending_question")
    if pending is None:
        return "missing", None, None
    if not isinstance(pending, dict):
        return "unavailable", None, None
    status = pending.get("status")
    if not isinstance(status, str) or not status:
        return "unavailable", None, None
    if status in {"blocked", "selecting"}:
        return "pending", None, pending
    if status == "answered":
        ticket_id = pending.get("ticket_id")
        if isinstance(ticket_id, str) and ticket_id:
            return "answered", ticket_id, pending
        return "unavailable", None, None
    return "unavailable", None, None


def reviewer_decision(
    config: Mapping,
    skill: str,
    provider_run: ProviderRun,
) -> dict:
    gitlab = config["gitlab"]
    endpoint = (
        f"projects/{quote(str(gitlab['project_id']), safe='')}/merge_requests?"
        + urlencode(
            {
                "state": "opened",
                "author_username": gitlab["user"],
                "per_page": 100,
                "order_by": "updated_at",
                "sort": "asc",
            }
        )
    )
    accumulated = []
    for page_number in range(1, MAX_GITLAB_PAGES + 1):
        page = gitlab_items(config, endpoint, page_number, provider_run)
        if (
            page is None
            or len(page) > 100
            or any(
                not isinstance(item.get("iid"), int)
                or isinstance(item.get("iid"), bool)
                for item in page
            )
        ):
            return result(
                "unavailable",
                config,
                skill,
                target_id=None,
                reason="configured GitLab merge-request probe failed",
            )
        accumulated.extend(page)
        if page:
            candidates = sorted(page, key=lambda item: item["iid"])
            return result(
                "eligible",
                config,
                skill,
                target_id=(
                    f"{gitlab['project_path']}!{candidates[0]['iid']}"
                ),
                reason="an authored open merge request requires review",
                fingerprint_source=accumulated,
            )
        if len(page) < 100:
            return result(
                "empty",
                config,
                skill,
                target_id=None,
                reason="no authored open merge request requires review",
                fingerprint_source=accumulated,
            )
    return result(
        "unavailable",
        config,
        skill,
        target_id=None,
        reason="configured GitLab merge-request probe failed",
    )


def load_json_object(
    path: Path,
    *,
    missing: dict | None,
) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return missing
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def manager_decision(
    config: Mapping,
    skill: str,
    runtime_dir: Path,
) -> dict:
    manager = config.get("manager")
    if not isinstance(manager, Mapping) or not isinstance(
        manager.get("sources"), list
    ):
        return result(
            "unavailable",
            config,
            skill,
            target_id=None,
            reason="manager sources are not configured",
        )
    state = load_json_object(
        runtime_dir / "manager-state.json",
        missing={"filed": {}},
    )
    if state is None or not isinstance(state.get("filed"), dict):
        return result(
            "unavailable",
            config,
            skill,
            target_id=None,
            reason="manager state is unavailable",
        )

    keys = []
    for source in manager["sources"]:
        if (
            not isinstance(source, Mapping)
            or source.get("format") != "research-v1"
            or not isinstance(source.get("findings_json"), str)
            or not source["findings_json"]
        ):
            return result(
                "unavailable",
                config,
                skill,
                target_id=None,
                reason="manager source format is unavailable to preflight",
            )
        ledger = load_json_object(
            Path(source["findings_json"]),
            missing={"findings": []},
        )
        if ledger is None or not isinstance(ledger.get("findings"), list):
            return result(
                "unavailable",
                config,
                skill,
                target_id=None,
                reason="manager source ledger is unavailable",
            )
        for finding in ledger["findings"]:
            key = finding.get("key") if isinstance(finding, Mapping) else None
            if not isinstance(key, str) or not key:
                return result(
                    "unavailable",
                    config,
                    skill,
                    target_id=None,
                    reason="manager source finding is malformed",
                )
            keys.append(key)

    sorted_keys = sorted(keys)
    unfiled = [key for key in sorted_keys if key not in state["filed"]]
    if unfiled:
        return result(
            "eligible",
            config,
            skill,
            target_id=unfiled[0],
            reason="at least one configured finding is unfiled",
            fingerprint_source=sorted_keys,
        )
    return result(
        "empty",
        config,
        skill,
        target_id=None,
        reason="all configured findings are already filed",
        fingerprint_source=sorted_keys,
    )


def decide(
    config: Mapping,
    skill: str,
    *,
    runtime_dir: Path,
    provider_run: ProviderRun = default_provider_run,
) -> dict:
    if skill == "manager-run":
        return manager_decision(config, skill, runtime_dir)
    if skill == "reviewer-run":
        return reviewer_decision(config, skill, provider_run)
    if skill == "unblock":
        pending_status, target_id, fingerprint_source = pending_unblock(
            runtime_dir
        )
        if pending_status == "unavailable":
            return result(
                "unavailable",
                config,
                skill,
                target_id=None,
                reason="unblock state is unavailable",
            )
        if pending_status == "pending":
            return result(
                "empty",
                config,
                skill,
                target_id=None,
                reason="a human decision is pending",
                fingerprint_source=fingerprint_source,
            )
        if pending_status == "answered":
            return result(
                "eligible",
                config,
                skill,
                target_id=target_id,
                reason="an answered human decision is ready",
                fingerprint_source=fingerprint_source,
            )
    if skill in QUEUE_ROLES:
        return issue_decision(config, skill, provider_run)
    return result(
        "unavailable",
        config,
        skill,
        target_id=None,
        reason="this role has no Phase A deterministic eligibility probe",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--project", required=True)
    check_parser.add_argument("--skill", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_runtime_config(args.project)
        decision = decide(
            config,
            args.skill,
            runtime_dir=runtime_root() / args.project,
        )
    except (KeyError, OSError, TypeError, ValueError):
        print(
            "pitcrew eligibility: configuration or local probe is unavailable",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(decision, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
