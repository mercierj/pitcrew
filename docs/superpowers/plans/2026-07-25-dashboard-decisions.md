# Dashboard Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the local dashboard display pending `unblock` decisions with GitLab context and submit answers that automatically trigger `unblock`.

**Architecture:** Add a server-side decision adapter backed by `unblock-state.json`, configured GitLab issue/notes reads, and an atomic answer write. Extend the local action API and render a priority decision banner in the existing dependency-free browser UI. Update the `unblock` contract to consume the persisted answer.

**Tech Stack:** Python standard library, GitLab `glab api`, HTML/CSS/vanilla JavaScript, unittest.

---

### Task 1: Add failing decision service tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Test: pending decision read, answer validation, state persistence, and unblock trigger.

- [ ] Add a fixture with `unblock-state.json` containing a blocked `pending_question`, a fake GitLab issue page, and fake notes.
- [ ] Assert `service.decisions()` returns the question, choices, ticket title, URL, description, and findings notes.
- [ ] Assert `service.submit_decision()` rejects an answer not in `choices`.
- [ ] Assert a valid answer writes `status=answered`, `answer`, `notes`, and `answered_at`, then triggers `unblock` even though it is not a scheduled role.
- [ ] Run `python3 -m unittest tests.test_dashboard.DashboardServiceTest -v`; confirm the new tests fail because the API does not exist.

### Task 2: Implement the server decision adapter

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`

- [ ] Add safe helpers to read the pending question, parse `getbill1/getbill#<iid>`, fetch the configured GitLab issue and notes, and cap returned text.
- [ ] Add `decisions()` returning an empty list when no blocked question exists.
- [ ] Add `submit_decision(ticket_id, answer, notes)` with exact-choice validation, atomic JSON replacement, and one `unblock` trigger.
- [ ] Keep the answer persisted if the trigger fails so the next retry can resume.
- [ ] Run the Task 1 service tests and confirm they pass.

### Task 3: Expose authenticated decision endpoints

**Files:**
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] Add `GET /api/decisions` to return the service payload.
- [ ] Add authenticated `POST /api/actions` payload `{action:"answer-decision",ticket_id,answer,notes}` with strict field validation.
- [ ] Add HTTP tests for missing session, malformed payload, invalid choice, and accepted answer.
- [ ] Run the focused HTTP tests with local binding permission.

### Task 4: Add the priority decision banner

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] Add a semantic `#decision-banner` above live agents, hidden when no decision is pending.
- [ ] Render ticket link, question, findings/context, and one button per configured choice using text DOM APIs only.
- [ ] Poll `/api/decisions` with the existing 10-second refresh and submit through the session-authenticated action endpoint.
- [ ] Disable the decision controls while submitting, show success/error state, and refresh the dashboard after acceptance.
- [ ] Add asset contract assertions for the region, renderer, polling, and safe DOM APIs.

### Task 5: Teach unblock to consume UI answers

**Files:**
- Modify: `skills/unblock/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] Document `pending_question.status=answered` with exact `answer`, optional `notes`, and `answered_at`.
- [ ] Require `unblock` to validate the answer against stored choices, skip re-asking, and continue at STEP 7.
- [ ] Preserve the current interactive-thread behavior for unanswered questions.
- [ ] Add contract assertions for the answered path.

### Task 6: Verify and hand off

**Files:**
- No new files.

- [ ] Run `python3 -m unittest tests.test_cli tests.test_dashboard tests.test_skill_contracts -v`.
- [ ] Run `git diff --check`.
- [ ] Verify the dashboard payload contains no session token and no unbounded transcript.
