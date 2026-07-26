#!/usr/bin/env python3
"""Locked, bounded JSONL storage for Pitcrew run history."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

try:
    from scripts.pitcrew_models import MODEL_CATALOG, aggregate_usage
except ModuleNotFoundError:
    from pitcrew_models import MODEL_CATALOG, aggregate_usage


REQUIRED_STRING_FIELDS = (
    "project",
    "skill",
    "started_at",
    "finished_at",
    "outcome",
    "summary",
)
VALID_OUTCOMES = {"success", "noop", "failed", "interrupted"}
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)
OPTIONAL_STRING_FIELDS = (
    "routing_reason",
    "target_id",
    "work_kind",
    "quality_outcome",
    "gate_decision",
    "gate_reason",
    "fingerprint",
)
OPTIONAL_BOOLEAN_FIELDS = ("model_invoked", "did_work")
VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
VALID_ROUTING_MODES = {"fixed", "observe"}
BENIGN_NOOP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\s*no eligible item"
        r"(?:\s*;\s*no approval(?:\s+\w+){0,3}\s+required)?[.!]?\s*",
        r"\s*no approval(?:\s+\w+){0,3}\s+required[.!]?\s*",
        r"\s*nothing missing[.!]?\s*",
        r"\s*(?:provider check completed\s*;\s*)?"
        r"no permissions?(?:\s+\w+){0,3}\s+required[.!]?\s*",
    )
)
ACTIONABLE_NOOP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brequired\b",
        r"\b(?:unavailable|not\s+available|missing|authentication|failed|"
        r"unauthenticated|unauth['’]d)\b",
        r"\bpermissions?\s+(?:(?:is|are|was|were)\s+)?"
        r"(?:denied|required)\b",
    )
)


def _decode_summary(summary: object) -> object:
    try:
        return json.loads(summary)
    except json.JSONDecodeError:
        if not isinstance(summary, str):
            return summary
        try:
            decoded, _ = json.JSONDecoder().raw_decode(summary.lstrip())
        except json.JSONDecodeError:
            return summary
        return decoded
    except TypeError:
        return summary


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"invalid timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _validate_record(record: object) -> dict:
    if not isinstance(record, dict):
        raise ValueError("history record must be an object")
    normalized = dict(record)
    for field in REQUIRED_STRING_FIELDS:
        if not isinstance(normalized.get(field), str) or not normalized[field]:
            raise ValueError(f"history record field {field} must be a non-empty string")
    if normalized["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"invalid history outcome: {normalized['outcome']}")
    _parse_timestamp(normalized["started_at"])
    _parse_timestamp(normalized["finished_at"])
    model = normalized.get("model")
    if "model" in normalized and (
        not isinstance(model, str) or model not in MODEL_CATALOG
    ):
        normalized.pop("model", None)
        normalized.pop("usage", None)
    for field in OPTIONAL_STRING_FIELDS:
        if field in normalized and (
            not isinstance(normalized[field], str) or not normalized[field]
        ):
            normalized.pop(field)
    for field in OPTIONAL_BOOLEAN_FIELDS:
        if field in normalized and not isinstance(normalized[field], bool):
            normalized.pop(field)
    if normalized.get("reasoning_effort") not in VALID_REASONING_EFFORTS:
        normalized.pop("reasoning_effort", None)
    if normalized.get("routing_mode") not in VALID_ROUTING_MODES:
        normalized.pop("routing_mode", None)
    candidate_model = normalized.get("candidate_model")
    if candidate_model not in MODEL_CATALOG:
        normalized.pop("candidate_model", None)
    usage = normalized.get("usage")
    if not isinstance(usage, dict) or set(usage) != set(USAGE_FIELDS):
        normalized.pop("usage", None)
    elif any(
        isinstance(usage[field], bool)
        or not isinstance(usage[field], int)
        or usage[field] < 0
        for field in USAGE_FIELDS
    ):
        normalized.pop("usage", None)
    else:
        normalized["usage"] = dict(usage)
    return normalized


class HistoryStore:
    def __init__(self, path: Path, retention_days: int = 7):
        self.path = Path(path)
        self.retention_days = retention_days
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.usage_total_path = self.path.with_name(f"{self.path.name}.usage-total.json")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_valid_records(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

        records = []
        for line in lines:
            try:
                record = json.loads(line)
                records.append(_validate_record(record))
            except (json.JSONDecodeError, ValueError):
                continue
        return records

    def _retained(self, records: list[dict], now: datetime) -> list[dict]:
        cutoff = now - timedelta(days=self.retention_days)
        return [
            record
            for record in records
            if _parse_timestamp(record["finished_at"]) >= cutoff
        ]

    def _replace(self, records: list[dict]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                for record in records:
                    temporary.write(json.dumps(record, separators=(",", ":")) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _valid_usage_total(value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        expected = aggregate_usage([])
        if set(value) != set(expected) or not isinstance(value.get("tokens"), dict):
            return None
        if set(value["tokens"]) != set(expected["tokens"]):
            return None
        for field in ("measured_runs", "unmeasured_runs"):
            if isinstance(value.get(field), bool) or not isinstance(value.get(field), int) or value[field] < 0:
                return None
        for field in expected["tokens"]:
            if isinstance(value["tokens"].get(field), bool) or not isinstance(value["tokens"].get(field), int) or value["tokens"][field] < 0:
                return None
        try:
            cost = Decimal(value["estimated_cost_usd"])
            if not cost.is_finite() or cost < 0:
                return None
        except (InvalidOperation, TypeError):
            return None
        return value

    def _read_usage_total(self) -> dict | None:
        try:
            value = json.loads(self.usage_total_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return self._valid_usage_total(value)

    def _write_usage_total(self, value: dict) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.usage_total_path.name}.",
            dir=self.usage_total_path.parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                temporary.write(json.dumps(value, separators=(",", ":")) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.usage_total_path)
            os.chmod(self.usage_total_path, 0o600)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _merge_usage_totals(current: dict, addition: dict) -> dict:
        cost = Decimal(current["estimated_cost_usd"]) + Decimal(addition["estimated_cost_usd"])
        return {
            "measured_runs": current["measured_runs"] + addition["measured_runs"],
            "unmeasured_runs": current["unmeasured_runs"] + addition["unmeasured_runs"],
            "tokens": {
                field: current["tokens"][field] + addition["tokens"][field]
                for field in current["tokens"]
            },
            "estimated_cost_usd": format(cost.quantize(Decimal("0.000001")), "f"),
        }

    def usage_total(self, now: str | None = None) -> dict:
        current_time = _parse_timestamp(now) if now is not None else datetime.now(UTC)
        with self._locked():
            records = self._retained(self._read_valid_records(), current_time)
            total = self._read_usage_total()
            if total is None:
                total = aggregate_usage(records)
                self._write_usage_total(total)
        return total

    def append(self, record: dict, now: str | None = None) -> None:
        normalized = _validate_record(record)
        current_time = _parse_timestamp(now) if now is not None else datetime.now(UTC)
        with self._locked():
            records = self._read_valid_records()
            retained = self._retained(records, current_time)
            total = self._read_usage_total()
            previous_total = total
            if total is None:
                total = aggregate_usage(retained)
            total = self._merge_usage_totals(total, aggregate_usage([normalized]))
            retained.append(normalized)
            try:
                self._write_usage_total(total)
                self._replace(self._retained(retained, current_time))
            except BaseException:
                if previous_total is None:
                    try:
                        self.usage_total_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    self._write_usage_total(previous_total)
                raise

    def read(
        self,
        *,
        now: str | None = None,
        skill: str | None = None,
        outcome: str | None = None,
    ) -> list[dict]:
        current_time = _parse_timestamp(now) if now is not None else datetime.now(UTC)
        with self._locked():
            valid_records = self._read_valid_records()
            records = self._retained(valid_records, current_time)
            if len(records) < len(valid_records):
                self._replace(records)
        if skill is not None:
            records = [record for record in records if record["skill"] == skill]
        if outcome is not None:
            records = [record for record in records if record["outcome"] == outcome]
        return sorted(
            records,
            key=lambda record: _parse_timestamp(record["finished_at"]),
            reverse=True,
        )


def classify_record(record: dict | None) -> str:
    if record is None:
        return "unknown"

    exit_code = record.get("exit_code")
    record_outcome = record.get("outcome")
    if exit_code not in (None, 0) or record_outcome in {"failed", "interrupted"}:
        return "failed"

    summary = record.get("summary", "")
    decoded = _decode_summary(summary)

    effective_outcome = record_outcome
    structured_status = decoded.get("status") if isinstance(decoded, dict) else None
    if isinstance(structured_status, str) and structured_status in VALID_OUTCOMES:
        effective_outcome = structured_status

    if effective_outcome in {"failed", "interrupted"}:
        return "failed"
    if effective_outcome == "success":
        return "healthy"
    if effective_outcome != "noop":
        return "unknown"

    if isinstance(decoded, dict):
        reason = decoded.get("reason", "")
    else:
        reason = decoded
    reason_text = str(reason)
    if any(pattern.fullmatch(reason_text) for pattern in BENIGN_NOOP_PATTERNS):
        return "healthy"
    if any(pattern.search(reason_text) for pattern in ACTIONABLE_NOOP_PATTERNS):
        return "warning"
    return "healthy"


def utc_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def append_gate_record(
    history_path: Path,
    *,
    project: str,
    skill: str,
    decision: str,
    reason: str,
    outcome: str,
    target_id: str | None,
    fingerprint: str | None,
) -> dict:
    if outcome not in {"noop", "failed"}:
        raise ValueError("pre-model outcome must be noop or failed")
    timestamp = utc_now()
    summary = {
        "status": outcome,
        "reason": reason,
        "project": project,
        "skill": skill,
        "target_id": target_id,
        "did_work": False,
        "work_kind": "none",
        "quality_outcome": "not-applicable",
        "next_action": "retry after the configured interval",
    }
    record = {
        "project": project,
        "skill": skill,
        "model_invoked": False,
        "started_at": timestamp,
        "finished_at": timestamp,
        "duration_ms": 0,
        "outcome": outcome,
        "exit_code": 0 if outcome == "noop" else 2,
        "summary": json.dumps(summary, separators=(",", ":")),
        "did_work": False,
        "work_kind": "none",
        "quality_outcome": "not-applicable",
        "gate_decision": decision,
        "gate_reason": reason,
    }
    if target_id:
        record["target_id"] = target_id
    if fingerprint:
        record["fingerprint"] = fingerprint
    HistoryStore(history_path).append(record)
    return summary
