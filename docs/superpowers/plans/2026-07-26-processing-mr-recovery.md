# Processing MR Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a completed implementation with an open merge request from remaining indefinitely in `agent-processing`.

**Architecture:** The implementation workflow is the source of truth for tracker lifecycle transitions. Its recovery step will recognize an open agent-authored change as ready for review, move the ticket to `agent-review`, verify the change, and leave review/validation to their existing roles. The dashboard continues to show an agent as running only while it has a live process.

**Tech Stack:** Markdown workflow contract; Python `unittest` contract tests.

---

### Task 1: Specify and implement recovery of a stale processing ticket with an open change

**Files:**

- Modify: `tests/test_skill_contracts.py`
- Modify: `skills/implementer-run/SKILL.md`

- [ ] **Step 1: Write the failing contract test**

```python
def test_implementer_recovers_processing_ticket_with_open_change_to_review(self):
    implementer = (ROOT / "skills/implementer-run/SKILL.md").read_text(
        encoding="utf-8"
    )
    self.assertIn("Open change exists", implementer)
    self.assertIn("state=\"$STATE_REVIEW_ID\"", implementer)
    self.assertIn("Recovered stale $STATE_PROCESSING state", implementer)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_implementer_recovers_processing_ticket_with_open_change_to_review -v`

Expected: FAIL because the recovery rule for an open change still leaves the ticket in processing.

- [ ] **Step 3: Update the workflow contract**

Replace the `STEP 0` open-change branch so it moves the ticket to `$STATE_REVIEW_ID`, immediately verifies the state, adds an audit comment, logs a `recovered-review` event, and fails closed to `$STATE_BLOCKED_ID` if verification fails. Keep no-change recovery to `$STATE_TODO` unchanged.

- [ ] **Step 4: Run the focused contract test**

Run: `python3 -m unittest tests.test_skill_contracts.ReferenceContractTest.test_implementer_recovers_processing_ticket_with_open_change_to_review -v`

Expected: PASS.

- [ ] **Step 5: Run the full contract suite**

Run: `python3 -m unittest tests.test_skill_contracts -v`

Expected: PASS with no failures.
