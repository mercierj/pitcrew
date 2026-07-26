"""Provider-neutral, fail-closed bugfix lifecycle decisions."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class LifecycleEvidenceError(ValueError):
    """Raised when normalized lifecycle evidence is incomplete or malformed."""


def _field(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise LifecycleEvidenceError(f"{path} is required")
    return mapping[key]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LifecycleEvidenceError(f"{path} must be an object")
    return value


def _string(value: Any, path: str, *, choices: set[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise LifecycleEvidenceError(f"{path} must be a non-empty string")
    if choices is not None and value not in choices:
        raise LifecycleEvidenceError(f"{path} is invalid")
    return value


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise LifecycleEvidenceError(f"{path} must be a boolean")
    return value


def _optional_sha(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def validate_snapshot(snapshot: Mapping[str, object]) -> dict[str, Any]:
    """Validate normalized provider evidence before any lifecycle decision."""
    root = _mapping(snapshot, "snapshot")
    provider = _string(
        _field(root, "provider", "provider"),
        "provider",
        choices={"github", "gitlab"},
    )
    ticket_url = _string(_field(root, "ticket_url", "ticket_url"), "ticket_url")
    phase = _string(
        _field(root, "phase", "phase"),
        "phase",
        choices={"claimed", "review", "terminal"},
    )
    current_head_sha = _string(
        _field(root, "current_head_sha", "current_head_sha"),
        "current_head_sha",
    )

    sensitive_source = _mapping(
        _field(root, "sensitive", "sensitive"), "sensitive"
    )
    sensitive = {
        key: _boolean(_field(sensitive_source, key, f"sensitive.{key}"), f"sensitive.{key}")
        for key in (
            "matched",
            "approval_label",
            "investigation_marker",
            "approval_marker",
        )
    }

    reproduction_source = _mapping(
        _field(root, "reproduction", "reproduction"), "reproduction"
    )
    reproduction = {
        key: _boolean(
            _field(reproduction_source, key, f"reproduction.{key}"),
            f"reproduction.{key}",
        )
        for key in ("attempted", "valid_red", "green", "same_command")
    }

    review_source = _mapping(_field(root, "review", "review"), "review")
    review = {
        "status": _string(
            _field(review_source, "status", "review.status"),
            "review.status",
            choices={"pending", "approved", "changes_requested"},
        ),
        "reviewer_head_sha": _optional_sha(
            _field(review_source, "reviewer_head_sha", "review.reviewer_head_sha"),
            "review.reviewer_head_sha",
        ),
        "validator_status": _string(
            _field(review_source, "validator_status", "review.validator_status"),
            "review.validator_status",
            choices={"pending", "passed", "failed"},
        ),
        "validator_head_sha": _optional_sha(
            _field(review_source, "validator_head_sha", "review.validator_head_sha"),
            "review.validator_head_sha",
        ),
        "ready_head_sha": _optional_sha(
            _field(review_source, "ready_head_sha", "review.ready_head_sha"),
            "review.ready_head_sha",
        ),
        "human_go_after_ready": _boolean(
            _field(
                review_source,
                "human_go_after_ready",
                "review.human_go_after_ready",
            ),
            "review.human_go_after_ready",
        ),
        "ci_green": _boolean(
            _field(review_source, "ci_green", "review.ci_green"),
            "review.ci_green",
        ),
    }
    fix_attempts = _field(review_source, "fix_attempts", "review.fix_attempts")
    if isinstance(fix_attempts, bool) or not isinstance(fix_attempts, int) or fix_attempts < 0:
        raise LifecycleEvidenceError("review.fix_attempts must be a non-negative integer")
    review["fix_attempts"] = fix_attempts

    return {
        "provider": provider,
        "ticket_url": ticket_url,
        "phase": phase,
        "current_head_sha": current_head_sha,
        "sensitive": sensitive,
        "reproduction": reproduction,
        "review": review,
    }


def decide(snapshot: Mapping[str, object]) -> str:
    evidence = validate_snapshot(snapshot)
    sensitive = evidence["sensitive"]
    reproduction = evidence["reproduction"]
    review = evidence["review"]
    phase = evidence["phase"]
    current_head = evidence["current_head_sha"]

    approval_complete = all(
        sensitive[key] is True
        for key in ("approval_label", "investigation_marker", "approval_marker")
    )
    if sensitive["matched"] is True and not approval_complete:
        return "route_investigate"

    if phase == "claimed":
        if reproduction["attempted"] is True and reproduction["valid_red"] is not True:
            return "block_unreproducible"
        if all(
            reproduction[key] is True
            for key in ("attempted", "valid_red", "green", "same_command")
        ):
            return "open_change"
        return "hold_fix"

    if phase == "review":
        rejected = (
            review["status"] == "changes_requested"
            or review["validator_status"] == "failed"
        )
        if rejected:
            return "address_review" if review["fix_attempts"] < 2 else "block_review"
        current_head_gates = all(
            (
                review["ready_head_sha"] == current_head,
                review["reviewer_head_sha"] == current_head,
                review["validator_head_sha"] == current_head,
                review["status"] == "approved",
                review["validator_status"] == "passed",
                review["human_go_after_ready"] is True,
                review["ci_green"] is True,
            )
        )
        return "merge_close" if current_head_gates else "hold_review"

    return "no_op"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        action = decide(snapshot)
    except (OSError, json.JSONDecodeError, LifecycleEvidenceError) as error:
        print(f"lifecycle evidence error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"action": action}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
