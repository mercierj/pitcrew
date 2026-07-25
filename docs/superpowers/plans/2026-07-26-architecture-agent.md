# Architecture Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a weekly, manually triggerable `architecture-run` role that records high-confidence structural proposals for dashboard approval and paced manager routing.

**Architecture:** Extend the common model/configuration contract with explicit reasoning effort, register the new scheduled role, and reuse the deterministic coverage helper with a separate architecture state file. Architecture proposals use the existing local proposal ledger with a new category; approved proposals are consumed idempotently by `manager-run` instead of being created remotely by the dashboard.

**Tech Stack:** Python 3 standard library, Bash, JSON, Markdown Codex skills, `launchd`, Python `unittest`, vanilla JavaScript dashboard.

---

### Task 1: Add explicit reasoning-effort configuration

**Files:**
- Modify: `scripts/pitcrew_models.py`
- Modify: `scripts/pitcrew_config.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `dashboard/app.js`
- Modify: `tests/test_models.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing model and configuration tests**

Add imports and assertions for optional per-role effort defaults and resolution:

```python
from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    DEFAULT_REASONING_EFFORTS,
    resolve_reasoning_effort,
)

def test_reasoning_effort_defaults_reference_supported_roles(self):
    self.assertTrue(set(DEFAULT_REASONING_EFFORTS) <= set(DEFAULT_MODELS))
    self.assertTrue(
        set(DEFAULT_REASONING_EFFORTS.values())
        <= {"low", "medium", "high", "xhigh"}
    )

def test_reasoning_effort_override_wins(self):
    config = {
        "agents": {
            "research-run": {
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
            }
        }
    }
    self.assertEqual("low", resolve_reasoning_effort(config, "research-run"))
```

Update the profile assertion in `tests/test_config.py` to require both fields:

```python
self.assertEqual(
    {
        role: {
            **{"model": model},
            **(
                {"reasoning_effort": DEFAULT_REASONING_EFFORTS[role]}
                if role in DEFAULT_REASONING_EFFORTS
                else {}
            ),
        }
        for role, model in DEFAULT_MODELS.items()
    },
    profile["agents"],
    relative,
)
```

Add a validation test that rejects `"reasoning_effort": "extreme"`.
Add a dashboard snapshot assertion:

```python
self.assertEqual(
    None,
    roles["research-run"]["configured_reasoning_effort"],
)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_models tests.test_config -v
```

Expected: failure because `DEFAULT_REASONING_EFFORTS` and
`resolve_reasoning_effort` do not exist and agent entries currently reject the
new field.

- [ ] **Step 3: Implement effort defaults and resolution**

Add to `scripts/pitcrew_models.py`:

```python
REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
DEFAULT_REASONING_EFFORTS: dict[str, str] = {}

def resolve_reasoning_effort(config: Mapping, skill: str) -> str | None:
    if skill not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {skill}")
    agents = config.get("agents", {})
    entry = agents.get(skill, {}) if isinstance(agents, Mapping) else {}
    return entry.get("reasoning_effort", DEFAULT_REASONING_EFFORTS.get(skill))
```

In `scripts/pitcrew_config.py`, require `model`, accept an optional
`reasoning_effort` for backward compatibility with existing runtime
configurations, reject every other key, validate a present effort against
`REASONING_EFFORTS`, and preserve the resolved effort in
`update_runtime_model`:

```python
allowed = {"model", "reasoning_effort"}
if "model" not in entry or not set(entry) <= allowed:
    raise ConfigError(f"agents.{skill} has unsupported fields")
if entry.get("model") not in MODEL_CATALOG:
    raise ConfigError(f"agents.{skill}.model is unsupported")
if (
    "reasoning_effort" in entry
    and entry.get("reasoning_effort") not in REASONING_EFFORTS
):
    raise ConfigError(f"agents.{skill}.reasoning_effort is unsupported")
```

```python
previous = agents.get(skill, {})
agents[skill] = {"model": model}
if "reasoning_effort" in previous:
    agents[skill]["reasoning_effort"] = previous["reasoning_effort"]
```

- [ ] **Step 4: Preserve existing profiles without changing unrelated roles**

Leave existing agent entries model-only. The new optional field is pinned only
for roles that define an explicit default in later tasks. This avoids changing
the reasoning policy of unrelated agents.

- [ ] **Step 5: Pass the resolved effort to Codex**

Add a `reasoning-effort` CLI subcommand beside the existing `model` subcommand in
`scripts/pitcrew_config.py`. In `bin/pitcrew-codex.sh`, resolve it and pass:

```bash
REASONING_EFFORT="$(
  python3 "$REPO_ROOT/scripts/pitcrew_config.py" reasoning-effort \
    --project "$PROJECT" --skill "$SKILL"
)"

if [[ -n "$REASONING_EFFORT" ]]; then
  CODEX_ARGS+=(
    -c "model_reasoning_effort=\"$REASONING_EFFORT\""
  )
fi
```

Extend dry-run output with
`reasoning_effort=${REASONING_EFFORT:-inherited}`. Add one CLI fixture that
configures `research-run` with `medium` and asserts the real invocation contains
`model_reasoning_effort="medium"`. Add another assertion that a legacy
model-only entry emits `reasoning_effort=inherited` and no reasoning override.

- [ ] **Step 6: Expose configured effort on standard agent cards**

Import `resolve_reasoning_effort` in `scripts/pitcrew_dashboard.py` and add:

```python
"configured_reasoning_effort": resolve_reasoning_effort(
    self.config,
    skill,
),
```

to every normalized enabled and disabled role. In `createModelControl`, append a
plain-text line:

```javascript
const effort = document.createElement("p");
effort.className = "latest-model";
effort.textContent = `Raisonnement : ${agent.configured_reasoning_effort || "hérité"}`;
wrapper.append(label, select, effort, latest);
```

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_models tests.test_config tests.test_cli tests.test_dashboard -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/pitcrew_models.py scripts/pitcrew_config.py scripts/pitcrew_dashboard.py dashboard/app.js bin/pitcrew-codex.sh tests/test_models.py tests/test_config.py tests/test_cli.py tests/test_dashboard.py
git commit -m "feat: configure reasoning effort per agent"
```

### Task 2: Register the scheduled architecture role

**Files:**
- Create: `skills/architecture-run/SKILL.md`
- Modify: `scripts/pitcrew_models.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `bin/pitcrew-schedule.py`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `tests/test_plugin_contract.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_schedule.py`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing registration and schedule tests**

Add `architecture-run` to the expected skill set and assert:

```python
self.assertEqual("gpt-5.6-sol", DEFAULT_MODELS["architecture-run"])
self.assertEqual("high", DEFAULT_REASONING_EFFORTS["architecture-run"])
```

In `tests/test_schedule.py`, require the role to be enabled and weekly:

```python
self.assertTrue(schedule["architecture-run"]["enabled"])
self.assertEqual(604800, schedule["architecture-run"]["interval_seconds"])
```

Update plist-count assertions by one and assert that the architecture plist uses
`architecture-run`, `getbill`, and `--scheduled`.

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_plugin_contract tests.test_models tests.test_schedule -v
```

Expected: failures because the role is not registered.

- [ ] **Step 3: Register the role**

Add:

```python
DEFAULT_MODELS["architecture-run"] = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORTS["architecture-run"] = "high"
```

Add `architecture-run` to `SKILLS` in `bin/pitcrew-codex.sh` and add this enabled
schedule entry:

```python
("architecture-run", 604800, True, ""),
```

Add to all shipped agent configurations:

```json
"architecture-run": {
  "model": "gpt-5.6-sol",
  "reasoning_effort": "high"
}
```

Add to the GetBill and example configuration roots:

```json
"architecture": {
  "interval_seconds": 604800
}
```

Validate `architecture.interval_seconds` as exactly `604800`. Keep the scheduler
entry fixed to the same value so the shipped weekly policy cannot drift between
configuration and `launchd`.

- [ ] **Step 4: Create the initial skill contract**

Create `skills/architecture-run/SKILL.md` with this complete outer contract:

```markdown
---
name: architecture-run
description: Use when scanning one configured repository area for high-confidence structural architecture problems and human-reviewed suggestions.
---

Read `references/CODEX-RUNTIME.md`, resolve exactly one configured project, and
read the repository's applicable `AGENTS.md` plus configured architecture
references. Perform one read-only pass. Never modify repository contents, create
remote artifacts, read secrets, or inspect ignored, untracked, uploaded,
generated, converted, dependency, or customer-data paths.

Use the configured repository checkout read-only. Preserve all unrelated local
changes and exclude modified or untracked files from findings.

Load `$STATE_DIR/architecture-state.json`. Use
`scripts/research_coverage.py select` with cell `architecture:$REPO_NAME` to select
one deterministic area. Inspect adjacent tracked interfaces only when needed to
prove a boundary violation.

Eligible categories are mixed responsibilities, framework or persistence
coupling, dependency direction or cycles, duplicated structural boundaries,
leaky interfaces, and missing abstractions demonstrated by change blast radius.
Do not record general hygiene, isolated hardening, security, documentation, or
test-coverage findings. Reject stylistic preferences and confidence below 80%.

Record at most three proposals in the configured proposal ledger. Each proposal
uses category `architecture`, source `architecture-run`, status `suggested`, a
stable id derived from repository, boundary, evidence paths, and selected tree
fingerprint, and includes severity, title, summary, tracked `file:line`
evidence, impact, and a bounded recommendation. Deduplicate by stable id.

After a completed scan, call `scripts/research_coverage.py record` against
`$STATE_DIR/architecture-state.json`. Do not advance coverage when configuration,
repository validation, or analysis fails. Return the runtime structured no-op
when no eligible proposal exists.
```

- [ ] **Step 5: Add skill safety contract tests**

Require the markers `architecture-state.json`, `architecture:$REPO_NAME`,
`category \`architecture\``, `at most three`, `confidence below 80%`, and the
excluded path classes. Assert forbidden strings `gh api`, `glab mr`,
`git checkout -- .`, and `graphify-out/converted` do not appear as readable
targets.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_plugin_contract tests.test_models tests.test_schedule tests.test_skill_contracts -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add skills/architecture-run/SKILL.md scripts/pitcrew_models.py bin/pitcrew-codex.sh bin/pitcrew-schedule.py profiles/getbill.json profiles/generic.json references/config.example.json tests/test_plugin_contract.py tests/test_models.py tests/test_schedule.py tests/test_skill_contracts.py
git commit -m "feat: register weekly architecture agent"
```

### Task 3: Make coverage rotation safe for architecture state

**Files:**
- Modify: `scripts/research_coverage.py`
- Modify: `tests/test_research_coverage.py`
- Modify: `skills/architecture-run/SKILL.md`

- [ ] **Step 1: Write failing permission and malformed-state tests**

Add tests proving that `update_cell_state` creates the parent with mode `0700`,
creates the state file with mode `0600`, and fails closed on malformed JSON:

```python
def test_state_write_is_private(self):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state" / "architecture-state.json"
        update_cell_state(
            {"cells": {}, "history": []},
            "architecture:getbill",
            ["src"],
            "src",
            0,
            "2026-07-26T10:00:00Z",
            path=path,
        )
        self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
```

Add a CLI test where `select` reads `{broken` and exits non-zero without
overwriting it.

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```bash
python3 -m unittest tests.test_research_coverage -v
```

Expected: permission assertions fail.

- [ ] **Step 3: Harden atomic state persistence**

Before `mkstemp`, set the state directory to `0700`; immediately set the
temporary descriptor to `0600`, flush and `fsync` before `os.replace`, and
`fsync` the parent directory:

```python
path.parent.mkdir(parents=True, exist_ok=True)
path.parent.chmod(0o700)
descriptor, temporary_name = tempfile.mkstemp(
    prefix=f".{path.name}.",
    dir=path.parent,
)
os.fchmod(descriptor, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary_name, path)
```

Keep malformed state as a hard CLI error. Do not auto-reset or overwrite it.
Update the module docstring to describe shared discovery-agent coverage.

- [ ] **Step 4: Clarify successful/no-op coverage advancement in the skill**

State explicitly that a valid scan with zero findings still records coverage,
while invalid configuration, unreadable tracked inputs, or interrupted analysis
does not.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_research_coverage tests.test_skill_contracts -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/research_coverage.py tests/test_research_coverage.py skills/architecture-run/SKILL.md
git commit -m "fix: harden architecture coverage state"
```

### Task 4: Queue approved architecture proposals through the manager

**Files:**
- Modify: `scripts/pitcrew_proposals.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `skills/manager-run/SKILL.md`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `tests/test_proposals.py`
- Modify: `tests/test_dashboard.py`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing proposal and dashboard tests**

Add an architecture proposal fixture and assert it validates. Add:

```python
def test_attach_tracker_is_idempotent(self):
    with tempfile.TemporaryDirectory() as temp:
        store = ProposalStore(Path(temp) / "proposals.json")
        store.append({
            "id": "architecture-boundary-1",
            "category": "architecture",
            "severity": "medium",
            "title": "Isoler la facturation",
            "summary": "Le contrôleur contient la politique de facturation.",
            "evidence": ["src/Controller/BillingController.php:42"],
            "recommendation": "Déplacer la politique dans un service de domaine.",
            "status": "suggested",
            "source": "architecture-run",
        })
        store.transition(
            "architecture-boundary-1",
            "approved",
            actor="dashboard",
        )
        first = store.attach_tracker(
            "architecture-boundary-1",
            {"iid": 123, "web_url": "https://gitlab.com/getbill1/getbill/-/issues/123"},
        )
        second = store.attach_tracker(
            "architecture-boundary-1",
            {"iid": 123, "web_url": "https://gitlab.com/getbill1/getbill/-/issues/123"},
        )
        self.assertEqual(first, second)
```

In `tests/test_dashboard.py`, approve an architecture proposal and assert the
proposal transitions locally while the mocked GitLab command runner receives no
issue-creation call. Retain the existing feature/security assertion that approval
does create an issue.

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_proposals tests.test_dashboard -v
```

Expected: architecture category is rejected and dashboard approval attempts a
GitLab issue.

- [ ] **Step 3: Extend the proposal store**

Add `"architecture"` to `CATEGORIES` and add:

```python
def attach_tracker(self, proposal_id: str, tracker: dict) -> dict:
    iid = tracker.get("iid")
    web_url = tracker.get("web_url")
    if (
        isinstance(iid, bool)
        or not isinstance(iid, int)
        or iid <= 0
        or not isinstance(web_url, str)
        or not web_url.startswith("https://")
    ):
        raise ProposalError("tracker metadata is invalid")
    with self._locked() as lock:
        try:
            records = self._read()
            for proposal in records:
                if proposal["id"] != proposal_id:
                    continue
                if proposal["category"] != "architecture":
                    raise ProposalError("tracker attachment is restricted")
                if proposal["status"] not in {"approved", "investigate"}:
                    raise ProposalError("proposal is not manager-eligible")
                existing = proposal.get("tracker")
                if existing is not None:
                    if existing != tracker:
                        raise ProposalError("proposal already has tracker metadata")
                    return proposal
                proposal["tracker"] = dict(tracker)
                self._write(records)
                return proposal
            raise ProposalError("proposal not found")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
```

Expose an `attach-tracker` CLI command that accepts ledger path, proposal id,
positive integer iid, and HTTPS URL, calls this method, and returns compact JSON.

- [ ] **Step 4: Keep architecture approval local**

In `DashboardService.decide_proposal`, branch before `_create_proposal_issue`:

```python
metadata = None
manager_queued = current.get("category") == "architecture"
if status in {"approved", "investigate"} and not manager_queued:
    issue = self._create_proposal_issue(
        current,
        investigate=status == "investigate",
    )
    metadata = {
        "tracker": {
            "iid": issue.get("iid"),
            "web_url": issue.get("web_url"),
        }
    }
```

Return `"manager_queued": manager_queued` so the dashboard can report the local
handoff accurately.

- [ ] **Step 5: Configure and document manager normalization**

Add an `architecture` manager source in the GetBill profile:

```json
{
  "name": "architecture",
  "format": "architecture-proposals-v1",
  "label": "pitcrew-source::architecture",
  "findings_json": "/Users/jo/.codex/pitcrew/getbill/proposals.json",
  "target_depth": 2,
  "investigate_wip": 1
}
```

For a generic project, let `manager-run` derive the same source from
`.proposals.ledger`, falling back to `$CONFIG_DIR/proposals.json`, with default
depths `2` and `1` when no explicit manager source exists. Extend `manager-run`
normalization with the exact rule:

```text
architecture-proposals-v1 — read only category=architecture records whose
status is approved or investigate and whose tracker field is absent. Use id as
finding-key, evidence as locations, recommendation as suggested_fix, and force
investigate route when status=investigate. After create/dedup, call the Pitcrew
proposal helper attach-tracker command. Do not mark manager state filed until
that helper succeeds.
```

Add skill-contract markers for the format and helper command.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_proposals tests.test_dashboard tests.test_skill_contracts tests.test_config -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/pitcrew_proposals.py scripts/pitcrew_dashboard.py skills/manager-run/SKILL.md profiles/getbill.json profiles/generic.json references/config.example.json tests/test_proposals.py tests/test_dashboard.py tests/test_skill_contracts.py tests/test_config.py
git commit -m "feat: route architecture proposals through manager"
```

### Task 5: Document and verify the architecture workflow

**Files:**
- Modify: `README.md`
- Modify: `references/TOPOLOGY.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `docs/CODEX.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write failing documentation contract tests**

Require these exact facts:

```python
self.assert_markers(
    "references/TOPOLOGY.md",
    "$pitcrew:architecture-run",
    "one rotating repository area",
    "human approval",
    "manager",
)
self.assert_markers(
    "references/SCHEDULED-TASKS.md",
    "architecture-run",
    "604800",
    "gpt-5.6-sol",
    "high",
)
```

- [ ] **Step 2: Run the documentation tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: missing architecture workflow markers.

- [ ] **Step 3: Update operator documentation**

Add the role to the crew tables and topology handoff:

```text
architecture -> local proposal -> dashboard approval -> manager -> tracker
```

Document the weekly schedule, manual Trigger availability, Sol/high policy,
separate architecture coverage state, three-proposal cap, and absence of direct
repository or GitLab writes.

- [ ] **Step 4: Run deterministic verification**

Run:

```bash
python3 -m unittest tests.test_docs tests.test_plugin_contract tests.test_models tests.test_config tests.test_cli tests.test_schedule tests.test_research_coverage tests.test_proposals tests.test_skill_contracts tests.test_dashboard -v
```

Expected: all selected tests pass.

Run:

```bash
bash tests/run.sh
```

Expected: complete suite passes.

- [ ] **Step 5: Commit**

```bash
git add README.md references/TOPOLOGY.md references/SCHEDULED-TASKS.md docs/CODEX.md tests/test_docs.py
git commit -m "docs: describe architecture agent workflow"
```
