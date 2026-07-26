# Shared change delivery contract

This provider-neutral contract is normative for every acting role that claims an
issue, delivers a change, and closes its lifecycle. Resolve each operation below
through the selected provider reference; do not put provider command syntax in a
role skill.

## Bind target before mutation

For a coordinated run, bind the canonical issue target to `PITCREW_RUN_ID`
before changing tracker state, labels, comments, repository files, branches, or
changes. A binding conflict performs no mutation and selects another eligible
item or returns a structured no-op.

## Claim

Re-fetch the issue. Verify provider binding, open state, expected logical state,
required routing labels, unfinished blockers, and absence of another active
owner. Preserve non-state labels and replace only the logical state label.
Re-read and verify the Expected remote state.

## Change identity

Derive one stable operation marker and ticket branch. Lookup before create by
provider, repository, ticket, and exact source branch. After an Uncertain result,
re-read before retrying. Never create a second open change for the same
operation.

## Review continuation

Evaluate only the Current head SHA. Reviewer signed off, Validator passed,
Human go, and Required CI checks are four independent signals. A verdict or
validation on an older SHA is stale. Change requests permit at most Two fix attempts,
each followed by focused verification.

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

## Provider-neutral operations

Use the selected provider reference to implement these abstract capabilities:

```text
LIST_ELIGIBLE_WORK(filters) -> configured tracker items
INSPECT_TRACKER_ITEM(ticket) -> current tracker item, labels, blockers, owner
CLAIM_WORK(ticket, expected_state, state, marker) -> verified expected remote state
FIND_OR_CREATE_CHANGE(ticket, repository, source_branch, marker) -> one open change
READ_CHANGE_REVIEWS(change, head_sha) -> reviews bound to the current head
READ_CHANGE_CHECKS(change, head_sha) -> required checks for the current head
MERGE_CHANGE(change, expected_head_sha) -> merged change or fail-closed result
CLOSE_LIFECYCLE(ticket, change, done_state, marker) -> done label, comment, close, re-read
```

Every operation is idempotency-aware: on an uncertain response, inspect the
provider state before retrying or creating another resource.
