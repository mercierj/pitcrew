# Architecture and Preprod Review Agents

## Goal

Add two distinct Pitcrew roles:

- `architecture-run` finds high-confidence structural architecture problems and
  proposes bounded improvements for human approval.
- `preprod-review-run` performs an explicitly manual, exhaustive review of the
  Git delta that has not yet reached Preprod.

These roles do not replace `research-run` or `reviewer-run`. The researcher
checks one rotating repository cell for hygiene, hardening, documentation drift,
or declared cross-repository contract drift. The reviewer evaluates one open
merge request against its specification. The new roles instead address
structural design quality and the interaction of all changes accumulated between
the Preprod and development branches.

## Role boundaries

### `architecture-run`

`architecture-run` is read-only. One pass examines one deterministic, rotating
area of one configured repository and may record high-confidence architecture
proposals. It never modifies source code, creates remote issues, or approves its
own recommendations.

The role looks specifically for:

- responsibilities mixed across module or layer boundaries;
- domain logic coupled to transport, persistence, framework, or presentation
  details;
- dependency direction violations and dependency cycles;
- duplicated structural patterns that should have one shared boundary;
- public interfaces that expose internal implementation details;
- changes whose blast radius demonstrates a missing abstraction or unstable
  contract.

General hygiene, isolated type or error-handling smells, security vulnerabilities,
test-flow gaps, and documentation drift stay with their existing roles. An
architecture proposal must explain the boundary being violated, cite tracked
files and relevant lines, describe the practical impact, and give a bounded
remediation sketch. Low-confidence stylistic preferences are discarded.

### `preprod-review-run`

`preprod-review-run` is read-only and manual-only. It reviews the exact merge-base
delta:

```text
origin/preprod...origin/develop
```

The run treats the delta as one release candidate rather than a collection of
independent merge requests. It checks individual changes and cross-change
interactions, including regressions, business invariants, permissions, security,
database migrations, configuration compatibility, background work, frontend and
backend contracts, and missing tests.

The role does not comment on GitLab, create issues, merge branches, access the
Preprod environment, or deploy. Its only durable output is a local report.

## Configuration and models

Both roles are added to the normal role and model registries and to the generic
and GetBill profiles.

`architecture-run` defaults to:

```json
{"model": "gpt-5.6-sol", "reasoning_effort": "high"}
```

`preprod-review-run` is pinned to:

```json
{"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}
```

The runtime configuration schema gains a validated `reasoning_effort` field for
roles. The launcher passes both the configured model and effort explicitly so a
run cannot inherit a different user-level default. The Preprod review card does
not expose a model selector: its Sol/xhigh policy is part of the role contract.

The project profile defines:

```json
{
  "architecture": {
    "interval_seconds": 604800
  },
  "preprod_review": {
    "base_ref": "origin/preprod",
    "compare_ref": "origin/develop",
    "history_limit": 10
  }
}
```

Only validated remote branch references are accepted. The default values above
are explicit in the GetBill profile and are not inferred from the current local
branch.

## Architecture coverage and proposal flow

Architecture coverage uses the same principles as research coverage without
sharing research state. The role stores project-scoped state in
`architecture-state.json`. The state contains stable, sorted top-level areas,
their last inspected timestamps and Git tree fingerprints, and the current
coverage epoch.

Each weekly or manual pass:

1. Loads the configured repository and applicable `AGENTS.md` and architecture
   references.
2. Excludes ignored, generated, dependency, secret, uploaded, and potentially
   customer-data paths.
3. Selects the least recently inspected area, prioritizing an area whose tracked
   tree fingerprint changed.
4. Reads adjacent interfaces only when needed to prove a boundary problem.
5. Records at most three proposals above an explicit high-confidence threshold.
6. Atomically updates coverage state even when no proposal is eligible.

Architecture proposals reuse the project proposal store after adding
`architecture` to its validated categories. A stable proposal id includes the
repository, affected boundary, evidence paths, and relevant tree fingerprint.
This prevents unchanged findings from being proposed repeatedly.

New proposals remain `suggested`. The dashboard offers Approve, Investigate, and
Reject actions. Reject requires a reason. Approve and Investigate change only
local proposal state; they do not call GitLab directly. `manager-run` consumes
approved architecture proposals that have no tracker metadata, performs its
normal deduplication and queue-depth checks, creates at most its bounded amount
of work, and writes the resulting tracker reference back to the proposal.
Provider failure leaves the approved proposal eligible for a later manager pass.

Existing feature and security proposal behavior is unchanged.

## Preprod review execution

The manual trigger runs through the normal locked, ephemeral execution path so
the global stop switch, one-role lock, live status, bounded summary, history, and
token accounting remain effective. It has no launchd job, interval, Restart
control, or automatic invocation path.

One run performs these stages in order:

1. Validate the repository, instructions, configured refs, model, and effort.
2. Fetch the configured remote refs without changing the working tree.
3. Resolve and persist the exact base SHA, compare SHA, and merge-base SHA.
4. Build a deterministic manifest of every commit and tracked changed file in
   the three-dot delta.
5. Review every manifest entry. For a large delta, partition files
   deterministically by component and risk, while retaining a coverage record for
   each file.
6. Perform a final cross-component synthesis after all partitions are covered.
7. Validate report completeness and atomically store the report.

Local modified and untracked files are excluded because they are not part of the
remote branch delta. Renames and deletions remain in the manifest. Binary or
generated files are recorded as such; their integration impact is reviewed even
when their raw content is not suitable for textual analysis.

A repeat run at the same base and compare SHA updates the report for that SHA
pair rather than creating an indistinguishable duplicate. A changed SHA pair
creates a new history entry. The configured history limit retains the newest ten
reports.

## Report contract

Reports live in a project-scoped, owner-only JSON store separate from the general
run summary. Every record contains:

- creation and completion timestamps;
- base, compare, and merge-base refs and SHAs;
- commit and changed-file counts;
- the deterministic file manifest and per-file coverage state;
- findings with severity, evidence, affected files, impact, and recommendation;
- a cross-change synthesis;
- the final verdict and, for incomplete runs, a bounded failure reason;
- model and reasoning effort.

The allowed verdicts are:

- `ready`: all manifest files were examined, synthesis completed, and no critical
  or high-severity finding remains;
- `changes_required`: coverage and synthesis completed, with at least one
  critical or high-severity finding;
- `incomplete`: refs, fetch, coverage, synthesis, persistence, or another
  required gate failed.

Medium and low findings are visible but do not alone change `ready` to
`changes_required`. The report must never emit `ready` from partial coverage.
No-delta is a valid complete report with zero commits, zero files, and `ready`.

## Dashboard

`architecture-run` appears with scheduled agents. Its card shows the weekly
cadence, next run, configured Sol/high policy, run health, usage, and the existing
Trigger, Stop, and Restart controls. Architecture suggestions appear in the
proposal panel with their category and evidence.

The dashboard adds a separate **Revue avant Preprod** panel for the manual role.
It shows:

- the fixed `origin/preprod...origin/develop` scope;
- the fixed Sol/xhigh policy;
- live, stopped, failed, or idle state;
- a **Lancer la revue complète** button with a cost/latency confirmation;
- a Stop control only while the role is running;
- the latest report, SHA pair, age, coverage counts, verdict, findings, and
  synthesis;
- access to the retained report history.

The trigger is disabled while the role is running or while the project-wide stop
switch is active. The panel exposes no scheduling, model-change, merge, release,
deployment, or remote-environment controls.

## Failure handling and safety

Both roles follow applicable repository instructions and preserve unrelated
working-tree changes. They do not read secrets or untracked data corpora.

`architecture-run` returns a structured no-op when configuration or the selected
area is unavailable. Invalid proposal data is rejected before persistence.

`preprod-review-run` fails closed. A failed fetch, missing ref, invalid SHA,
manifest mismatch, uncovered file, missing synthesis, model-policy mismatch, or
write failure produces `incomplete`, never `ready`. An earlier successful report
remains in history but is visibly stale and is not presented as the result of
the failed SHA pair.

Stopping either role preserves the last valid durable state. Temporary writes
are atomic and owner-only. Error messages are bounded and redacted before the
dashboard exposes them.

## Verification

Automated tests cover:

- plugin, installer, configuration, model, and reasoning-effort contracts for
  both roles;
- the seven-day architecture schedule and absence of any Preprod review
  schedule or launchd job;
- deterministic architecture area rotation, changed-fingerprint priority,
  exclusions, deduplication, and proposal limits;
- `architecture` proposal validation and the human approval-to-manager handoff,
  including idempotent tracker metadata;
- explicit three-dot ref validation, SHA capture, commit/file manifests,
  renames, deletions, binaries, generated-file coverage, and no-delta behavior;
- fixed Sol/xhigh execution for `preprod-review-run`;
- `ready`, `changes_required`, and every fail-closed `incomplete` path;
- report replacement for identical SHA pairs, bounded history, atomic writes,
  permissions, and malformed-store rejection;
- dashboard rendering, confirmation, global-stop and overlap gates, live Stop,
  report history, output escaping, and the absence of forbidden schedule,
  model, merge, release, or deployment controls.

The complete deterministic test suite must pass after the new roles are added.
