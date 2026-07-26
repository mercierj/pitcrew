#!/usr/bin/env python3
"""Validated, local-only proposal ledger used by discovery roles and dashboard."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from argparse import ArgumentParser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ProposalError(ValueError):
    pass


STATUSES = {"suggested", "approved", "dismissed", "investigate"}
CATEGORIES = {"security", "feature", "architecture"}
SEVERITIES = {"critical", "high", "medium", "low"}
REQUIRED = {"id", "category", "severity", "title", "summary", "evidence", "recommendation", "status", "source"}
ARCHITECTURE_CATEGORIES = {
    "responsabilités mélangées", "couplage framework/persistence",
    "direction/cycles dépendances", "frontières dupliquées", "interfaces fuyantes",
    "abstraction manquante prouvée",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_tracker(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {"iid", "web_url"}:
        raise ProposalError("tracker is invalid")
    iid = value["iid"]
    web_url = value["web_url"]
    if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0 or not isinstance(web_url, str):
        raise ProposalError("tracker is invalid")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in web_url):
        raise ProposalError("tracker is invalid")
    try:
        parsed = urlparse(web_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ProposalError("tracker is invalid") from error
    if parsed.scheme != "https" or not parsed.netloc or not hostname or parsed.username is not None or parsed.password is not None:
        raise ProposalError("tracker is invalid")
    if port is not None and not 1 <= port <= 65535:
        raise ProposalError("tracker is invalid")
    return {"iid": iid, "web_url": web_url}


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
    evidence = value["evidence"]
    if value["category"] == "architecture":
        if value.get("architecture_category") not in ARCHITECTURE_CATEGORIES:
            raise ProposalError("proposal architecture_category is invalid")
        where = value.get("where")
        if where is not None and (
            not isinstance(where, list)
            or not where
            or not all(isinstance(item, str) and item.strip() for item in where)
        ):
            raise ProposalError("proposal where is invalid")
        if isinstance(evidence, dict):
            files = evidence.get("files")
            if not isinstance(files, list) or not files or not all(isinstance(item, str) and item.strip() for item in files):
                raise ProposalError("proposal evidence is invalid")
        elif not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item.strip() for item in evidence):
            raise ProposalError("proposal evidence is invalid")
    elif not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
        raise ProposalError("proposal evidence is invalid")
    if "tracker" in value:
        _validate_tracker(value["tracker"])
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

    def attach_tracker(self, proposal_id: str, tracker: dict) -> dict:
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ProposalError("proposal id is invalid")
        normalized = _validate_tracker(tracker)
        with self._locked() as lock:
            try:
                records = self._read()
                for proposal in records:
                    if proposal["id"] != proposal_id:
                        continue
                    if proposal["category"] != "architecture":
                        raise ProposalError("tracker attachment requires architecture proposal")
                    if proposal["status"] not in {"approved", "investigate"}:
                        raise ProposalError("proposal is not approved or investigate")
                    existing = proposal.get("tracker")
                    if existing is not None:
                        if existing == normalized:
                            return proposal
                        raise ProposalError("proposal has a different tracker")
                    proposal["tracker"] = normalized
                    self._write(records)
                    return proposal
                raise ProposalError("proposal not found")
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Manage the local PitCrew proposal ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    attach = commands.add_parser("attach-tracker")
    attach.add_argument("--ledger", required=True)
    attach.add_argument("--proposal-id", required=True)
    attach.add_argument("--iid", required=True, type=int)
    attach.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "attach-tracker":
            proposal = ProposalStore(Path(args.ledger)).attach_tracker(
                args.proposal_id, {"iid": args.iid, "web_url": args.url}
            )
            print(json.dumps({"id": proposal["id"], "tracker": proposal["tracker"]}, separators=(",", ":")))
            return 0
    except ProposalError as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
