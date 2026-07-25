# Stale-sweep GitLab Lifecycle Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed `stale-sweep` reconcile open blocked/done GitLab issues when their related merge request has already been merged.

**Architecture:** Keep reconciliation in the existing skill contract. Extend its candidate states, define GitLab's distinct merged/closed semantics in the provider reference, and make successful reconciliation use the provider's atomic close-lifecycle operation with an idempotent note and post-write verification.

**Tech Stack:** Markdown skill contracts, GitLab REST provider contract, Python `unittest`, Codex plugin validation and local marketplace reinstall.

---

### Task 1: Add failing lifecycle contract tests

**Files:**
- Modify: `tests/test_skill_contracts.py`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write the failing test**

Add this test to `ReferenceContractTest` while preserving the existing uncommitted
`/-/work_items/<number>` assertion:

```python
def test_stale_sweep_repairs_gitlab_merged_lifecycle_drift(self):
    stale = (ROOT / "skills/stale-sweep/SKILL.md").read_text(encoding="utf-8")
    gitlab = (ROOT / "references/providers/gitlab.md").read_text(
        encoding="utf-8"
    )

    for state in (
        "STATE_REVIEW",
        "STATE_PROCESSING",
        "STATE_TODO",
        "STATE_BLOCKED",
        "STATE_DONE",
    ):
        self.assertIn(
            f'LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="${state}"',
            stale,
        )

    self.assertIn("built-in lifecycle is open", stale)
    self.assertIn("state=merged", stale)
    self.assertIn("state=closed", stale)
    self.assertIn("`state=merged` and non-null `merged_at`", stale)
    self.assertIn("`closed_at` may be null", stale)
    self.assertIn("CLOSE_LIFECYCLE", stale)
    self.assertIn("pitcrew:stale-sweep:done:", stale)
    self.assertIn("verify both", stale)
    self.assertIn("built-in issue state is closed", stale)
    self.assertIn("`$STATE_DONE` label is present", stale)

    self.assertIn("GitLab merge request states are mutually distinct", gitlab)
    self.assertIn("`state=merged`", gitlab)
    self.assertIn("`closed_at` is normally null", gitlab)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_stale_sweep_repairs_gitlab_merged_lifecycle_drift \
  -v
```

Expected: `FAIL` because `STATE_BLOCKED`, `STATE_DONE`, explicit GitLab merged
semantics, and `CLOSE_LIFECYCLE` are missing from the stale-sweep contract.

- [ ] **Step 3: Commit the test only**

Do not commit yet: keep the failing test visible through the GREEN phase so the
implementation and test land atomically.

### Task 2: Correct GitLab and stale-sweep lifecycle contracts

**Files:**
- Modify: `references/providers/gitlab.md`
- Modify: `skills/stale-sweep/SKILL.md`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Document GitLab terminal state semantics**

Add this rule under merge-request listing in
`references/providers/gitlab.md`:

```markdown
GitLab merge request states are mutually distinct: `state=merged` is the
terminal merged signal and `closed_at` is normally null for that state.
`state=closed` selects closed-without-merge changes and does not include merged
changes. Reconciliation must query `state=merged` and `state=closed`
separately; never require `closed_at` for a merged change.
```

- [ ] **Step 2: Expand stale-sweep candidates**

Add open-only queries for both missing lifecycle labels:

```text
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_BLOCKED",    limit=100)
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_DONE",       limit=100)
```

State explicitly that every query is limited to tracker items whose built-in
lifecycle is open. `STATE_DONE` candidates must still have merged-change
evidence before closure.

- [ ] **Step 3: Correct merged/closed classification**

Require the selected provider to search terminal merged and
closed-without-merge changes separately. Define GitLab merged evidence as
`state=merged` plus non-null `merged_at`; allow null `closed_at`.

Map merged changes to:

```text
CLOSE_LIFECYCLE(
  id="<TICKET-id>",
  change="<change-url>",
  marker="<!-- pitcrew:stale-sweep:done:<ticket-id>:change:<change-id> -->"
)
```

The operation must preserve non-state labels, apply `$STATE_DONE`, post the
idempotent audit note, close the issue, then re-read and verify both that the
built-in issue state is closed and that the `$STATE_DONE` label is present.

- [ ] **Step 4: Run focused test and verify GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_stale_sweep_repairs_gitlab_merged_lifecycle_drift \
  -v
```

Expected: `OK` with one passing test.

- [ ] **Step 5: Run all skill contract tests**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the lifecycle fix**

Stage only:

```bash
git add tests/test_skill_contracts.py references/providers/gitlab.md skills/stale-sweep/SKILL.md
git commit -m "fix: reconcile merged GitLab ticket lifecycle"
```

### Task 3: Validate and reinstall the plugin

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Verify: plugin root and installed cache

- [ ] **Step 1: Run repository tests**

Run:

```bash
./tests/run.sh
```

Expected: complete suite passes.

- [ ] **Step 2: Validate the skill and plugin**

Run the skill quick validator against `skills/stale-sweep`, then validate the
plugin root:

```bash
python3 /Users/jo/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/stale-sweep
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: both validators succeed.

- [ ] **Step 3: Update the plugin cachebuster**

Run:

```bash
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py .
```

Expected: `.codex-plugin/plugin.json` keeps base version `0.2.0` and receives
one fresh `+codex.<timestamp>` suffix.

- [ ] **Step 4: Commit the cachebuster**

```bash
git add .codex-plugin/plugin.json
git commit -m "chore: refresh pitcrew plugin cache"
```

- [ ] **Step 5: Reinstall from the configured local marketplace**

Read the marketplace name with the plugin-creator helper, confirm it points to
this repository, and run:

```bash
codex plugin add pitcrew@<confirmed-local-marketplace>
```

Expected: Codex installs the new cachebuster version from
`/Users/jo/Prog/pitcrew`.

- [ ] **Step 6: Verify installed content**

Compare the source and installed `skills/stale-sweep/SKILL.md` hashes and
manifest versions. Expected: hashes and versions match.
