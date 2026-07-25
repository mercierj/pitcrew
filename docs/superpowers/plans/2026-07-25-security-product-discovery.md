# Security and Product Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `security-run`, `product-discovery-run`, and dashboard-gated proposal approval without fragmenting existing research responsibilities.

**Architecture:** Discovery roles write validated local proposal records. The dashboard owns human decisions and, on approval, creates one tracker issue using the configured GitLab provider. Existing lifecycle agents consume the resulting normal `todo` issue.

**Tech Stack:** Python 3, JSON ledgers, existing GitLab CLI adapter, vanilla JavaScript dashboard, unittest.

---

### Task 1: Add proposal schema and ledger helpers

**Files:**
- Create: `scripts/pitcrew_proposals.py`
- Test: `tests/test_proposals.py`

- [ ] Write tests for valid security/product records, duplicate keys, atomic append, and invalid state transitions.
- [ ] Implement strict field validation, redaction-safe summaries, file locking, append, list, and transition helpers.
- [ ] Run `python3 -m unittest tests/test_proposals.py`.

### Task 2: Add the two skill contracts

**Files:**
- Create: `skills/security-run/SKILL.md`
- Create: `skills/product-discovery-run/SKILL.md`
- Modify: `scripts/pitcrew_models.py`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `README.md`
- Modify: `references/TOPOLOGY.md`
- Test: `tests/test_skill_contracts.py`, `tests/test_config.py`

- [ ] Add one-pass read-only contracts that write proposals and never create tracker work.
- [ ] Register both roles with model defaults and proposal-ledger configuration.
- [ ] Update topology and role tables.
- [ ] Run skill/config tests.

### Task 3: Add dashboard proposal APIs

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `bin/pitcrew-dashboard`
- Test: `tests/test_dashboard.py`

- [ ] Add list and decision methods with session-gated API routes.
- [ ] Approve by creating one GitLab issue with category and source labels, then transition the local record.
- [ ] Reject only with a non-empty reason; investigate transitions to the existing blocked-decision path.
- [ ] Fail closed when GitLab is unavailable and never create a remote issue for rejected/suggested records.
- [ ] Run dashboard API tests.

### Task 4: Add dashboard UI

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Test: `tests/test_dashboard.py`

- [ ] Render pending proposals with category, severity, evidence, and detail fields.
- [ ] Add accessible approve/reject/investigate controls and rejection-reason form.
- [ ] Refresh proposal state after every decision and preserve the existing blocker panel.
- [ ] Run frontend contract tests.

### Task 5: Verify the complete suite

- [ ] Run `bash tests/run.sh`.
- [ ] Run `python3 bin/pitcrew-codex.sh security-run getbill --dry-run` and the equivalent product-discovery command.
- [ ] Inspect `git diff --check` and ensure unrelated pre-existing changes remain untouched.
