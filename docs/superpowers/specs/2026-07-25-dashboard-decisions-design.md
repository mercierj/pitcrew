# Dashboard Decisions Design

**Status:** Approved in conversation

## Goal

Expose pending `unblock` questions in the local Pitcrew dashboard so the operator can read the ticket context, choose one of the configured answers, and have `unblock` apply that answer automatically.

## Design

The dashboard adds a priority decision banner above the live-agent section. The server reads the configured project's `unblock-state.json`, resolves the pending ticket and its notes through the configured GitLab project, and returns a bounded decision payload. The browser renders the question, choices, findings, ticket link, and a single-submit action.

Submitting a decision atomically records `answer`, optional `notes`, and `answered_at` in `pending_question`, then triggers the configured `unblock` runner. The dashboard never mutates GitLab directly. `unblock` is updated to consume an answered pending question instead of re-emitting it.

## Safety and failure behavior

- Only the configured GitLab project and the current pending ticket are read.
- The answer must exactly match one configured choice.
- The dashboard session token protects submission.
- Duplicate submissions are rejected once the question is answered.
- GitLab or runner failures leave the pending answer persisted for retry.
- Findings and descriptions are bounded before returning to the browser.

## Verification

Add service/API tests for pending decision loading, exact-choice validation, answer persistence, and unblock triggering. Add DOM contract tests for the banner, context, choices, polling, and authenticated submission.
