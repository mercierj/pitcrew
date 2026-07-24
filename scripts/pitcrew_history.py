#!/usr/bin/env python3
"""Locked, bounded JSONL storage for Pitcrew run history."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator


REQUIRED_STRING_FIELDS = (
    "project",
    "skill",
    "started_at",
    "finished_at",
    "outcome",
    "summary",
)
VALID_OUTCOMES = {"success", "noop", "failed", "interrupted"}
ACTIONABLE_NOOP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brequired\s+(?:file|reference|dependency|configuration|config)"
        r"\s+(?:(?:is|was)\s+)?(?:unavailable|missing|failed)\b",
        r"\bmissing\s+"
        r"(?:configuration|config|file|reference|dependency|credential|"
        r"[\w.-]+\.[a-z0-9_-]+)\b",
        r"\bprovider(?:\s+(?:check|authentication))?\s+"
        r"(?:(?:is|was)\s+)?(?:unavailable|failed)\b",
        r"\bauthentication\s+(?:(?:is|was)\s+)?"
        r"(?:required|unavailable|failed|invalid|expired|revoked)\b",
        r"\bpermissions?\s+(?:(?:is|are|was|were)\s+)?"
        r"(?:denied|required|failed|unavailable)\b",
    )
)


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
    for field in REQUIRED_STRING_FIELDS:
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError(f"history record field {field} must be a non-empty string")
    if record["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"invalid history outcome: {record['outcome']}")
    _parse_timestamp(record["started_at"])
    _parse_timestamp(record["finished_at"])
    return record


class HistoryStore:
    def __init__(self, path: Path, retention_days: int = 7):
        self.path = Path(path)
        self.retention_days = retention_days
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

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

    def append(self, record: dict, now: str | None = None) -> None:
        _validate_record(record)
        current_time = _parse_timestamp(now) if now is not None else datetime.now(UTC)
        with self._locked():
            records = self._read_valid_records()
            records.append(record)
            self._replace(self._retained(records, current_time))

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
    try:
        decoded = json.loads(summary)
    except (json.JSONDecodeError, TypeError):
        decoded = summary

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
    if any(pattern.search(reason_text) for pattern in ACTIONABLE_NOOP_PATTERNS):
        return "warning"
    return "healthy"
