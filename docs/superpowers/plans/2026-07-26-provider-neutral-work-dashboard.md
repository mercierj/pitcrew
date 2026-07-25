# Provider-Neutral Work Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GitLab-only dashboard work collector with one normalized GitHub/GitLab work feed that routes eligible bugs to `bugfixer-run` and preserves local dashboard operation during provider failure.

**Architecture:** A focused `pitcrew_forge_work.py` module owns provider API calls, target validation, normalization, caching inputs, and ticket action selection. `DashboardService` delegates to it and serves one `/api/forge-work` endpoint while retaining `/api/gitlab` as a temporary alias. The browser renders provider-neutral issue and change fields without performing provider-specific mutations during reads.

**Tech Stack:** Python 3.13 standard library, `glab`, `gh`, Python `unittest`, vanilla JavaScript/HTML/CSS.

**Dependencies:** Complete the ticket-run coordinator, provider runtime, and bugfixer lifecycle plans first. The dashboard must use durable per-ticket run state, not the legacy role-wide `running` boolean, to decide CTA availability.

**Execution context:** Work in the current checkout and preserve ongoing dashboard changes. Do not overwrite or revert the current merged-lifecycle, reasoning-effort, coordinator, or day-to-day UI work; adapt the provider split around the resulting code.

---

## File map

- Create `scripts/pitcrew_forge_work.py`: normalized schema, GitLab/GitHub adapters, canonical target validation, related-change extraction, and agent-action routing.
- Create `tests/test_forge_work.py`: provider parity, pagination, malformed response, degradation, target, and routing tests.
- Modify `scripts/pitcrew_dashboard.py`: inject the adapter, cache normalized work, expose provider-neutral service methods, and remove provider reads from the service class.
- Modify `bin/pitcrew-dashboard`: add `/api/forge-work` and keep `/api/gitlab` as a GitLab-only compatibility alias.
- Modify `tests/test_dashboard.py`: service delegation, HTTP, action, cache, security, and compatibility coverage.
- Modify `dashboard/index.html`: rename the work panel and DOM IDs to provider-neutral terms.
- Modify `dashboard/app.js`: render normalized issues/changes and poll `/api/forge-work`.
- Modify `dashboard/styles.css`: rename MR-only selectors without changing the established visual system.
- Modify `README.md` and `tests/test_docs.py`: document provider-neutral behavior and degraded operation.

## Task 1: Lock a normalized forge-work schema

**Files:**
- Create: `scripts/pitcrew_forge_work.py`
- Create: `tests/test_forge_work.py`

- [ ] **Step 1: Write failing normalization tests**

Create `tests/test_forge_work.py`:

```python
import unittest

from scripts.pitcrew_forge_work import (
    ForgeWorkError,
    normalize_change,
    normalize_issue,
)


class ForgeWorkSchemaTest(unittest.TestCase):
    def test_normalized_issue_has_no_provider_specific_identity_fields(self):
        issue = normalize_issue(
            provider="gitlab",
            number=12,
            title="Payment total is stale",
            body="Observed after retry",
            labels=[
                "pitcrew-agent",
                "pitcrew-type::bug",
                "pitcrew-state::todo",
            ],
            state="open",
            canonical_url=(
                "https://gitlab.com/getbill1/getbill/-/issues/12"
            ),
            lifecycle="todo",
            route=None,
            source=None,
            related_change_urls=[],
            bugfix=None,
        )
        self.assertEqual(
            {
                "provider": "gitlab",
                "resource_type": "issue",
                "number": 12,
                "reference": "#12",
                "title": "Payment total is stale",
                "body": "Observed after retry",
                "labels": [
                    "pitcrew-agent",
                    "pitcrew-type::bug",
                    "pitcrew-state::todo",
                ],
                "state": "open",
                "canonical_url": (
                    "https://gitlab.com/getbill1/getbill/-/issues/12"
                ),
                "lifecycle": "todo",
                "route": None,
                "source": None,
                "related_change_urls": [],
                "bugfix": {
                    "reproduction": None,
                    "verification": None,
                    "ready_head_sha": None,
                    "blocked_reason": None,
                },
                "agent_action": None,
            },
            issue,
        )
        self.assertNotIn("iid", issue)
        self.assertNotIn("web_url", issue)

    def test_normalized_change_uses_pull_or_merge_request_kind(self):
        change = normalize_change(
            provider="github",
            kind="pull_request",
            number=7,
            title="Fix stale payment total",
            canonical_url="https://github.com/acme/payments/pull/7",
            state="open",
            source_branch="fix/issue-12",
            target_branch="main",
            author="octocat",
            checks_status="passing",
            head_sha="a" * 40,
        )
        self.assertEqual("pull_request", change["kind"])
        self.assertEqual("#7", change["reference"])
        self.assertEqual("passing", change["checks_status"])

    def test_normalizers_reject_unsafe_urls_and_invalid_numbers(self):
        with self.assertRaises(ForgeWorkError):
            normalize_issue(
                provider="github",
                number=0,
                title="x",
                body="",
                labels=[],
                state="open",
                canonical_url="javascript:alert(1)",
                lifecycle="todo",
                route=None,
                source=None,
                related_change_urls=[],
                bugfix=None,
            )
```

- [ ] **Step 2: Run and confirm import failure**

Run:

```bash
python3 -m unittest tests.test_forge_work.ForgeWorkSchemaTest -v
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement immutable normalized constructors**

Create `scripts/pitcrew_forge_work.py` with:

```python
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
            _https_url(url, "related_change_url")
            for url in related_change_urls
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
```

- [ ] **Step 4: Run schema tests**

```bash
python3 -m unittest tests.test_forge_work.ForgeWorkSchemaTest -v
```

Expected: PASS.

- [ ] **Step 5: Commit normalized schema**

```bash
git add scripts/pitcrew_forge_work.py tests/test_forge_work.py
git commit -m "feat: define normalized forge work schema"
```

## Task 2: Move GitLab collection behind an adapter

**Files:**
- Modify: `scripts/pitcrew_forge_work.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `tests/test_forge_work.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write a failing GitLab adapter parity test**

Build a fake command runner using the existing `gitlab_issues()` and
`gitlab_merge_requests()` fixtures. Assert:

```python
work = GitLabForgeWork(config, runner).collect()
self.assertEqual("gitlab", work["provider"])
self.assertEqual(
    {"todo", "processing", "review", "blocked", "done"},
    set(work["groups"]),
)
self.assertEqual(1, work["groups"]["todo"][0]["number"])
self.assertEqual(
    "merge_request",
    work["changes"][0]["kind"],
)
self.assertNotIn("iid", work["groups"]["todo"][0])
self.assertNotIn("web_url", work["changes"][0])
```

Also assert all list operations include the configured `--hostname` and
configured numeric project ID; reject malformed list payloads.

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_forge_work.GitLabForgeWorkTest -v
```

Expected: FAIL because `GitLabForgeWork` does not exist.

- [ ] **Step 3: Implement the GitLab adapter**

Add `GitLabForgeWork` with constructor:

```python
class GitLabForgeWork:
    def __init__(
        self,
        config: Mapping[str, object],
        command_runner: Callable,
    ):
        self.config = config
        self.command_runner = command_runner
```

Move these read-only responsibilities from `DashboardService`:

- paginated `glab api --hostname <host>` issue and MR lookup;
- lifecycle, route, and source scoped-label extraction;
- related MR URL parsing restricted to the configured host/project;
- pipeline status and head SHA normalization;
- parsing exact `pitcrew:bugfix:red:v1`, `green:v1`, `ready:v1`, and
  `blocked:v1` issue-comment markers for bug tickets in processing, review, or
  blocked states;
- degraded payload construction.

`collect()` returns:

```python
{
    "provider": "gitlab",
    "degraded": False,
    "error": None,
    "groups": {state: [...] for state in LIFECYCLES},
    "changes": [...],
}
```

Do not mutate issues while collecting. Merged-lifecycle reconciliation remains
owned by `stale-sweep`; a dashboard read cannot change remote state.

- [ ] **Step 4: Delegate from `DashboardService`**

Inject a `forge_work_factory` defaulting to provider selection. Replace direct
GitLab collection with:

```python
def forge_work(self, force_refresh: bool = False) -> dict:
    if self._forge_cache_is_fresh(force_refresh):
        return self._forge_cache
    adapter = self.forge_work_factory(self.config, self.command_runner)
    collected = adapter.collect()
    return self._cache_forge_work(collected)
```

Keep `gitlab_work()` temporarily:

```python
def gitlab_work(self, force_refresh: bool = False) -> dict:
    if self.config["providers"]["forge"] != "gitlab":
        raise DashboardError("GitLab compatibility endpoint is unavailable")
    return self.forge_work(force_refresh=force_refresh)
```

- [ ] **Step 5: Run focused dashboard tests**

```bash
python3 -m unittest tests.test_forge_work tests.test_dashboard -v
```

Expected: PASS with updated normalized assertions.

- [ ] **Step 6: Commit GitLab adapter extraction**

```bash
git add scripts/pitcrew_forge_work.py scripts/pitcrew_dashboard.py \
  tests/test_forge_work.py tests/test_dashboard.py
git commit -m "refactor: isolate GitLab dashboard adapter"
```

## Task 3: Add the GitHub work adapter and target validator

**Files:**
- Modify: `scripts/pitcrew_forge_work.py`
- Modify: `tests/test_forge_work.py`

- [ ] **Step 1: Write failing GitHub parity tests**

Use fixture responses for:

```text
gh api --hostname github.com repos/acme/payments/issues?state=all&labels=pitcrew-agent&per_page=100
gh api --hostname github.com repos/acme/payments/pulls?state=all&per_page=100
gh api --hostname github.com repos/acme/payments/commits/<head-sha>/check-runs
```

Include one issue-shaped response, one issue endpoint entry with `pull_request`
that must be discarded, one open PR, and all five lifecycle labels. Assert the
normalized payload has the same keys as the GitLab payload.

Add:

```python
def test_canonical_issue_target_validates_provider_binding(self):
    self.assertEqual(
        "https://github.com/acme/payments/issues/12",
        canonical_issue_target(
            self.config,
            "https://github.com/acme/payments/issues/12",
        ),
    )
    for value in (
        "http://github.com/acme/payments/issues/12",
        "https://github.com/other/payments/issues/12",
        "https://github.com/acme/payments/pull/12",
        "https://github.com/acme/payments/issues/0",
        "https://github.com/acme/payments/issues/12?x=1",
    ):
        with self.assertRaises(ForgeWorkError):
            canonical_issue_target(self.config, value)
```

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_forge_work.GitHubForgeWorkTest -v
```

Expected: FAIL because the GitHub adapter is absent.

- [ ] **Step 3: Implement GitHub collection**

Add `GitHubForgeWork` with the same `collect()` result schema. Validate
`github.host`, `github.user`, `github.owner`, and `github.repository` from the
already validated config. Use the command runner with argument arrays only:

```python
[
    "gh", "api", "--hostname", host, "--paginate",
    (
        f"repos/{repository}/issues"
        f"?state=all&labels={quote(agent_label, safe='')}&per_page=100"
    ),
]
```

and:

```python
[
    "gh", "api", "--hostname", host, "--paginate",
    f"repos/{repository}/pulls?state=all&per_page=100",
]
```

Filter issue endpoint objects containing `pull_request`. Convert GitHub label
objects through their `name`. Derive `checks_status` as `passing`, `failing`,
`pending`, or `absent` by querying
`repos/{repository}/commits/{head_sha}/check-runs` for each open pull request.
Every GitHub command includes the configured hostname.

For bug tickets in processing, review, or blocked states, fetch issue comments
and parse only the exact bugfix markers. Keep the most recent marker of each
type, cap captured text at 2 KiB, and redact secrets before placing it under
`issue.bugfix`. GitLab uses the same parser so provider payloads remain
identical.

- [ ] **Step 4: Implement canonical target validation**

Add:

```python
def canonical_issue_target(
    config: Mapping[str, object],
    target: object,
) -> str:
    value = _https_url(target, "target")
    parsed = urlsplit(value)
    providers = config["providers"]
    provider = providers["tracker"]
    if parsed.query or parsed.fragment:
        raise ForgeWorkError("ticket target must not contain query or fragment")
    if provider == "github":
        github = config["github"]
        expected = f"/{github['repository']}/issues/"
        host = github["host"]
    elif provider == "gitlab":
        gitlab = config["gitlab"]
        expected = f"/{gitlab['project_path']}/-/issues/"
        host = gitlab["host"]
    else:
        raise ForgeWorkError("tracker does not expose native issues")
    if parsed.netloc != host or not parsed.path.startswith(expected):
        raise ForgeWorkError("ticket target does not match configured binding")
    number = parsed.path.removeprefix(expected)
    if not number.isdigit() or int(number) <= 0:
        raise ForgeWorkError("ticket target number is invalid")
    return value
```

Preserve the existing GitLab work-item compatibility in a separate explicit
branch; do not let the GitHub parser accept pull URLs.

- [ ] **Step 5: Run and commit GitHub parity**

```bash
python3 -m unittest tests.test_forge_work -v
git add scripts/pitcrew_forge_work.py tests/test_forge_work.py
git commit -m "feat: collect GitHub work for dashboard"
```

Expected: tests PASS.

## Task 4: Route ticket CTAs by normalized labels and durable runs

**Files:**
- Modify: `scripts/pitcrew_forge_work.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `tests/test_forge_work.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing action-routing tests**

Cover:

```python
cases = (
    (["pitcrew-agent", "bug"], "todo", "bugfixer-run", "Corriger ce bug"),
    (["pitcrew-agent", "enhancement"], "todo", "implementer-run", "Lancer l’implémentation"),
    (["pitcrew-agent"], "blocked", "unblock", "Débloquer ce ticket"),
    (["pitcrew-agent"], "done", "stale-sweep", "Vérifier la clôture"),
)
```

Also assert that todo issues with `bug` but without the configured `agent`
label have no CTA, and that todo issues carrying `agent + bug + investigate`
have no CTA. This keeps dashboard eligibility identical to the bugfixer
selection contract instead of treating every bug-labelled issue as runnable.

For a target already `queued` or `running` in the run-store snapshot, assert
`available=False` and a public state of `queued` or `running`. Assert
`processing` and `review` have no launch CTA.

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest \
  tests.test_forge_work.ForgeWorkActionTest \
  tests.test_dashboard.DashboardServiceTest.test_launch_ticket_agent_targets_validated_issue -v
```

Expected: FAIL because action routing is lifecycle-only and GitLab-specific.

- [ ] **Step 3: Implement provider-neutral action routing**

Add:

```python
def ticket_agent_action(
    *,
    issue: Mapping[str, object],
    labels: Mapping[str, str],
    enabled_skills: set[str],
    active_run: Mapping[str, object] | None,
    globally_stopped: bool,
) -> dict | None:
    lifecycle = issue["lifecycle"]
    issue_labels = set(issue["labels"])
    if lifecycle == "todo":
        if (
            labels["agent"] not in issue_labels
            or labels["investigate"] in issue_labels
        ):
            return None
        if labels["bug"] in issue_labels:
            skill, label = "bugfixer-run", "Corriger ce bug"
        else:
            skill, label = "implementer-run", "Lancer l’implémentation"
    elif lifecycle == "blocked":
        skill, label = "unblock", "Débloquer ce ticket"
    elif lifecycle == "done":
        skill, label = "stale-sweep", "Vérifier la clôture"
    else:
        return None
    active_state = active_run.get("state") if active_run else None
    available = (
        skill in enabled_skills
        and not globally_stopped
        and active_state not in {"queued", "running"}
    )
    return {
        "skill": skill,
        "label": label,
        "target": issue["canonical_url"],
        "available": available,
        "run_state": active_state,
        "unavailable_reason": (
            None if available
            else "Exécution déjà active" if active_state in {"queued", "running"}
            else "Agent indisponible"
        ),
    }
```

Resolve logical labels from the selected provider config, never hard-code
GetBill label names in the adapter.

- [ ] **Step 4: Make dashboard launches use canonical validation and coordinator admission**

Replace `_validate_ticket_target` with `canonical_issue_target`. Allow only the
skill returned by the issue's current normalized `agent_action`; re-fetch work
immediately before admission. Enqueue through the coordinator and return the
durable run object, not a transient child PID:

```python
{
    "accepted": True,
    "run_id": run["run_id"],
    "state": run["state"],
    "skill": skill,
    "target": canonical_target,
}
```

Two simultaneous launch requests for the same target must return the same active
`run_id`.

- [ ] **Step 5: Run and commit action routing**

```bash
python3 -m unittest tests.test_forge_work tests.test_dashboard -v
git add scripts/pitcrew_forge_work.py scripts/pitcrew_dashboard.py \
  tests/test_forge_work.py tests/test_dashboard.py
git commit -m "feat: route bug tickets to bugfixer"
```

Expected: tests PASS.

## Task 5: Serve the provider-neutral endpoint

**Files:**
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing HTTP tests**

Add:

```python
def test_api_forge_work_returns_normalized_work(self):
    status, _, payload = self.request("GET", "/api/forge-work?refresh=1")
    self.assertEqual(200, status)
    self.assertEqual("gitlab", json.loads(payload)["provider"])
    self.assertEqual([("forge_work", True)], self.service.calls)

def test_api_gitlab_is_compatibility_alias_only_for_gitlab(self):
    status, _, _ = self.request("GET", "/api/gitlab?refresh=1")
    self.assertEqual(200, status)
    self.assertEqual([("gitlab_work", True)], self.service.calls)
```

Add a GitHub-configured service case where `/api/gitlab` returns 404 or 409 with
a non-sensitive error while `/api/forge-work` returns GitHub work.

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_dashboard.DashboardHttpTest -v
```

Expected: FAIL because `/api/forge-work` is absent.

- [ ] **Step 3: Add endpoint dispatch**

Add:

```python
if parsed.path == "/api/forge-work":
    query = parse_qs(parsed.query, keep_blank_values=True)
    force_refresh = query.get("refresh", ["0"])[0] in {"1", "true"}
    self._service_json(
        lambda: service.forge_work(force_refresh=force_refresh)
    )
    return
```

Retain `/api/gitlab` as an alias only when the configured provider is GitLab.
Apply the existing security headers and error redaction.

- [ ] **Step 4: Run and commit HTTP API**

```bash
python3 -m unittest tests.test_dashboard -v
git add bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: expose normalized forge work API"
```

Expected: tests PASS.

## Task 6: Render provider-neutral issues and changes

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing asset-contract assertions**

Require:

```python
for marker in (
    'id="forge-work"',
    'id="forge-groups"',
    'id="change-list"',
    'id="changes-title"',
):
    self.assertIn(marker, self.html)

for marker in (
    'fetchJson(forgePath)',
    "renderForgeWork",
    "related_change_urls",
    "issue.bugfix.reproduction",
    "issue.bugfix.verification",
    "issue.bugfix.blocked_reason",
    "change.reference",
    "change.kind",
    "agentAction.run_state",
):
    self.assertIn(marker, self.javascript)

for forbidden in (
    "renderGitLab",
    "gitlabGroups",
    "mergeRequestList",
):
    self.assertNotIn(forbidden, self.javascript)
```

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest -v
```

Expected: FAIL on provider-specific DOM and function names.

- [ ] **Step 3: Rename the work panel**

Use:

```html
<section id="forge-work" aria-labelledby="forge-title">
  <div class="section-heading">
    <div>
      <p class="eyebrow">Forge configurée</p>
      <h2 id="forge-title">Travail GitHub/GitLab</h2>
    </div>
    <p id="forge-state" class="section-note">En attente du premier relevé</p>
  </div>
  <section class="changes-panel" aria-labelledby="changes-title">
    <h3 id="changes-title">Pull requests / merge requests ouvertes</h3>
    <p id="changes-state" class="section-note">Aucun changement chargé</p>
    <div id="change-list" class="change-list" aria-live="polite"></div>
  </section>
  <div id="forge-groups" class="work-grid"></div>
</section>
```

Update the header copy from GitLab-only wording to configured-forge wording.

- [ ] **Step 4: Render normalized fields**

Rename `renderGitLab` to `renderForgeWork` and `renderMergeRequests` to
`renderChanges`. Use:

```javascript
const issueLink = safeExternalLink(
  issue.canonical_url,
  issue.title || `Ticket ${issue.reference || "?"}`,
);
const related = Array.isArray(issue.related_change_urls)
  ? issue.related_change_urls
  : [];
const reproduction = issue.bugfix?.reproduction;
const verification = issue.bugfix?.verification;
const blockedReason = issue.bugfix?.blocked_reason;
const changeLabel = change.kind === "pull_request" ? "PR" : "MR";
const changeLink = safeExternalLink(
  change.canonical_url,
  `${change.title || "Changement sans titre"} · ${change.reference || "?"}`,
);
```

For a bug issue, add a `<details>` block only when at least one structured
evidence field exists. Render reproduction, verification, ready head SHA, and
blocked reason with `textContent`; never assign provider text through
`innerHTML`.

Display `queued` as `En attente` and `running` as `En cours` from
`agentAction.run_state`. Poll:

```javascript
const forgePath = manual
  ? "/api/forge-work?refresh=1"
  : "/api/forge-work";
renderForgeWork(await fetchJson(forgePath));
```

Do not add a provider-neutral manual merge action in this feature. Preserve the
existing GitLab manual merge endpoint and button only behind an explicit
GitLab capability; the bugfixer merge remains human-go plus agent controlled.

- [ ] **Step 5: Rename style selectors**

Mechanically rename:

```text
merge-requests-panel -> changes-panel
merge-request-list -> change-list
merge-request-card -> change-card
merge-request-branches -> change-branches
merge-request-meta -> change-meta
merge-request-actions -> change-actions
```

Do not change spacing, colors, typography, or responsive behavior.

- [ ] **Step 6: Run and commit UI changes**

```bash
python3 -m unittest tests.test_dashboard -v
git add dashboard/index.html dashboard/app.js dashboard/styles.css \
  tests/test_dashboard.py
git commit -m "feat: render provider-neutral forge work"
```

Expected: tests PASS.

## Task 7: Verify degradation, parity, and documentation

**Files:**
- Modify: `README.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add failing documentation markers**

Require:

```python
for marker in (
    "/api/forge-work",
    "GitHub and GitLab",
    "Corriger ce bug",
    "provider panel degrades independently",
    "/api/gitlab compatibility alias",
):
    self.assertIn(marker, readme)
```

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_docs -v
```

Expected: FAIL on the new markers.

- [ ] **Step 3: Document normalized dashboard behavior**

Document that:

- the panel follows the configured native provider;
- GitHub and GitLab failures do not break local agent status/history/decisions;
- eligible `agent + bug + todo` work exposes `Corriger ce bug`;
- queued/running state comes from the durable ticket coordinator;
- `/api/gitlab` is temporary and GitLab-only.

No `tests/run.sh` edit is needed: its existing `unittest discover` command
automatically includes `tests/test_forge_work.py`.

- [ ] **Step 4: Run focused and full verification**

```bash
python3 -m unittest tests.test_forge_work tests.test_dashboard tests.test_docs -v
bash tests/run.sh
```

Expected: all tests PASS and no provider credentials are required by fixtures.

- [ ] **Step 5: Commit dashboard documentation**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: explain provider-neutral work dashboard"
```
