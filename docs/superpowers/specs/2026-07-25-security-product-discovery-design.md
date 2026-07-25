# Security and Product Discovery Design

## Goal

Add only two high-value roles to Pitcrew: a dedicated security scanner and a
product-discovery scanner whose proposals are reviewed in the local dashboard
before becoming tracker work.

## Scope

`security-run` records one high-confidence security finding per bounded pass.
`product-discovery-run` records one evidence-backed product opportunity per
bounded pass. Existing research, investigation, QA, review, and validation
roles remain responsible for their existing surfaces; no separate agents are
created for performance, dependencies, UX, data quality, or documentation.

## Proposal lifecycle

Local proposals use `suggested`, `approved`, and `dismissed` states. The
dashboard can approve, reject, or route a proposal to investigation. Approval
creates one GitLab issue with the approved content and the normal `todo` state;
rejection requires a reason. No remote issue is created before approval.

## Data flow

Each role writes a JSON proposal to a project-scoped ledger. The dashboard reads
the ledger, validates the proposal shape, and exposes only redacted public
fields. Dashboard mutations use the existing session token and an atomic ledger
update. GitLab issue creation is provider-gated and fails closed.

## Security rules

Security findings include severity, evidence, affected paths, impact, and a fix
sketch. Critical and high findings remain human-gated. Neither role reads
secrets, writes repository files, or changes remote state during discovery.

## Dashboard

Add a pending-proposals panel with category/severity filters, detail view, and
Approve, Reject, and Investigate actions. Approved proposals show their created
issue reference and lifecycle state. The existing blocked-decision panel stays
separate because it represents implementation blockers, not new work.

## Verification

Tests cover ledger validation, deduplication, approve/reject transitions,
rejection reasons, no GitLab call before approval, issue creation after
approval, API session protection, and the rendered dashboard controls.
