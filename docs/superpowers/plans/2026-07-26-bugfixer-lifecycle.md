# Bugfixer Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an exclusive, evidence-first `bugfixer-run` role that consumes labelled GitHub/GitLab bugs, requires a red reproduction, opens one reviewed change, and merges only after current-head review, validation, human `go`, and green CI.

**Architecture:** A shared provider-neutral delivery reference owns claim, change, review, merge, idempotency, and closeout invariants. `implementer-run` and `bugfixer-run` keep separate selection policies. Sensitive bugs route through `investigate-run` and an explicit `unblock` approval marker before they can return to the bugfix queue.

**Tech Stack:** Markdown Codex skills and provider contracts, Python 3.13 configuration/model/scheduler code, Bash runner, JSON profiles, Python `unittest`.

**Dependencies:** Complete `docs/superpowers/plans/2026-07-26-ticket-run-coordinator.md` and `docs/superpowers/plans/2026-07-26-bugfixer-provider-runtime.md` first. Confirm targetless scheduled runs can call `bind-target` before tracker or checkout mutation.

**Execution context:** Work in the current checkout. The feature explicitly benefits from isolated per-ticket checkouts because the coordinator permits parallel ticket runs; do not create a separate development worktree merely to implement this plan. Preserve and stage around unrelated changes.

---

## File map

- Create `references/CHANGE-DELIVERY.md`: normative provider-neutral claim-to-close lifecycle shared by acting roles.
- Modify `skills/implementer-run/SKILL.md`: read the shared contract and exclude `bug` tickets in both todo and review paths.
- Create `skills/bugfixer-run/SKILL.md`: exclusive bug selection, risk routing, red reproduction, fix, review continuation, and merge.
- Modify `skills/unblock/SKILL.md`: add the investigated sensitive-bug decision and approval marker.
- Modify `scripts/pitcrew_config.py` and `tests/test_config.py`: validate bugfixer policy.
- Modify `profiles/getbill.json` and `profiles/generic.json`: add model/policy configuration without enabling unsafe generic work.
- Create `scripts/pitcrew_bugfix_lifecycle.py`: fail-closed, provider-neutral lifecycle decision engine.
- Create `tests/fixtures/bugfixer/github.json` and `tests/fixtures/bugfixer/gitlab.json`: equivalent provider snapshots for lifecycle parity.
- Create `tests/fixtures/bugfixer/negative.json`: forged and malformed evidence that must fail closed.
- Create `tests/test_bugfix_lifecycle.py`: exercise the five required outcomes against both provider fixtures.
- Modify `scripts/pitcrew_models.py` and `tests/test_models.py`: register the quality/high role defaults.
- Modify `bin/pitcrew-codex.sh`, `bin/pitcrew-schedule.py`, `bin/install.sh`, and their tests: admit, sandbox, install, and schedule the role.
- Modify `tests/test_plugin_contract.py` and `.codex-plugin/plugin.json`: expose the new skill.
- Modify `references/TOPOLOGY.md`, `references/DIRECTED-TARGET.md`, `references/SCHEDULED-TASKS.md`, `README.md`, and `tests/test_skill_contracts.py`: document exclusive routing and lifecycle gates.

## Task 1: Extract a normative shared delivery contract

**Files:**
- Create: `references/CHANGE-DELIVERY.md`
- Modify: `skills/implementer-run/SKILL.md`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing shared-contract tests**

Add to `ReferenceContractTest`:

```python
def test_shared_change_delivery_contract(self):
    self.assert_markers(
        "references/CHANGE-DELIVERY.md",
        "Bind target before mutation",
        "Expected remote state",
        "Lookup before create",
        "Current head SHA",
        "Reviewer signed off",
        "Validator passed",
        "Human go",
        "Required CI checks",
        "Close lifecycle",
        "Uncertain result",
        "Two fix attempts",
    )

def test_implementer_uses_shared_delivery_and_excludes_bugs(self):
    implementer = (
        ROOT / "skills/implementer-run/SKILL.md"
    ).read_text(encoding="utf-8")
    self.assertIn("references/CHANGE-DELIVERY.md", implementer)
    self.assertIn(
        "drop every ticket carrying `$BUG_LABEL` before sorting",
        implementer,
    )
    self.assertIn(
        "exclude `$BUG_LABEL` from review continuation",
        implementer,
    )
```

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_shared_change_delivery_contract \
  tests.test_skill_contracts.ReferenceContractTest.test_implementer_uses_shared_delivery_and_excludes_bugs -v
```

Expected: FAIL because the shared reference and exclusions do not exist.

- [ ] **Step 3: Create `references/CHANGE-DELIVERY.md`**

Write a complete normative contract with this ordered lifecycle:

```markdown
# Shared change delivery contract

## Bind target before mutation

For a coordinated run, bind the canonical issue target to `PITCREW_RUN_ID`
before changing tracker state, labels, comments, repository files, branches, or
changes. A binding conflict performs no mutation and selects another eligible
item or returns a structured no-op.

## Claim

Re-fetch the issue. Verify provider binding, open state, expected logical state,
required routing labels, unfinished blockers, and absence of another active
owner. Preserve non-state labels and replace only the logical state label.
Re-read and verify the expected remote state.

## Change identity

Derive one stable operation marker and ticket branch. Lookup before create by
provider, repository, ticket, and exact source branch. After an uncertain
result, re-read before retrying. Never create a second open change for the same
operation.

## Review continuation

Evaluate only the current head SHA. Reviewer signed off, validator passed,
Human go, and Required CI checks are four independent signals. A verdict or
validation on an older SHA is stale. Change requests permit at most Two fix
attempts, each followed by focused verification.

## Merge and Close lifecycle

Immediately before merge, re-fetch provider binding, issue, change, discussions,
review, validation, CI, source/target branches, and Current head SHA. Merge only
when every role-specific gate passes. Close lifecycle by applying done,
commenting with the idempotency marker, closing the issue, and re-reading both
resources.

## Failure behavior

Provider mismatch, lost eligibility, exhausted attempts, or ambiguous state
fails closed. Preserve the open change when human pickup is useful. Redact
secrets. Return a structured no-op when no mutation occurred.
```

Add provider-neutral pseudocode for `LIST_ELIGIBLE_WORK`,
`INSPECT_TRACKER_ITEM`, `CLAIM_WORK`, `FIND_OR_CREATE_CHANGE`,
`READ_CHANGE_REVIEWS`, `READ_CHANGE_CHECKS`, `MERGE_CHANGE`, and
`CLOSE_LIFECYCLE`; point each operation to the selected provider reference.

- [ ] **Step 4: Make implementer consume the contract**

At the top of `skills/implementer-run/SKILL.md`, after provider references, add:

```markdown
Read `references/CHANGE-DELIVERY.md` before selection. It is normative for
binding, claim, idempotency, review continuation, merge, and closeout. This
skill owns only non-bug selection and implementation policy.
```

In STEP A, filter review tickets before any verdict lookup:

```markdown
**Exclusive bug ownership:** exclude `$BUG_LABEL` from review continuation.
`bugfixer-run` owns bug review, fixes, merge, and closeout.
```

In STEP B, immediately after the investigate filter, add:

```markdown
Drop every ticket carrying `$BUG_LABEL` before sorting. Bugs are exclusively
owned by `$pitcrew:bugfixer-run`; do not claim, reset, repair, merge, or close
them from this role.
```

Remove duplicated provider-neutral rules only where the new reference contains
the same complete invariant. Keep implementer-specific Slack digest, scope
checks, plan handling, low-risk behavior, test-flow follow-up, and parachute.

- [ ] **Step 5: Run contract tests**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: PASS.

- [ ] **Step 6: Commit the shared contract**

```bash
git add references/CHANGE-DELIVERY.md skills/implementer-run/SKILL.md tests/test_skill_contracts.py
git commit -m "refactor: share change delivery contract"
```

## Task 2: Validate bugfixer risk policy

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing policy tests**

Add:

```python
def valid_bugfixer_policy(self):
    return {
        "sensitive_labels": [
            "pitcrew-category::security",
            "security",
            "authentication",
            "payment",
            "privacy",
        ],
        "risky_categories_regex": (
            "security|auth|payment|PII|secret|token"
        ),
        "sensitive_approved_label": "pitcrew-risk::approved",
    }

def test_bugfixer_policy_is_complete_and_regex_is_valid(self):
    profile = load_profile(ROOT / "profiles/getbill.json")
    profile["bugfixer"] = self.valid_bugfixer_policy()
    validate(profile)

    for key in (
        "sensitive_labels",
        "risky_categories_regex",
        "sensitive_approved_label",
    ):
        broken = copy.deepcopy(profile)
        del broken["bugfixer"][key]
        with self.subTest(key=key):
            with self.assertRaisesRegex(ConfigError, f"bugfixer.{key}"):
                validate(broken)

    broken = copy.deepcopy(profile)
    broken["bugfixer"]["risky_categories_regex"] = "("
    with self.assertRaisesRegex(ConfigError, "bugfixer.risky_categories_regex"):
        validate(broken)

def test_bugfixer_sensitive_labels_are_unique_non_empty_strings(self):
    profile = load_profile(ROOT / "profiles/getbill.json")
    for labels in ([], ["security", "security"], ["security", ""], "security"):
        broken = copy.deepcopy(profile)
        broken["bugfixer"] = self.valid_bugfixer_policy()
        broken["bugfixer"]["sensitive_labels"] = labels
        with self.subTest(labels=labels):
            with self.assertRaisesRegex(ConfigError, "bugfixer.sensitive_labels"):
                validate(broken)
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_config.ConfigTest.test_bugfixer_policy_is_complete_and_regex_is_valid \
  tests.test_config.ConfigTest.test_bugfixer_sensitive_labels_are_unique_non_empty_strings -v
```

Expected: FAIL because `validate` ignores `bugfixer`.

- [ ] **Step 3: Implement policy validation**

Add:

```python
def _validate_bugfixer(config: Mapping[str, Any]) -> None:
    policy = config.get("bugfixer")
    if policy is None:
        return
    if not isinstance(policy, Mapping):
        raise ConfigError("bugfixer must be an object")
    allowed = {
        "sensitive_labels",
        "risky_categories_regex",
        "sensitive_approved_label",
    }
    if set(policy) != allowed:
        missing = sorted(allowed - set(policy))
        extra = sorted(set(policy) - allowed)
        field = (missing or extra)[0]
        raise ConfigError(f"bugfixer.{field} is invalid")
    labels = policy["sensitive_labels"]
    if (
        not isinstance(labels, list)
        or not labels
        or any(not isinstance(label, str) or not label for label in labels)
        or len(labels) != len(set(labels))
    ):
        raise ConfigError(
            "bugfixer.sensitive_labels must be a unique non-empty string array"
        )
    regex = policy["risky_categories_regex"]
    if not isinstance(regex, str) or not regex:
        raise ConfigError(
            "bugfixer.risky_categories_regex must be a non-empty string"
        )
    try:
        re.compile(regex, re.IGNORECASE)
    except re.error as error:
        raise ConfigError(
            "bugfixer.risky_categories_regex must compile"
        ) from error
    approved = policy["sensitive_approved_label"]
    if not isinstance(approved, str) or not approved:
        raise ConfigError(
            "bugfixer.sensitive_approved_label must be a non-empty string"
        )
```

Call `_validate_bugfixer(config)` from `validate`.

- [ ] **Step 4: Add explicit profile policy**

Add the tested policy to `profiles/getbill.json`. Leave `profiles/generic.json`
without a `bugfixer` block so a generic project stays disabled until configured.
Do not create remote labels from profile loading.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tests.test_config -v
git add scripts/pitcrew_config.py profiles/getbill.json tests/test_config.py
git commit -m "feat: validate bugfixer risk policy"
```

Expected: tests PASS; generic remains valid and disabled.

## Task 3: Define the exclusive evidence-first bugfixer skill

**Required sub-skill for this task:** Invoke
`superpowers:writing-skills` before creating or validating the new skill.

**Files:**
- Create: `skills/bugfixer-run/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing skill-contract tests**

Add:

```python
def test_bugfixer_is_exclusive_evidence_first_and_human_gated(self):
    self.assert_markers(
        "skills/bugfixer-run/SKILL.md",
        "references/CHANGE-DELIVERY.md",
        'label="$AGENT_LABEL"',
        'state="$STATE_TODO"',
        "$BUG_LABEL",
        "$INVESTIGATE_LABEL",
        "bind-target",
        "valid red reproduction",
        "before modifying production code",
        "same reproduction must turn green",
        "pitcrew:bugfix:red:v1",
        "pitcrew:bugfix:green:v1",
        "pitcrew:bugfix:ready:v1",
        "pitcrew:bugfix:blocked:v1",
        "two fix attempts",
        "reviewer signed off on the current head SHA",
        "validator passed the current head SHA",
        "human `go`",
        "required CI checks are green",
        "CLOSE_LIFECYCLE",
        "structured no-op",
    )
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_bugfixer_is_exclusive_evidence_first_and_human_gated -v
```

Expected: FAIL because the skill is absent.

- [ ] **Step 3: Create the skill frontmatter and load sequence**

Create:

```markdown
---
name: bugfixer-run
description: Use when reproducing and fixing one eligible configured bug issue through a reviewed change.
---

Read `references/CODEX-RUNTIME.md`, `references/PROVIDERS.md`, the selected
provider reference, `references/CHANGE-DELIVERY.md`,
`references/DIRECTED-TARGET.md`, repository `AGENTS.md`, project lessons and
topology before lookup or mutation. Perform exactly one bounded pass.
```

Resolve the same tracker, forge, repository, state, Slack, and safety values as
implementer plus:

```text
BUGFIXER_SENSITIVE_LABELS=<bugfixer.sensitive_labels>
BUGFIXER_RISKY_RE=<bugfixer.risky_categories_regex>
SENSITIVE_APPROVED_LABEL=<bugfixer.sensitive_approved_label>
```

Before selection, list configured provider labels and require exact presence of
the agent, bug, investigate, approval, and five lifecycle labels. A missing
label returns a structured no-op; the skill never creates labels.

- [ ] **Step 4: Write the deterministic selection and risk steps**

Use this exact order:

```markdown
STEP 0 — Reconcile an orphan only when its canonical ticket has no active run
and no existing open change.

STEP A — Service `$STATE_REVIEW` issues carrying both `$AGENT_LABEL` and
`$BUG_LABEL`. Apply only current-head review/validation/go/CI signals.

STEP B — Query `label="$AGENT_LABEL", state="$STATE_TODO"`, then keep only
issues carrying `$BUG_LABEL`, without `$INVESTIGATE_LABEL`, and without open
blockers. Sort quick-win, priority, age.

STEP C — Read the full issue and comments. Match exact sensitive labels and
`$BUGFIXER_RISKY_RE`, but perform no tracker or repository mutation yet. Hold
the routing decision in memory.

STEP D — Call coordinator `bind-target` with the canonical issue URL before
the sensitive-routing comment, label transition, claim, or checkout mutation.
On conflict, select another candidate or no-op.

STEP E — If sensitive approval is absent, preserve categorization, remove
`$AGENT_LABEL`, add `$INVESTIGATE_LABEL`, keep `$STATE_TODO`, post one marked
routing comment, finalize the coordinated run, and stop without repository
writes. Otherwise claim `$STATE_PROCESSING` and create one isolated ticket
checkout.
```

Define valid sensitive approval as all three signals:

1. `SENSITIVE_APPROVED_LABEL` exists;
2. investigation findings marker exists;
3. latest approval marker
   `<!-- pitcrew:bugfix-sensitive-approved:v1 ticket=<id> -->`
   names the configured human and scoped constraints.

- [ ] **Step 5: Write the red-to-green implementation steps**

Add:

```markdown
STEP F — Reproduce. Add the smallest automated regression test or deterministic
local flow. Run it before production-code edits. A valid red reproduction fails
on the reported behavior, not setup, an unrelated assertion, or an unavailable
external service. Post a redacted command/assertion/revision evidence comment.

If no valid red reproduction exists during the bounded run: discard uncommitted
isolated-checkout edits, create no change, move to `$STATE_BLOCKED`, post
attempted commands/results/hypotheses, finalize the run, and stop.

STEP G — Fix. Apply the smallest production change. The same reproduction must
turn green. Run focused tests and repository-required validation. Architecture
ambiguity, unavailable services, insufficient confidence, or broad blast radius
routes to `$STATE_BLOCKED`.

STEP H — Open or reuse one idempotent PR/MR. Include issue, root cause, red
evidence, fix, green evidence, validation, and risks. Move to `$STATE_REVIEW`.
```

Require the reproduction test to remain in the opened change. Do not create the
implementer's deferred QA-coverage ticket for a bug whose regression test is
already part of the fix.

Use exact machine-readable comment markers so dashboard adapters never infer
state from prose:

```markdown
<!-- pitcrew:bugfix:red:v1 -->
Command: `<redacted command>`
Observed: <failing assertion or result>
Expected: <expected result>
Revision: `<sha>`

<!-- pitcrew:bugfix:green:v1 -->
Command: `<same redacted command>`
Result: passed
Validation: `<redacted focused commands>`

<!-- pitcrew:bugfix:ready:v1 sha=<head-sha> -->
Change: <PR/MR URL>
Root cause: <one line>

<!-- pitcrew:bugfix:blocked:v1 -->
Reason: <one line>
Attempts: <redacted commands and outcomes>
```

The angle-bracket values above are runtime substitutions defined by the skill,
not unresolved implementation placeholders.

- [ ] **Step 6: Write review continuation and terminal behavior**

Add:

```markdown
Change requests permit two fix attempts. Each pushes a new head and reruns the
same reproduction plus required validation.

Merge only when the reviewer signed off on the current head SHA, the validator
passed the current head SHA, the configured human posted actionable `go` after
the first ready marker, and all required CI checks are green.

Immediately re-fetch expected issue state, source/target branches, open change,
discussions, reviews, validation marker, checks, and head SHA. Then squash
merge, delete the confirmed source branch, call `CLOSE_LIFECYCLE`, verify done
plus closed, post the bounded summary, and finalize the coordinated run.
```

Keep prod/preprod actions prohibited and preserve existing GetBill Graphify,
staging, reference, secret, database, and destructive-Git gates.

- [ ] **Step 7: Run and commit the skill**

```bash
python3 -m unittest tests.test_skill_contracts -v
git add skills/bugfixer-run/SKILL.md tests/test_skill_contracts.py
git commit -m "feat: add evidence-first bugfixer skill"
```

Expected: tests PASS.

## Task 4: Add the sensitive-bug unblock decision

**Files:**
- Modify: `skills/unblock/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write a failing marker test**

```python
def test_unblock_has_safe_sensitive_bug_return_path(self):
    self.assert_markers(
        "skills/unblock/SKILL.md",
        "sensitive-bug",
        "Authorize bounded bugfix",
        "Human pickup",
        "Reject or duplicate",
        "pitcrew:bugfix-sensitive-approved:v1",
        "SENSITIVE_APPROVED_LABEL",
        "remove `$INVESTIGATE_LABEL`",
        "restore `$AGENT_LABEL`",
        "completed investigation findings",
    )
```

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_unblock_has_safe_sensitive_bug_return_path -v
```

Expected: FAIL on the new markers.

- [ ] **Step 3: Add classification and choices**

Resolve `BUG_LABEL` and `SENSITIVE_APPROVED_LABEL`. Classify `sensitive-bug`
before generic `auth-sensitive` when the blocked ticket carries both
`BUG_LABEL` and `INVESTIGATE_LABEL` and contains completed investigation
findings.

Use exactly these choices:

```text
Authorize bounded bugfix
Human pickup
Reject or duplicate
```

The authorization path requires non-empty scope constraints from the human. It
posts:

```markdown
<!-- pitcrew:bugfix-sensitive-approved:v1 ticket=<TICKET-id> -->
Unblocker: sensitive bugfix authorized by <configured-human>.
Scope constraints: <verbatim human constraints>
Investigation findings: <finding marker or comment URL>
```

Then preserve `BUG_LABEL`, remove `INVESTIGATE_LABEL`, restore `AGENT_LABEL`,
add `SENSITIVE_APPROVED_LABEL`, and move to `STATE_TODO`. Re-read and verify all
four label/state conditions. On verification failure, return to blocked without
claiming approval.

`Human pickup` leaves blocked and removes `AGENT_LABEL`. `Reject or duplicate`
records the reason, applies done, and closes through the provider contract.

- [ ] **Step 4: Run and commit**

```bash
python3 -m unittest tests.test_skill_contracts -v
git add skills/unblock/SKILL.md tests/test_skill_contracts.py
git commit -m "feat: gate sensitive bugfix approval"
```

Expected: tests PASS.

## Task 5: Lock lifecycle behavior across GitHub and GitLab

**Files:**
- Create: `scripts/pitcrew_bugfix_lifecycle.py`
- Create: `tests/fixtures/bugfixer/github.json`
- Create: `tests/fixtures/bugfixer/gitlab.json`
- Create: `tests/fixtures/bugfixer/negative.json`
- Create: `tests/test_bugfix_lifecycle.py`
- Modify: `skills/bugfixer-run/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write equivalent provider fixtures**

Create one JSON array per provider with these exact scenario names and expected
actions:

```json
[
  {"name": "ordinary_red_to_green", "expected": "open_change", "snapshot": {}},
  {"name": "unreproducible", "expected": "block_unreproducible", "snapshot": {}},
  {"name": "sensitive_routing", "expected": "route_investigate", "snapshot": {}},
  {"name": "review_or_validator_rejection", "expected": "address_review", "snapshot": {}},
  {"name": "all_gates_current_head", "expected": "merge_close", "snapshot": {}}
]
```

Populate every `snapshot` with this provider-neutral schema:

```json
{
  "provider": "github",
  "ticket_url": "https://github.example/acme/app/issues/17",
  "phase": "claimed",
  "current_head_sha": "abc123",
  "sensitive": {
    "matched": false,
    "approval_label": false,
    "investigation_marker": false,
    "approval_marker": false
  },
  "reproduction": {
    "attempted": true,
    "valid_red": true,
    "green": true,
    "same_command": true
  },
  "review": {
    "status": "pending",
    "reviewer_head_sha": null,
    "validator_status": "pending",
    "validator_head_sha": null,
    "ready_head_sha": null,
    "human_go_after_ready": false,
    "ci_green": false,
    "fix_attempts": 0
  }
}
```

The GitLab fixture uses `provider: "gitlab"` and a canonical GitLab issue URL
but otherwise represents the same five facts. Vary only facts needed for each
expected action. These checked-in fixtures represent the normalized output of
the provider operations; no network credentials are allowed in this test.

Create `negative.json` with:

1. an otherwise valid `ordinary_red_to_green` snapshot whose
   `reproduction.attempted` is `false` but whose `valid_red`, `green`, and
   `same_command` values are forged to `true`, expecting `hold_fix`;
2. valid baseline snapshots plus overrides for malformed nested evidence:
   `sensitive.matched: "yes"`, `reproduction.attempted: "yes"`,
   `review.fix_attempts: []`, and `review.ci_green: 1`, each expecting
   `LifecycleEvidenceError`.

- [ ] **Step 2: Write failing behavioral parity tests**

Create:

```python
import json
import unittest
from pathlib import Path

from scripts.pitcrew_bugfix_lifecycle import LifecycleEvidenceError, decide

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/bugfixer"
REQUIRED_SCENARIOS = {
    "ordinary_red_to_green",
    "unreproducible",
    "sensitive_routing",
    "review_or_validator_rejection",
    "all_gates_current_head",
}


class BugfixLifecycleParityTest(unittest.TestCase):
    def load(self, provider: str) -> list[dict]:
        return json.loads((FIXTURES / f"{provider}.json").read_text())

    def test_required_scenarios_have_provider_parity(self):
        decisions = {}
        for provider in ("github", "gitlab"):
            cases = self.load(provider)
            names = [case["name"] for case in cases]
            self.assertEqual(REQUIRED_SCENARIOS, set(names))
            self.assertEqual(len(REQUIRED_SCENARIOS), len(names))
            decisions[provider] = {
                case["name"]: decide(case["snapshot"])
                for case in cases
            }
            self.assertEqual(
                {case["name"]: case["expected"] for case in cases},
                decisions[provider],
            )
        self.assertEqual(decisions["github"], decisions["gitlab"])

    def test_current_head_mismatch_never_merges(self):
        snapshot = self.load("github")[-1]["snapshot"]
        snapshot["review"]["validator_head_sha"] = "stale"
        self.assertEqual("hold_review", decide(snapshot))

    def test_incomplete_sensitive_approval_routes_to_investigation(self):
        snapshot = self.load("gitlab")[2]["snapshot"]
        snapshot["sensitive"]["approval_marker"] = False
        self.assertEqual("route_investigate", decide(snapshot))

    def test_forged_red_green_without_attempt_is_held(self):
        case = json.loads((FIXTURES / "negative.json").read_text())[
            "attempted_false"
        ]
        self.assertEqual(case["expected"], decide(case["snapshot"]))

    def test_malformed_nested_evidence_raises_typed_error(self):
        data = json.loads((FIXTURES / "negative.json").read_text())
        for case in data["malformed"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(LifecycleEvidenceError):
                    decide(case["snapshot"])
```

- [ ] **Step 3: Run and confirm failure**

```bash
python3 -m unittest tests.test_bugfix_lifecycle -v
```

Expected: FAIL because the lifecycle decision module is absent.

- [ ] **Step 4: Implement the fail-closed decision engine**

Create `validate_snapshot(snapshot)` and
`decide(snapshot: Mapping[str, object]) -> str`. Validation runs before any
decision and enforces:

- `provider` is exactly `github` or `gitlab`;
- `ticket_url`, `phase`, and `current_head_sha` are non-empty strings, with
  `phase` in `claimed`, `review`, or `terminal`;
- all four `sensitive` fields and all four `reproduction` fields are actual
  booleans (`type(value) is bool`);
- `review.status` is `pending`, `approved`, or `changes_requested`;
- `review.validator_status` is `pending`, `passed`, or `failed`;
- each review SHA is either `None` or a non-empty string;
- `human_go_after_ready` and `ci_green` are actual booleans;
- `fix_attempts` is a non-negative integer and not a boolean.

Every missing key, wrong container, invalid enum, or wrong nested type raises
`LifecycleEvidenceError` with the dotted field name. No raw `TypeError`,
`KeyError`, or coercion may escape.

Then use this ordered logic:

```python
def decide(snapshot: Mapping[str, object]) -> str:
    evidence = validate_snapshot(snapshot)
    sensitive = evidence["sensitive"]
    reproduction = evidence["reproduction"]
    review = evidence["review"]
    phase = evidence["phase"]
    current_head = evidence["current_head_sha"]

    approval_complete = all(
        sensitive.get(key) is True
        for key in ("approval_label", "investigation_marker", "approval_marker")
    )
    if sensitive.get("matched") is True and not approval_complete:
        return "route_investigate"

    if phase == "claimed":
        if reproduction.get("attempted") is True and (
            reproduction.get("valid_red") is not True
        ):
            return "block_unreproducible"
        if all(
            reproduction.get(key) is True
            for key in ("attempted", "valid_red", "green", "same_command")
        ):
            return "open_change"
        return "hold_fix"

    if phase == "review":
        rejected = (
            review.get("status") == "changes_requested"
            or review.get("validator_status") == "failed"
        )
        if rejected:
            return (
                "address_review"
                if review.get("fix_attempts", 0) < 2
                else "block_review"
            )
        current_head_gates = all(
            (
                review.get("ready_head_sha") == current_head,
                review.get("reviewer_head_sha") == current_head,
                review.get("validator_head_sha") == current_head,
                review.get("status") == "approved",
                review.get("validator_status") == "passed",
                review.get("human_go_after_ready") is True,
                review.get("ci_green") is True,
            )
        )
        return "merge_close" if current_head_gates else "hold_review"

    return "no_op"
```

Add an `evaluate` CLI subcommand accepting `--snapshot PATH`, printing
`{"action": "<decision>"}` on stdout, and returning non-zero with a redacted
error on invalid input. It must perform no provider call or mutation.

- [ ] **Step 5: Make the skill consume the decision engine**

Add `scripts/pitcrew_bugfix_lifecycle.py evaluate --snapshot <path>` to the
bugfixer contract at three checkpoints:

1. after `bind-target` and before sensitive routing or claim;
2. after reproduction/verification evidence and before opening a change;
3. after re-fetching current-head gates and before merge/close.

Only the matching action permits the next mutation. An invalid snapshot or
unexpected action fails closed to `$STATE_BLOCKED`; it never guesses from
provider prose. Extend `test_bugfixer_is_exclusive_evidence_first_and_human_gated`
with the script path plus `route_investigate`, `open_change`, and `merge_close`
markers.

- [ ] **Step 6: Run parity and contract tests, then commit**

```bash
python3 -m unittest \
  tests.test_bugfix_lifecycle tests.test_skill_contracts -v
git add scripts/pitcrew_bugfix_lifecycle.py \
  tests/fixtures/bugfixer/github.json tests/fixtures/bugfixer/gitlab.json \
  tests/fixtures/bugfixer/negative.json \
  tests/test_bugfix_lifecycle.py skills/bugfixer-run/SKILL.md \
  tests/test_skill_contracts.py
git commit -m "feat: enforce provider-neutral bugfix lifecycle"
```

Expected: all five scenarios PASS for both providers, the result dictionaries
are equal, and stale-head evidence cannot produce `merge_close`.

## Task 6: Register, schedule, and sandbox the role

**Required sub-skill for plugin metadata:** Invoke `plugin-creator` before
changing the plugin cachebuster or refreshing the local installed plugin.

**Files:**
- Modify: `scripts/pitcrew_models.py`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `bin/pitcrew-schedule.py`
- Modify: `bin/install.sh`
- Modify: `.codex-plugin/plugin.json`
- Test: `tests/test_models.py`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_schedule.py`
- Test: `tests/test_plugin_contract.py`

- [ ] **Step 1: Write failing registration tests**

Update expected role maps and add assertions:

```python
self.assertEqual("gpt-5.6-sol", DEFAULT_MODELS["bugfixer-run"])
self.assertEqual("high", DEFAULT_REASONING_EFFORTS["bugfixer-run"])
```

In scheduler tests, require `bugfixer-run` at `900` seconds, enabled for the
GetBill profile and disabled with `bugfixer policy is not configured` for the
generic profile. In runner tests, require `bugfixer-run` in the allowlist and
the same isolated-checkout sandbox path as `implementer-run`.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
python3 -m unittest \
  tests.test_models tests.test_config tests.test_cli tests.test_schedule \
  tests.test_plugin_contract -v
```

Expected: FAIL because the role is unregistered.

- [ ] **Step 3: Register model and profile defaults**

Add:

```python
DEFAULT_MODELS["bugfixer-run"] = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORTS["bugfixer-run"] = "high"
```

Represent these as literal entries in the existing dictionaries, not mutations
after declaration. Add `bugfixer-run` to both profile `agents` maps. Keep the
GetBill risk policy from Task 2; generic has model metadata but no risk policy.

- [ ] **Step 4: Register runner, installer, and plugin**

Add `bugfixer-run` to the shell runner and compatibility installer allowlists.
Change the isolated-checkout sandbox condition to:

```bash
if [[ "$SKILL" == "implementer-run" || "$SKILL" == "bugfixer-run" ]]; then
  SANDBOX_MODE="danger-full-access"
fi
```

The role still inherits the caller approval policy and every project safety
gate. Add the skill to plugin contract expectations and bump only the plugin
cachebuster according to the repository's plugin workflow when implementation
is complete.

- [ ] **Step 5: Make scheduler eligibility configuration-driven**

Import `Mapping` from `collections.abc` and `load_runtime_config` from
`scripts.pitcrew_config` into `bin/pitcrew-schedule.py`. Add the schedule row:

```python
("bugfixer-run", 900, True, "")
```

Before returning project entries, resolve:

```python
def bugfixer_eligibility(config: Mapping[str, object]) -> tuple[bool, str]:
    providers = config.get("providers", {})
    pair = (
        providers.get("forge"),
        providers.get("tracker"),
    ) if isinstance(providers, Mapping) else (None, None)
    if pair not in {("github", "github"), ("gitlab", "gitlab")}:
        return False, "native GitHub or GitLab issue tracker is not configured"
    if not isinstance(config.get("bugfixer"), Mapping):
        return False, "bugfixer policy is not configured"
    if not config.get("repos"):
        return False, "no repository binding is configured"
    return True, ""
```

Apply this only to the bugfixer entry for the requested project. `list`,
`render`, `install`, `status`, `stop`, `stop-all`, and `resume-all` must all use
the same resolved entry list.

- [ ] **Step 6: Run and commit registration**

```bash
python3 -m unittest \
  tests.test_models tests.test_config tests.test_cli tests.test_schedule \
  tests.test_plugin_contract -v
git add scripts/pitcrew_models.py profiles/getbill.json profiles/generic.json \
  bin/pitcrew-codex.sh bin/pitcrew-schedule.py bin/install.sh \
  .codex-plugin/plugin.json tests/test_models.py tests/test_config.py \
  tests/test_cli.py tests/test_schedule.py tests/test_plugin_contract.py
git commit -m "feat: register scheduled bugfixer role"
```

Expected: tests PASS.

## Task 7: Document topology and verify the complete lifecycle

**Files:**
- Modify: `README.md`
- Modify: `references/TOPOLOGY.md`
- Modify: `references/DIRECTED-TARGET.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `tests/test_docs.py`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Add failing documentation assertions**

Require all of:

```python
markers = (
    "$pitcrew:bugfixer-run",
    "agent + bug + todo",
    "valid red reproduction",
    "investigate-run",
    "reviewer-run",
    "validator-run",
    "human `go`",
    "implementer-run excludes bugs",
)
```

- [ ] **Step 2: Run and confirm failure**

```bash
python3 -m unittest tests.test_docs tests.test_skill_contracts -v
```

Expected: FAIL on missing bugfixer documentation.

- [ ] **Step 3: Update crew topology and operator docs**

Add this routing to `references/TOPOLOGY.md`:

```text
agent + bug + todo -> bugfixer -> review -> human go -> done
                         |
                    sensitive
                         v
             investigate -> unblock approval
```

State that `implementer-run` excludes `bug` in todo and review, directed targets
do not bypass eligibility, and the approval label alone is insufficient without
investigation plus the human marker.

Document the 15-minute bounded schedule, GetBill policy, missing-label no-op, and
the operator step to create `pitcrew-risk::approved` before enabling the role.

- [ ] **Step 4: Run complete deterministic verification**

Run:

```bash
python3 -m unittest \
  tests.test_models tests.test_config tests.test_cli tests.test_schedule \
  tests.test_plugin_contract tests.test_skill_contracts \
  tests.test_bugfix_lifecycle tests.test_docs -v
bash tests/run.sh
```

Expected: all tests PASS.

- [ ] **Step 5: Commit lifecycle documentation**

```bash
git add README.md references/TOPOLOGY.md references/DIRECTED-TARGET.md \
  references/SCHEDULED-TASKS.md tests/test_docs.py tests/test_skill_contracts.py
git commit -m "docs: describe exclusive bugfixer lifecycle"
```
