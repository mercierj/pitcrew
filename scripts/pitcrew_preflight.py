#!/usr/bin/env python3
"""Deterministic scheduled-run gate for known provider failures."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
import tempfile
from pathlib import Path

if __package__:
    from scripts.pitcrew_history import append_gate_record
else:
    from pitcrew_history import append_gate_record


DEFAULT_COOLDOWN_SECONDS = 1800


def runtime_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "pitcrew"
    return Path(os.environ["HOME"]).expanduser() / ".codex" / "pitcrew"


def state_path(project: str) -> Path:
    return runtime_root() / project / "state" / "provider-circuit.json"


def parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)


def cooldown_seconds() -> int:
    raw = os.environ.get("PITCREW_PROVIDER_COOLDOWN_SECONDS")
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    value = int(raw)
    if value < 0:
        raise ValueError("cooldown must not be negative")
    return value


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("provider circuit state must be an object")
    return value


def active_cooldown(entry: object, current: datetime, seconds: int) -> datetime | None:
    if not isinstance(entry, dict):
        raise ValueError("provider circuit cooldown is malformed")
    failed_at_raw = entry.get("failed_at")
    reason = entry.get("reason")
    if not isinstance(failed_at_raw, str) or not isinstance(reason, str) or not reason:
        raise ValueError("provider circuit cooldown is malformed")
    expires_at = parse_timestamp(failed_at_raw) + timedelta(seconds=seconds)
    return expires_at if current < expires_at else None


def write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check(project: str, skill: str) -> dict:
    path = state_path(project)
    state = read_state(path)
    current = now()
    provider_entry = (
        {"failed_at": state.get("failed_at"), "reason": state.get("reason")}
        if "failed_at" in state
        else state.get("provider")
    )
    expires_at = active_cooldown(provider_entry, current, cooldown_seconds()) if provider_entry else None
    if expires_at is not None:
        return {
            "decision": "noop",
            "reason": "provider failure cooldown is active",
            "project": project,
            "skill": skill,
            "next_action": f"retry after {expires_at.isoformat().replace('+00:00', 'Z')}",
        }
    skill_entry = state.get("skills", {}).get(skill) if isinstance(state.get("skills"), dict) else None
    expires_at = active_cooldown(skill_entry, current, cooldown_seconds()) if skill_entry else None
    if expires_at is not None:
        return {
            "decision": "noop",
            "reason": "no-op cooldown is active for this skill",
            "project": project,
            "skill": skill,
            "next_action": f"retry after {expires_at.isoformat().replace('+00:00', 'Z')}",
        }
    return {"decision": "run", "project": project, "skill": skill}


def record_failure(project: str, reason: str, failed_at: str | None) -> dict:
    timestamp = failed_at or now().isoformat().replace("+00:00", "Z")
    parse_timestamp(timestamp)
    path = state_path(project)
    previous = read_state(path)
    write_state(path, {"provider": {"failed_at": timestamp, "reason": reason}, "skills": previous.get("skills", {})})
    return {"status": "recorded", "project": project, "failed_at": timestamp}


def record_noop(project: str, skill: str, reason: str, failed_at: str | None) -> dict:
    timestamp = failed_at or now().isoformat().replace("+00:00", "Z")
    parse_timestamp(timestamp)
    path = state_path(project)
    previous = read_state(path)
    skills = previous.get("skills", {})
    if not isinstance(skills, dict):
        raise ValueError("provider circuit skills state is malformed")
    skills = dict(skills)
    skills[skill] = {"failed_at": timestamp, "reason": reason}
    provider = previous.get("provider")
    if provider is None and "failed_at" in previous:
        provider = {"failed_at": previous["failed_at"], "reason": previous["reason"]}
    state = {"skills": skills}
    if provider is not None:
        state["provider"] = provider
    write_state(path, state)
    return {"status": "recorded", "project": project, "skill": skill, "failed_at": timestamp}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--project", required=True)
    check_parser.add_argument("--skill", required=True)
    failure_parser = subparsers.add_parser("record-provider-failure")
    failure_parser.add_argument("--project", required=True)
    failure_parser.add_argument("--reason", required=True)
    failure_parser.add_argument("--at")
    noop_parser = subparsers.add_parser("record-noop")
    noop_parser.add_argument("--project", required=True)
    noop_parser.add_argument("--skill", required=True)
    noop_parser.add_argument("--reason", required=True)
    noop_parser.add_argument("--at")
    gate_parser = subparsers.add_parser("record-gate")
    gate_parser.add_argument("--project", required=True)
    gate_parser.add_argument("--skill", required=True)
    gate_parser.add_argument("--decision", required=True)
    gate_parser.add_argument("--reason", required=True)
    gate_parser.add_argument(
        "--outcome",
        choices=("noop", "failed"),
        default="noop",
    )
    gate_parser.add_argument("--target-id")
    gate_parser.add_argument("--fingerprint")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check(args.project, args.skill)
        elif args.command == "record-provider-failure":
            result = record_failure(args.project, args.reason, args.at)
        elif args.command == "record-gate":
            result = append_gate_record(
                runtime_root() / args.project / "history.jsonl",
                project=args.project,
                skill=args.skill,
                decision=args.decision,
                reason=args.reason,
                outcome=args.outcome,
                target_id=args.target_id,
                fingerprint=args.fingerprint,
            )
        else:
            result = record_noop(args.project, args.skill, args.reason, args.at)
    except (OSError, ValueError, KeyError) as error:
        print(f"pitcrew preflight: {error}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
