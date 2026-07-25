# GitHub/GitLab bugfixer agent

## Context

Pitcrew's `implementer-run` can already select configured tracker work, create a
change on GitHub or GitLab, and participate in the review-to-merge lifecycle. It
does not provide a bug-only queue or require a failing reproduction before code
changes. GitLab Issues are documented as a tracker provider, while native GitHub
Issues still need a complete provider contract.

The new `bugfixer-run` role gives reported bugs a dedicated, evidence-first path
without allowing every issue in a repository to trigger code changes.

## Goals

- Consume one bounded GitHub or GitLab issue carrying the configured logical
  labels `agent`, `bug`, and `todo`.
- Keep the bug queue exclusive to `bugfixer-run`.
- Require an automated failing reproduction before modifying production code.
- Open a pull request or merge request containing the minimal fix and regression
  coverage.
- Preserve the existing independent review, validation, human `go`, and green-CI
  gates before merge.
- Route sensitive bugs to read-only investigation instead of fixing them
  unattended.
- Expose the role and ticket lifecycle in the scheduler and dashboard.

## Non-goals

- Watching every issue carrying only a generic `bug` label.
- Acting on issues from an unconfigured host, owner, group, or repository.
- Supporting Linear as the bugfixer's tracker in the first version.
- Fixing security-, authentication-, payment-, privacy-, or secret-related bugs
  before `investigate-run` and the human unblock workflow have scoped them.
- Merging production-code changes without the configured human's `go`.
- Replacing `reviewer-run`, `validator-run`, or `investigate-run`.

## Chosen approach

Add a distinct `bugfixer-run` skill backed by a provider-neutral delivery
contract shared with `implementer-run`.

This keeps role ownership visible in scheduling, configuration, history, usage,
and the dashboard. A parameterized implementer mode was rejected because it
would make the already broad implementer contract harder to reason about. A
router that handed bugs back to the implementer was rejected because it would
split the evidence-first guarantee across two runs and make ownership unclear.

## Architecture

```text
GitHub/GitLab issue
labels: agent + bug + todo
           |
           v
     bugfixer-run
           |
    risk preflight
      /         \
 sensitive    ordinary
    |            |
investigate   reproduce red
                 |
            minimal fix
                 |
           reproduce green
                 |
               PR/MR
                 |
       reviewer + validator
                 |
          human go + CI
                 |
      bugfixer merges/closes
```

`bugfixer-run` owns both new bug selection and the continuation of bug changes
already in `review`. `implementer-run` must exclude every issue carrying the
configured `bug` label at all lifecycle stages, including merge handling. This
exclusive ownership prevents two roles from racing on the same bug.

The durable ticket-run coordinator is an implementation prerequisite for this
role. The current runner still uses a coarse per-role lock, so this feature also
finishes the coordinator integration described in the existing ticket-run
coordinator design. Its lock key is the configured provider, project, and issue
identifier, so manual, dashboard, and scheduled launches converge on one ticket
execution. The acting role also re-fetches the remote issue and expected state
immediately before each claim or terminal mutation.

## Eligibility and routing

An issue is eligible only when all of these conditions are true:

1. Its tracker is the configured `github` or `gitlab` provider.
2. Its host, owner or group, and repository match the validated project binding.
3. It is open and carries the configured `agent`, `bug`, and `todo` labels.
4. It does not carry the configured `investigate` label.
5. It is not blocked by unfinished tracker work.

A directed issue URL uses the same checks. Passing a URL never bypasses label,
state, risk, identity, repository, review, or merge gates.

Before claiming ordinary work, `bugfixer-run` reads the full issue and comments
and classifies sensitivity using exact configured `sensitive_labels` plus the
configured risk regex. A sensitive match causes one idempotent routing mutation:

- preserve all categorization labels;
- remove the `agent` label;
- add the `investigate` label;
- keep the issue in `todo`;
- post an audit comment naming the matched label or risk category.

`investigate-run` can then select the issue using its existing `investigate +
todo` contract. The bugfixer performs no repository write for that issue.

After investigation, `unblock` owns the explicit human decision:

- **authorize a bounded bugfix:** preserve `bug`, remove `investigate`, restore
  `agent`, add the configured `sensitive_approved` label, record the human
  approval and scoped constraints in an idempotency-marked comment, and return
  the issue to `todo`;
- **human pickup:** leave the issue `blocked`, without `agent`;
- **reject or duplicate:** apply the configured terminal decision and close it.

The risk preflight accepts `sensitive_approved` only when the issue also contains
completed investigation findings and the approval marker authored by the
configured human. This prevents an arbitrary label addition from bypassing the
risk gate and avoids routing an approved issue back to investigation forever.

## Provider contracts

Provider selection remains explicit and fail-closed.

### GitLab

The existing `references/providers/gitlab.md` contract continues to own GitLab
Issue operations. It must expose the full provider-neutral operations used by
the shared delivery lifecycle: list, inspect, comment, claim/transition, create
or find an MR, read checks and reviews, merge, and close the issue.

State transitions preserve all non-state labels and replace only the configured
scoped state label. Closing applies the configured `done` label and closes the
issue.

### GitHub

Add a native GitHub Issues provider contract distinct from the existing
GitHub-forge-plus-Linear-tracker contract. It validates the configured GitHub
host, authenticated user, owner, and repository before any lookup or mutation.

It maps provider-neutral tracker operations to GitHub Issues through `gh`:

- list and inspect open issues with configured labels;
- read and post comments;
- preserve categorization labels while replacing the Pitcrew state label;
- locate or create a PR idempotently from the ticket branch;
- read PR reviews and checks;
- squash-merge and delete the branch after all gates pass;
- apply the configured `done` label and close the issue.

An issue is never silently redirected between GitHub, GitLab, or Linear when a
provider capability or authentication check fails.

The first version supports native matched pairs only:
`forge=github, tracker=github` and `forge=gitlab, tracker=gitlab`. Cross-provider
issue-to-change routing remains out of scope.

## Bugfix lifecycle

### 1. Select and claim

The role first services bug tickets already in `review`, including change
requests, human `go`, CI holds, and terminal merge. If no review work is
actionable, it selects the highest-priority eligible `todo` issue, using
quick-win, priority, and age ordering consistent with the existing crew.

After risk and dependency checks, it acquires the durable ticket lock, re-fetches
the issue, moves it to `processing`, and posts a concise pickup comment.

Because scheduled bug runs can execute concurrently for different tickets, each
claimed ticket uses an isolated checkout. Existing unrelated working-tree
changes are never copied, reset, staged, or committed.

### 2. Reproduce

The role reads repository instructions and applicable project references, then
creates the smallest automated reproduction at the appropriate test layer. It
runs that exact reproduction before modifying production code.

The reproduction is valid only when it fails for the behavior described by the
issue. A setup failure, unrelated failing test, missing external service, or
assertion unrelated to the report does not count.

The ticket receives concise, redacted evidence:

- reproduction command;
- relevant failing assertion or observed result;
- expected result;
- affected revision.

If no valid reproduction can be produced within the bounded run, the role makes
no production-code change and opens no PR/MR. It moves the issue to `blocked`
and posts the attempted commands, results, and remaining hypotheses.

### 3. Fix and verify

After a valid red reproduction, the role applies the smallest fix that addresses
the demonstrated cause. The same reproduction must turn green. The role then
runs focused tests plus the repository's required validation commands.

The normal implementer scope guardrails still apply. Architectural ambiguity,
an unavailable required service, insufficient confidence, or unexpectedly broad
blast radius moves the issue to `blocked` with evidence instead of encouraging
speculative changes.

Repository instructions remain authoritative. Secret reads, database writes,
destructive Git, and production or preproduction actions remain governed by the
project safety policy.

### 4. Open the change

The role creates or reuses an idempotently named branch and PR/MR linked to the
issue. The change description includes:

- issue reference and root cause;
- red reproduction evidence;
- fix summary;
- green reproduction and validation commands;
- explicit remaining risks, if any.

It transitions the issue to `review` and posts the change URL. `reviewer-run`
and `validator-run` remain independent actors.

### 5. Review, validate, and merge

On later passes, `bugfixer-run` handles reviewer change requests, with at most
two fix attempts. Each attempt reruns the reproduction and relevant validations.
Exhaustion or an unresolvable CI failure moves the issue to `blocked` and leaves
the PR/MR open for human pickup.

For production-code changes, merge requires all of:

1. the configured reviewer has signed off on the current head SHA;
2. the validator has passed the current head SHA;
3. the configured human has posted an actionable `go`;
4. all required CI checks are green;
5. the issue and PR/MR still match the expected repository, branch, and SHA.

After a successful squash merge, `bugfixer-run` deletes the source branch,
applies `done`, posts the merge audit comment, and closes the issue. It re-reads
both resources to verify the terminal state before reporting completion.

## Shared delivery contract

A provider-neutral shared reference owns behavior common to `implementer-run`
and `bugfixer-run`:

- validated provider dispatch and binding;
- issue state transitions and label preservation;
- idempotency markers and uncertain-result recovery;
- branch and PR/MR lookup or creation;
- review, validator, human-go, and CI gates;
- merge and lifecycle closeout;
- bounded retry and blocked-state behavior.

Role skills retain their own selection and domain policy:

- `implementer-run`: non-bug implementation work;
- `bugfixer-run`: bug-only selection, sensitivity routing, red reproduction, and
  regression verification.

The shared reference is normative rather than duplicated prose. Provider-specific
commands remain in provider references.

## Runtime coordinator integration

`bugfixer-run` is not enabled until the durable ticket-run coordinator is wired
into all launch paths. This feature includes the remaining integration work:

- dashboard and directed launches enqueue a canonical issue target before Codex
  starts;
- scheduled launches enqueue a targetless run, receive an opaque `run_id`, and
  call `bind-target` immediately after issue selection and before any tracker or
  repository mutation;
- the runner claims capacity, records the worker PID, emits heartbeats and public
  phases, and finalizes every success, failure, cancellation, or no-op;
- the dashboard and scheduler reconcile interrupted workers and drain queued
  runs;
- the legacy per-role lock remains as a temporary coarse fallback until the
  coordinated path passes its concurrency tests, but it is not the source of
  ticket ownership.

If `bind-target` reports that another active run owns the issue, the scheduled
skill performs no remote mutation and selects another eligible issue or exits
with a structured no-op.

## Configuration

GitLab continues to use its existing validated block. Native GitHub Issues adds a
parallel explicit block:

```json
{
  "providers": {
    "forge": "github",
    "tracker": "github"
  },
  "github": {
    "host": "github.com",
    "user": "configured-login",
    "owner": "configured-owner",
    "repository": "configured-owner/configured-repository",
    "tracker": {
      "assignee_login": "configured-login",
      "ticket_prefix": "configured-owner/configured-repository#",
      "labels": {
        "agent": "pitcrew-agent",
        "investigate": "pitcrew-investigate",
        "quick_win": "pitcrew-quick-win",
        "bug": "bug",
        "improvement": "enhancement"
      },
      "states": {
        "todo": "pitcrew-state-todo",
        "processing": "pitcrew-state-processing",
        "review": "pitcrew-state-review",
        "blocked": "pitcrew-state-blocked",
        "done": "pitcrew-state-done"
      }
    }
  }
}
```

Configuration validation requires every shown GitHub identity, repository,
tracker, label, and state field when `providers.tracker=github`. It rejects a
repository that is not exactly `<owner>/<name>`. Authentication validation also
requires the active `gh` identity and repository remote to match those values.

This extends schema version 1 compatibly: existing projects without a native
GitHub tracker remain readable. A migration/configuration command prompts for or
accepts the explicit GitHub binding; it never derives an owner or repository
from the current directory or authentication identity. The shipped generic
profile documents the new block without silently enabling it.

Existing provider mappings supply logical label and state names. The role adds
sensitivity policy and one explicit human-approval label:

```json
{
  "bugfixer": {
    "sensitive_labels": [
      "security",
      "authentication",
      "payment",
      "privacy"
    ],
    "risky_categories_regex": "security|auth|payment|PII|secret|token",
    "sensitive_approved_label": "pitcrew-risk-approved"
  }
}
```

Label matching is exact and case-sensitive after provider normalization. Regex
matching is case-insensitive and applies to issue title, description, and
configured category labels. The approval label must already exist on the
configured tracker; Pitcrew does not invent it during a run. Invalid or missing
sensitivity configuration disables `bugfixer-run` with an explicit reason
rather than weakening the risk gate.

`bugfixer-run` uses the quality model by default and receives the same optional
per-role model override and concurrency configuration as other roles.

## Scheduler and dashboard

The scheduler registers `bugfixer-run` every 15 minutes. Each invocation remains
a bounded, one-ticket pass. The role is enabled only when:

- the tracker is `github` or `gitlab`;
- the forge supports the matching PR/MR workflow;
- `agent`, `bug`, `investigate`, and lifecycle labels are configured;
- sensitivity policy is valid;
- at least one repository binding is valid.

Otherwise it remains visible but disabled with the precise reason.

The dashboard replaces its GitLab-only work collector with a provider-neutral
forge-work service. GitHub and GitLab adapters normalize issues, PRs/MRs, labels,
lifecycle, checks, related changes, canonical URLs, and agent actions into the
same internal shape. A provider-neutral `/api/forge-work` endpoint drives the
UI; `/api/gitlab` can remain as a compatibility alias during migration.

The dashboard adds:

- a Bugfixer role card with status, model, latest run, usage, and controls;
- bugfix events in retained activity history;
- a `Corriger ce bug` action for eligible `todo` issues;
- the reproduction, PR/MR, review, validation, and blocked reasons in ticket
  detail;
- routing of the generic `todo` action to `bugfixer-run` for bug-labelled
  issues and to `implementer-run` for other eligible work.

Provider-specific authentication or API failure degrades only the forge-work
panel. Local agent status, history, controls, and decisions remain available.

## Error handling and idempotency

- Provider, identity, host, project, or repository mismatch returns a structured
  no-op before mutation.
- Every comment, label transition, branch, and PR/MR uses an operation marker
  and lookup-before-create behavior.
- After a timeout or uncertain network response, the role re-reads remote state
  before retrying.
- The local durable ticket lock plus immediate remote state checks prevent two
  local runs from claiming the same issue.
- Logs, issue comments, and reproduction excerpts redact secrets and cap noisy
  output.
- A crash in `processing` follows the existing stale-run reconciliation policy;
  it never causes a second change to be created blindly.

## Testing

### Contract and unit tests

- Role registration, model defaults, profile validation, scheduler eligibility,
  dashboard descriptions, and directed-target allowlists include
  `bugfixer-run`.
- Configuration fixtures cover the complete native GitHub block, malformed or
  mismatched owner/repository values, and backward compatibility for projects
  whose tracker is not GitHub.
- Selection fixtures prove that `bugfixer-run` accepts only `agent + bug + todo`
  and that `implementer-run` excludes `bug` at every state.
- Risk fixtures cover exact sensitive labels, regex matches, label preservation,
  `agent` removal, `investigate` addition, the human-approved return path, bypass
  rejection, and no repository writes before approval.
- Reproduction fixtures distinguish a valid bug failure from setup and unrelated
  failures.
- Idempotency and concurrency fixtures cover duplicate launches, uncertain
  provider results, admission, target binding, heartbeat/finalization, stale
  state, and existing PR/MR reuse.

### Provider parity tests

The same lifecycle scenarios run against GitHub and GitLab provider fixtures:

1. ordinary bug reaches a PR/MR with red-to-green evidence;
2. unreproducible bug blocks without production-code changes or PR/MR;
3. sensitive bug routes to investigation;
4. review or validation failure prevents merge;
5. signed-off, validated, human-approved, CI-green work merges and closes once.

Dashboard parity tests run the same normalized work fixtures through GitHub and
GitLab adapters and verify the Bugfixer CTA, ticket detail, degraded panel, and
compatibility alias.

### Repository verification

The complete deterministic suite remains `bash tests/run.sh`. Focused tests cover
configuration, models, CLI, scheduling, run coordination, dashboard APIs and UI,
provider contracts, skill contracts, and documentation before the full suite.

## Acceptance criteria

The feature is complete when all of the following are demonstrable:

1. A configured GitLab issue with `agent + bug + todo` is claimed only by
   `bugfixer-run`, produces a real red-to-green reproduction, and opens one MR.
2. The equivalent configured GitHub issue follows the same lifecycle and opens
   one PR, and both providers expose the same dashboard actions and status.
3. `implementer-run` never selects, repairs, or merges an issue carrying `bug`.
4. Sensitive and unreproducible issues produce the specified non-coding outcomes;
   a sensitive issue can resume only through the investigated, human-approved
   unblock path.
5. No bug change merges before current-head review, validation, human `go`, and
   green CI, and successful merge closes the issue exactly once.
