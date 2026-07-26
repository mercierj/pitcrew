# Architecture Dirty-Worktree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `architecture-run` complete a safe scan when the target checkout contains unrelated work in progress.

**Architecture:** The role keeps its read-only safety boundary. It skips modified, untracked, ignored, generated, dependency, secret, and customer-data paths as evidence, but it does not treat those paths as a failure for the complete pass. Curated references follow the same rule: use them only when clean and tracked; otherwise continue without them.

**Tech Stack:** Markdown skill contract, Python `unittest` contract checks.

---

### Task 1: Specify the dirty-worktree contract

**Files:**
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write the failing test**

```python
self.assertIn(
    "A dirty or missing optional reference must not abort the pass",
    text,
)
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_secondary_roles_are_provider_neutral_and_getbill_safe -v`

Expected: failure because the existing architecture contract does not define this behavior.

### Task 2: Continue safely around work in progress

**Files:**
- Modify: `skills/architecture-run/SKILL.md`

- [ ] **Step 1: Add the minimum contract language**

```markdown
Skip every modified or ineligible path; do not abort a pass because unrelated WIP exists.
A dirty or missing optional reference must not abort the pass: continue with eligible tracked source inputs.
```

- [ ] **Step 2: Run the targeted test and verify it passes**

Run: `python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_secondary_roles_are_provider_neutral_and_getbill_safe -v`

Expected: `OK`.

### Task 3: Verify the real workflow

**Files:**
- No repository code changes.

- [ ] **Step 1: Run the complete contract suite**

Run: `python3 -m unittest tests.test_skill_contracts -v`

Expected: `OK`.

- [ ] **Step 2: Trigger one read-only scan**

Run: `./bin/pitcrew-codex.sh architecture-run getbill`

Expected: a structured completed result, with a proposal or a valid zero-finding outcome.
