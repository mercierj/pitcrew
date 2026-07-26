---
name: bugfixer-run
description: Use when reproducing and fixing one eligible configured bug issue through a reviewed change.
---

# Evidence-first bug fixer

Read `references/CODEX-RUNTIME.md`, `references/PROVIDERS.md`, the selected
provider reference, `references/CHANGE-DELIVERY.md`,
`references/DIRECTED-TARGET.md`, repository `AGENTS.md`, project lessons, and
topology before lookup or mutation. Perform exactly one bounded pass.

Resolve the validated tracker, forge, repository, state, Slack, and safety
values used by `implementer-run`, plus:

```text
BUGFIXER_SENSITIVE_LABELS=<bugfixer.sensitive_labels>
BUGFIXER_RISKY_RE=<bugfixer.risky_categories_regex>
SENSITIVE_APPROVED_LABEL=<bugfixer.sensitive_approved_label>
```

Before selection, list configured provider labels and require exact presence of
the agent, bug, investigate, approval, and five lifecycle labels. A missing
label returns a structured no-op; this skill never creates labels. Keep all
prod/preprod, Graphify, staging, reference, secret, database, and destructive
Git gates from the repository instructions.

Provider-specific syntax belongs only in the selected provider reference. Use
the provider-neutral operations from `references/CHANGE-DELIVERY.md`:
`LIST_ELIGIBLE_WORK`, `INSPECT_TRACKER_ITEM`, `CLAIM_WORK`,
`FIND_OR_CREATE_CHANGE`, `READ_CHANGE_REVIEWS`, `READ_CHANGE_CHECKS`,
`MERGE_CHANGE`, and `CLOSE_LIFECYCLE`.

## Selection and sensitive routing

**STEP 0 — Reconcile** an orphan only when its canonical ticket has no active
run and no existing open change. Otherwise preserve it for its owner.

**STEP A — Review continuation.** Service `$STATE_REVIEW` issues carrying both
`$AGENT_LABEL` and `$BUG_LABEL`. Apply only current-head review, validation,
human-go, and CI signals.

**STEP B — Select one candidate.** Query
`label="$AGENT_LABEL", state="$STATE_TODO"`, then keep only issues carrying
`$BUG_LABEL`, without `$INVESTIGATE_LABEL`, and without open blockers. Sort
quick-win, priority, age. `bugfixer-run` exclusively owns this bug queue;
never claim, reset, repair, merge, or close a non-bug ticket.

**STEP C — Assess risk.** Read the full issue and comments. Match exact
sensitive labels and `$BUGFIXER_RISKY_RE`, but perform no tracker or repository
mutation yet. Hold the routing decision in memory.

**STEP D — Bind before mutation.** Call coordinator `bind-target` with the
canonical issue URL before the sensitive-routing comment, label transition,
claim, or checkout mutation. On conflict, select another candidate or return a
structured no-op.

**STEP E — Route or claim.** A valid sensitive approval requires all three:

1. `SENSITIVE_APPROVED_LABEL` exists.
2. An investigation findings marker exists.
3. The latest `<!-- pitcrew:bugfix-sensitive-approved:v1 ticket=<id> -->`
   names the configured human and scoped constraints.

If sensitive approval is absent, preserve categorization, remove `$AGENT_LABEL`,
add `$INVESTIGATE_LABEL`, keep `$STATE_TODO`, post one marked routing comment,
finalize the coordinated run, and stop without repository writes. Otherwise
claim `$STATE_PROCESSING` and create one isolated ticket checkout.

## Evidence-first change delivery

**STEP F — Reproduce.** Add the smallest automated regression test or
deterministic local flow. Run it before modifying production code. A valid red
reproduction fails on the reported behavior, not setup, an unrelated assertion,
or an unavailable external service. Post a redacted command/assertion/revision
evidence comment:

```markdown
<!-- pitcrew:bugfix:red:v1 -->
Command: `<redacted command>`
Observed: <failing assertion or result>
Expected: <expected result>
Revision: `<sha>`
```

If no valid red reproduction exists during the bounded run: discard uncommitted
isolated-checkout edits, create no change, move to `$STATE_BLOCKED`, post
attempted commands/results/hypotheses using `<!-- pitcrew:bugfix:blocked:v1 -->`,
finalize the run, and stop.

**STEP G — Fix.** Apply the smallest production change. The same reproduction must turn green.
Run focused tests and repository-required validation.
Architecture ambiguity, unavailable services, insufficient confidence, or broad
blast radius routes to `$STATE_BLOCKED` with:

```markdown
<!-- pitcrew:bugfix:green:v1 -->
Command: `<same redacted command>`
Result: passed
Validation: `<redacted focused commands>`
```

**STEP H — Open or reuse one change.** Use `FIND_OR_CREATE_CHANGE` to open or
reuse one idempotent PR/MR. Include issue, root cause, red evidence, fix, green
evidence, validation, and risks. Move to `$STATE_REVIEW` and post:

```markdown
<!-- pitcrew:bugfix:ready:v1 sha=<head-sha> -->
Change: <PR/MR URL>
Root cause: <one line>
```

The reproduction test remains in the opened change. Do not create the
implementer's deferred QA-coverage ticket for a bug whose regression test is
already part of the fix.

Use this blocked marker for every bounded failure:

```markdown
<!-- pitcrew:bugfix:blocked:v1 -->
Reason: <one line>
Attempts: <redacted commands and outcomes>
```

## Review, merge, and closeout

Change requests permit two fix attempts. Each pushes a new head and reruns the
same reproduction plus required validation.

Merge only when the reviewer signed off on the current head SHA, the validator passed the current head SHA,
the configured human posted actionable human `go` after the first ready marker,
and all required CI checks are green.

Immediately re-fetch expected issue state, source/target branches, open change,
discussions, reviews, validation marker, checks, and head SHA. Then squash
merge, delete the confirmed source branch, call `CLOSE_LIFECYCLE`, verify done
plus closed, post the bounded summary, and finalize the coordinated run.

Any provider mismatch, lost eligibility, stale signal, uncertain result, or
mutation failure fails closed. Preserve a useful open change for human pickup;
otherwise return a structured no-op.
