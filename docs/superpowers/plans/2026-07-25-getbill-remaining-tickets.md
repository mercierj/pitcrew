# GetBill Remaining Tickets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the configured Pitcrew implementer able to process GetBill tickets #4 and #5, then run one bounded implementation pass for each ticket while leaving security tickets #1 and #2 awaiting human scope decisions.

**Architecture:** Keep the existing one-ticket-per-pass and fail-closed workflow. Grant the implementer only the repository Git metadata access needed to create its temporary worktree, then invoke the existing implementer skill separately for #4 and #5. Verify each result through GitLab issue state, merge-request existence, commit contents, and focused test/build output.

**Tech Stack:** Pitcrew Codex skills, GitLab CLI/API, Git, GetBill Symfony/PHP test and build commands.

---

### Task 1: Verify and enable the implementer repository boundary

**Files:**
- Read: `/Users/jo/Prog/getbill/AGENTS.md`
- Read: `/Users/jo/Prog/getbill/.git`
- Read/write boundary: `/Users/jo/Prog/getbill/.git/FETCH_HEAD` and the temporary worktree area

- [ ] **Step 1: Verify the current Git metadata failure**

Run:

```bash
git -C /Users/jo/Prog/getbill fetch origin develop --dry-run
```

Expected: the command either succeeds or reports the exact `.git` write restriction that blocked tickets #3 and #4.

- [ ] **Step 2: Confirm unrelated working-tree changes are preserved**

Run:

```bash
git -C /Users/jo/Prog/getbill status --short --branch
```

Record the existing changes; do not reset, clean, stash, or overwrite them.

- [ ] **Step 3: Grant only the required write access**

Run the implementer with the approved elevated filesystem boundary, targeting only the configured GetBill repository Git metadata and its temporary worktree directory. Do not change application code or deploy anything during this step.

- [ ] **Step 4: Re-run the metadata check**

Run:

```bash
git -C /Users/jo/Prog/getbill fetch origin develop --dry-run
```

Expected: no `.git/FETCH_HEAD` permission error.

### Task 2: Implement ticket #4

**Files:**
- Modify: only files selected by the implementer for `getbill1/getbill#4`
- Test: focused controller test selected by the ticket scope

- [ ] **Step 1: Run exactly one bounded implementer pass for ticket #4**

Invoke `$pitcrew:implementer-run` for project `getbill`. The configured eligibility order must select #4 after #3 is blocked; do not manually edit the issue body or labels to bypass routing.

- [ ] **Step 2: Verify the resulting lifecycle**

Query GitLab for issue #4 and its open merge requests. Expected successful outcome: a feature branch, an open MR targeting `develop`, a commit, focused verification results, and issue state `pitcrew-state::review` or a factual `pitcrew-state::blocked` result if a new blocker is encountered.

- [ ] **Step 3: Preserve the human merge gate**

Do not merge the MR, deploy it, or close issue #4. If implementation succeeds, leave it awaiting review/validation.

### Task 3: Implement ticket #5

**Files:**
- Modify: only files selected by the implementer for `getbill1/getbill#5`
- Test: focused controller test selected by the ticket scope

- [ ] **Step 1: Run exactly one bounded implementer pass for ticket #5**

Invoke `$pitcrew:implementer-run` again only after Task 2 has returned. The configured eligibility order must select #5; do not process multiple tickets in one pass.

- [ ] **Step 2: Verify the resulting lifecycle**

Query GitLab for issue #5 and its open merge requests. Expected successful outcome: a feature branch, an open MR targeting `develop`, a commit, focused verification results, and issue state `pitcrew-state::review` or a factual `pitcrew-state::blocked` result if a new blocker is encountered.

- [ ] **Step 3: Preserve the human merge gate**

Do not merge the MR, deploy it, or close issue #5. If implementation succeeds, leave it awaiting review/validation.

### Task 4: Confirm security tickets remain intentionally gated

**Files:**
- Read: GitLab issues #1 and #2
- Read: `/Users/jo/.codex/pitcrew/getbill/state/investigate-state.json`

- [ ] **Step 1: Verify findings remain available**

Confirm both issues retain their investigation findings and remain blocked pending the operator’s scope decision.

- [ ] **Step 2: Do not implement #1 or #2 in this pass**

Do not move either ticket to `todo`, create a branch, or modify application code for either security item without an explicit scope choice.

### Task 5: Final verification

**Files:**
- Read: `/Users/jo/.codex/pitcrew/getbill/logs/implementer-run.last.txt`
- Read: GitLab issue and MR records for #4 and #5

- [ ] **Step 1: Check all five ticket states**

Expected: #1/#2 blocked for human decision, #3 blocked with MR !9 open, and #4/#5 either review-ready with MRs or blocked with a precise reason.

- [ ] **Step 2: Check repository safety**

Run:

```bash
git -C /Users/jo/Prog/getbill status --short --branch
```

Expected: pre-existing unrelated changes remain untouched and no default-branch or deployment mutation occurred.
