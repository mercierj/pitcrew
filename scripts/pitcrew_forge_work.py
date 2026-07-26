"""Provider-neutral normalized forge-work schema."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import re
import subprocess
from urllib.parse import quote, urlsplit


LIFECYCLES = ("todo", "processing", "review", "blocked", "done")
PROVIDERS = {"github", "gitlab"}
CHANGE_KINDS = {"pull_request", "merge_request"}


class ForgeWorkError(ValueError):
    pass


def _label_value(labels: object, prefix: str) -> str | None:
    if not isinstance(labels, list):
        return None
    values = [
        label.removeprefix(prefix)
        for label in labels
        if isinstance(label, str) and label.startswith(prefix)
    ]
    return values[0] if len(values) == 1 and values[0] else None


class GitLabForgeWork:
    """Read and normalize GitLab issues and merge requests without mutations."""

    def __init__(
        self,
        config: Mapping[str, object],
        command_runner: Callable,
    ):
        self.config = config
        self.command_runner = command_runner
        gitlab = config.get("gitlab")
        if not isinstance(gitlab, Mapping):
            raise ForgeWorkError("gitlab configuration is required")
        self.host = _string_field(gitlab.get("host"), "gitlab.host")
        self.project_id = _number(gitlab.get("project_id"))
        self.project_path = _string_field(
            gitlab.get("project_path"), "gitlab.project_path"
        )

    def _empty(self, error: str) -> dict:
        return {
            "provider": "gitlab",
            "degraded": True,
            "error": error,
            "groups": {state: [] for state in LIFECYCLES},
            "changes": [],
        }

    def _page(self, path: str) -> list[Mapping[str, object]]:
        args = ["glab", "api", "--hostname", self.host, path]
        try:
            result = self.command_runner(
                args, text=True, capture_output=True, check=False
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ForgeWorkError("GitLab command is unavailable") from error
        if result.returncode:
            raise ForgeWorkError("GitLab request failed")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise ForgeWorkError("GitLab returned invalid JSON") from error
        if not isinstance(payload, list) or not all(
            isinstance(item, Mapping) for item in payload
        ):
            raise ForgeWorkError("GitLab returned invalid JSON")
        return payload

    def _collection(self, resource: str) -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        for page in range(1, 101):
            batch = self._page(
                f"projects/{self.project_id}/{resource}?scope=all&per_page=100&page={page}"
            )
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise ForgeWorkError("GitLab pagination limit exceeded")

    def _related_urls(
        self, issue: Mapping[str, object], changes: Sequence[dict]
    ) -> list[str]:
        text = " ".join(
            str(issue.get(key, "")) for key in ("description", "web_url", "references")
        )
        issue_number = issue.get("iid")
        allowed = {change["canonical_url"] for change in changes}
        related = {
            url
            for url in re.findall(r"https://[^\s<>'\"]+/-/merge_requests/\d+", text)
            if url in allowed
        }
        for change in changes:
            if f"!{change['number']}" in text:
                related.add(change["canonical_url"])
            if isinstance(issue_number, int) and f"#{issue_number}" in str(change.get("_text", "")):
                related.add(change["canonical_url"])
        return sorted(related)

    def _change(self, raw: Mapping[str, object]) -> dict:
        author = raw.get("author")
        pipeline = raw.get("head_pipeline")
        pipeline_status = pipeline.get("status") if isinstance(pipeline, Mapping) else ""
        checks_status = {
            "success": "passing",
            "failed": "failing",
            "running": "pending",
            "pending": "pending",
        }.get(pipeline_status, "unknown")
        change = normalize_change(
            provider="gitlab",
            kind="merge_request",
            number=_number(raw.get("iid")),
            title=str(raw.get("title", "")),
            canonical_url=_https_url(raw.get("web_url"), "canonical_url"),
            state="open" if raw.get("state") == "opened" else str(raw.get("state", "")),
            source_branch=str(raw.get("source_branch", "")),
            target_branch=str(raw.get("target_branch", "")),
            author=str(author.get("username", "")) if isinstance(author, Mapping) else "",
            checks_status=str(checks_status),
            head_sha=str(raw.get("sha", raw.get("diff_refs", ""))),
        )
        change["_text"] = " ".join(
            str(raw.get(key, "")) for key in ("description", "web_url", "references")
        )
        return change

    def collect(self) -> dict:
        try:
            raw_changes = self._collection("merge_requests")
            changes = [self._change(change) for change in raw_changes]
            raw_issues = self._collection("issues")
            groups = {state: [] for state in LIFECYCLES}
            for raw in raw_issues:
                labels = raw.get("labels")
                lifecycle = _label_value(labels, "pitcrew-state::")
                if lifecycle not in groups:
                    continue
                issue = normalize_issue(
                    provider="gitlab",
                    number=_number(raw.get("iid")),
                    title=str(raw.get("title", "")),
                    body=str(raw.get("description", "")),
                    labels=labels if isinstance(labels, list) else [],
                    state="open" if raw.get("state") == "opened" else "closed",
                    canonical_url=_https_url(raw.get("web_url"), "canonical_url"),
                    lifecycle=lifecycle,
                    route=_label_value(labels, "pitcrew-route::"),
                    source=_label_value(labels, "pitcrew-source::"),
                    related_change_urls=self._related_urls(raw, changes),
                    bugfix=None,
                )
                groups[lifecycle].append(issue)
            for change in changes:
                change.pop("_text", None)
            return {
                "provider": "gitlab",
                "degraded": False,
                "error": None,
                "groups": groups,
                "changes": changes,
            }
        except ForgeWorkError as error:
            return self._empty(str(error))


class GitHubForgeWork:
    """Read and normalize GitHub issues and pull requests without mutations."""

    def __init__(self, config: Mapping[str, object], command_runner: Callable):
        self.config = config
        self.command_runner = command_runner
        github = config.get("github")
        if not isinstance(github, Mapping):
            raise ForgeWorkError("github configuration is required")
        self.host = _string_field(github.get("host"), "github.host")
        self.user = _string_field(github.get("user"), "github.user")
        self.owner = _string_field(github.get("owner"), "github.owner")
        self.repository = _string_field(github.get("repository"), "github.repository")
        tracker = github.get("tracker")
        labels = tracker.get("labels") if isinstance(tracker, Mapping) else None
        self.agent_label = _string_field(
            labels.get("agent") if isinstance(labels, Mapping) else None,
            "github.tracker.labels.agent",
        )

    def _empty(self, error: str) -> dict:
        return {
            "provider": "github",
            "degraded": True,
            "error": error,
            "groups": {state: [] for state in LIFECYCLES},
            "changes": [],
        }

    def _list(self, endpoint: str) -> list[Mapping[str, object]]:
        args = ["gh", "api", "--hostname", self.host, "--paginate", endpoint]
        try:
            result = self.command_runner(args, text=True, capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise ForgeWorkError("GitHub command is unavailable") from error
        if result.returncode:
            raise ForgeWorkError("GitHub request failed")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise ForgeWorkError("GitHub returned invalid JSON") from error
        if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
            raise ForgeWorkError("GitHub returned invalid JSON")
        return payload

    def _checks(self, head_sha: str) -> str:
        endpoint = f"repos/{self.repository}/commits/{head_sha}/check-runs"
        args = ["gh", "api", "--hostname", self.host, endpoint]
        try:
            result = self.command_runner(args, text=True, capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise ForgeWorkError("GitHub command is unavailable") from error
        if result.returncode:
            raise ForgeWorkError("GitHub checks request failed")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise ForgeWorkError("GitHub returned invalid JSON") from error
        runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
        if not isinstance(runs, list) or not all(isinstance(run, Mapping) for run in runs):
            raise ForgeWorkError("GitHub returned invalid JSON")
        if not runs:
            return "absent"
        statuses = [run.get("status") for run in runs]
        conclusions = [run.get("conclusion") for run in runs]
        if any(status != "completed" for status in statuses):
            return "pending"
        if any(conclusion not in {"success", "neutral", "skipped"} for conclusion in conclusions):
            return "failing"
        return "passing"

    @staticmethod
    def _labels(raw: object) -> list[str]:
        if not isinstance(raw, list):
            raise ForgeWorkError("GitHub issue labels are invalid")
        labels = []
        for label in raw:
            if not isinstance(label, Mapping) or not isinstance(label.get("name"), str):
                raise ForgeWorkError("GitHub issue labels are invalid")
            labels.append(label["name"])
        return labels

    def _change(self, raw: Mapping[str, object]) -> dict:
        head = raw.get("head")
        base = raw.get("base")
        user = raw.get("user")
        if not isinstance(head, Mapping) or not isinstance(base, Mapping):
            raise ForgeWorkError("GitHub pull request is invalid")
        head_sha = _string_field(head.get("sha"), "pull_request.head.sha")
        return normalize_change(
            provider="github",
            kind="pull_request",
            number=_number(raw.get("number")),
            title=str(raw.get("title", "")),
            canonical_url=_https_url(raw.get("html_url"), "canonical_url"),
            state=str(raw.get("state", "")),
            source_branch=str(head.get("ref", "")),
            target_branch=str(base.get("ref", "")),
            author=str(user.get("login", "")) if isinstance(user, Mapping) else "",
            checks_status=self._checks(head_sha),
            head_sha=head_sha,
        )

    def _related_urls(self, issue: Mapping[str, object], changes: Sequence[dict]) -> list[str]:
        number = issue.get("number")
        text = " ".join(str(issue.get(key, "")) for key in ("body", "html_url"))
        related = set()
        for change in changes:
            if f"#{change['number']}" in text or (isinstance(number, int) and f"#{number}" in str(change)):
                related.add(change["canonical_url"])
        return sorted(related)

    def collect(self) -> dict:
        try:
            issues_endpoint = (
                f"repos/{self.repository}/issues?state=all&labels="
                f"{quote(self.agent_label, safe='')}&per_page=100"
            )
            raw_issues = self._list(issues_endpoint)
            raw_changes = self._list(f"repos/{self.repository}/pulls?state=all&per_page=100")
            changes = [self._change(change) for change in raw_changes]
            groups = {state: [] for state in LIFECYCLES}
            for raw in raw_issues:
                if "pull_request" in raw:
                    continue
                labels = self._labels(raw.get("labels"))
                lifecycle = _label_value(labels, "pitcrew-state::")
                if lifecycle not in groups:
                    continue
                groups[lifecycle].append(normalize_issue(
                    provider="github",
                    number=_number(raw.get("number")),
                    title=str(raw.get("title", "")),
                    body=str(raw.get("body", "")),
                    labels=labels,
                    state=str(raw.get("state", "")),
                    canonical_url=_https_url(raw.get("html_url"), "canonical_url"),
                    lifecycle=lifecycle,
                    route=_label_value(labels, "pitcrew-route::"),
                    source=_label_value(labels, "pitcrew-source::"),
                    related_change_urls=self._related_urls(raw, changes),
                    bugfix=None,
                ))
            return {"provider": "github", "degraded": False, "error": None, "groups": groups, "changes": changes}
        except ForgeWorkError as error:
            return self._empty(str(error))


def canonical_issue_target(config: Mapping[str, object], target: object) -> str:
    value = _https_url(target, "target")
    parsed = urlsplit(value)
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        raise ForgeWorkError("tracker does not expose native issues")
    provider = providers.get("tracker")
    if parsed.query or parsed.fragment:
        raise ForgeWorkError("ticket target must not contain query or fragment")
    if provider == "github":
        binding = config.get("github")
        path_key = "repository"
        prefix_suffixes = ("/issues/",)
    elif provider == "gitlab":
        binding = config.get("gitlab")
        path_key = "project_path"
        # GitLab work-item URLs are accepted for compatibility, but stored
        # under the canonical issue URL so coordinator keys remain stable.
        prefix_suffixes = ("/-/issues/", "/-/work_items/")
    else:
        raise ForgeWorkError("tracker does not expose native issues")
    if not isinstance(binding, Mapping):
        raise ForgeWorkError("ticket target does not match configured binding")
    host = _string_field(binding.get("host"), "provider.host")
    project = _string_field(binding.get(path_key), "provider.project")
    expected = next(
        (f"/{project}{suffix}" for suffix in prefix_suffixes if parsed.path.startswith(f"/{project}{suffix}")),
        None,
    )
    if parsed.netloc != host or expected is None:
        raise ForgeWorkError("ticket target does not match configured binding")
    number = parsed.path.removeprefix(expected)
    if not number.isdigit() or int(number) <= 0:
        raise ForgeWorkError("ticket target number is invalid")
    if provider == "gitlab":
        return f"https://{host}/{project}/-/issues/{number}"
    return value


def ticket_agent_action(
    *,
    issue: Mapping[str, object],
    labels: Mapping[str, str],
    enabled_skills: set[str],
    active_run: Mapping[str, object] | None,
    globally_stopped: bool,
) -> dict | None:
    lifecycle = issue.get("lifecycle")
    issue_labels = issue.get("labels")
    target = issue.get("canonical_url")
    if not isinstance(lifecycle, str) or not isinstance(issue_labels, list) or not isinstance(target, str):
        return None
    if not all(isinstance(value, str) for value in issue_labels):
        return None
    required = ("agent", "bug", "investigate")
    if not all(isinstance(labels.get(key), str) and labels[key] for key in required):
        return None
    label_set = set(issue_labels)
    if lifecycle == "todo":
        if labels["agent"] not in label_set or labels["investigate"] in label_set:
            return None
        skill, label = (
            ("bugfixer-run", "Corriger ce bug")
            if labels["bug"] in label_set
            else ("implementer-run", "Lancer l’implémentation")
        )
    elif lifecycle == "blocked":
        skill, label = "unblock", "Débloquer ce ticket"
    elif lifecycle == "done":
        skill, label = "stale-sweep", "Vérifier la clôture"
    else:
        return None
    active_state = active_run.get("state") if isinstance(active_run, Mapping) else None
    available = (
        skill in enabled_skills
        and not globally_stopped
        and active_state not in {"queued", "running"}
    )
    return {
        "skill": skill,
        "label": label,
        "target": target,
        "available": available,
        "run_state": active_state,
        "unavailable_reason": (
            None if available
            else "Exécution déjà active" if active_state in {"queued", "running"}
            else "Agent indisponible"
        ),
    }


def _string_field(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ForgeWorkError(f"{field} must be a non-empty string")
    return value


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
