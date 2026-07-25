#!/usr/bin/env python3
"""Validated, local-only proposal ledger used by discovery roles and dashboard."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ProposalError(ValueError):
    pass


STATUSES = {"suggested", "approved", "dismissed", "investigate"}
CATEGORIES = {"security", "feature"}
SEVERITIES = {"critical", "high", "medium", "low"}
REQUIRED = {"id", "category", "severity", "title", "summary", "evidence", "recommendation", "status", "source"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate(value: Any) -> dict:
    if not isinstance(value, dict) or not REQUIRED.issubset(value):
        raise ProposalError("proposal has invalid fields")
    if not isinstance(value["id"], str) or not value["id"] or len(value["id"]) > 160:
        raise ProposalError("proposal id is invalid")
    if value["category"] not in CATEGORIES:
        raise ProposalError("proposal category is invalid")
    if value["severity"] not in SEVERITIES:
        raise ProposalError("proposal severity is invalid")
    if value["status"] not in STATUSES:
        raise ProposalError("proposal status is invalid")
    for key in ("title", "summary", "recommendation", "source"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 12000:
            raise ProposalError(f"proposal {key} is invalid")
    if not isinstance(value["evidence"], list) or not all(isinstance(item, str) and item for item in value["evidence"]):
        raise ProposalError("proposal evidence is invalid")
    return dict(value)


class ProposalStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProposalError("proposal ledger is invalid") from error
        if not isinstance(value, list):
            raise ProposalError("proposal ledger is invalid")
        return [_validate(item) for item in value]

    def _write(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".proposals-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def list(self) -> list[dict]:
        return self._read()

    def append(self, value: dict) -> dict:
        proposal = _validate(value)
        with self._locked() as lock:
            try:
                records = self._read()
                if any(item["id"] == proposal["id"] for item in records):
                    raise ProposalError("duplicate proposal id")
                proposal.setdefault("created_at", _now())
                records.append(proposal)
                self._write(records)
                return proposal
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def transition(self, proposal_id: str, status: str, *, actor: str, reason: str = "", metadata: dict | None = None) -> dict:
        if status not in {"approved", "dismissed", "investigate"}:
            raise ProposalError("invalid proposal transition")
        if status == "dismissed" and not reason.strip():
            raise ProposalError("reason is required")
        with self._locked() as lock:
            try:
                records = self._read()
                for proposal in records:
                    if proposal["id"] != proposal_id:
                        continue
                    if proposal["status"] != "suggested":
                        raise ProposalError("proposal is no longer pending")
                    proposal["status"] = status
                    proposal["decision"] = {"actor": actor, "at": _now()}
                    if reason:
                        proposal["decision"]["reason"] = reason[:4000]
                    if metadata:
                        proposal.update(metadata)
                    self._write(records)
                    return proposal
                raise ProposalError("proposal not found")
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
