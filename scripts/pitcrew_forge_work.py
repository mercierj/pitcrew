"""Provider-neutral normalized forge-work schema."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from urllib.parse import urlsplit


LIFECYCLES = ("todo", "processing", "review", "blocked", "done")
PROVIDERS = {"github", "gitlab"}
CHANGE_KINDS = {"pull_request", "merge_request"}


class ForgeWorkError(ValueError):
    pass


def _https_url(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ForgeWorkError(f"{field} must be an HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ForgeWorkError(f"{field} must be an HTTPS URL")
    return value


def _number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ForgeWorkError("resource number must be a positive integer")
    return value


def normalize_issue(
    *,
    provider: str,
    number: int,
    title: str,
    body: str,
    labels: Sequence[str],
    state: str,
    canonical_url: str,
    lifecycle: str,
    route: str | None,
    source: str | None,
    related_change_urls: Sequence[str],
    bugfix: Mapping[str, object] | None,
) -> dict:
    if provider not in PROVIDERS:
        raise ForgeWorkError("provider is unsupported")
    number = _number(number)
    if lifecycle not in LIFECYCLES:
        raise ForgeWorkError("lifecycle is unsupported")
    if state not in {"open", "closed"}:
        raise ForgeWorkError("issue state is unsupported")
    evidence = dict(bugfix or {})
    return {
        "provider": provider,
        "resource_type": "issue",
        "number": number,
        "reference": f"#{number}",
        "title": str(title),
        "body": str(body),
        "labels": [str(label) for label in labels],
        "state": state,
        "canonical_url": _https_url(canonical_url, "canonical_url"),
        "lifecycle": lifecycle,
        "route": route,
        "source": source,
        "related_change_urls": [
            _https_url(url, "related_change_url") for url in related_change_urls
        ],
        "bugfix": {
            "reproduction": evidence.get("reproduction"),
            "verification": evidence.get("verification"),
            "ready_head_sha": evidence.get("ready_head_sha"),
            "blocked_reason": evidence.get("blocked_reason"),
        },
        "agent_action": None,
    }


def normalize_change(
    *,
    provider: str,
    kind: str,
    number: int,
    title: str,
    canonical_url: str,
    state: str,
    source_branch: str,
    target_branch: str,
    author: str,
    checks_status: str,
    head_sha: str,
) -> dict:
    if provider not in PROVIDERS or kind not in CHANGE_KINDS:
        raise ForgeWorkError("change provider or kind is unsupported")
    number = _number(number)
    return {
        "provider": provider,
        "resource_type": "change",
        "kind": kind,
        "number": number,
        "reference": ("!" if kind == "merge_request" else "#") + str(number),
        "title": str(title),
        "canonical_url": _https_url(canonical_url, "canonical_url"),
        "state": str(state),
        "source_branch": str(source_branch),
        "target_branch": str(target_branch),
        "author": str(author),
        "checks_status": str(checks_status),
        "head_sha": str(head_sha),
    }
