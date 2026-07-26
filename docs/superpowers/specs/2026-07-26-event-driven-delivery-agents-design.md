# Event-Driven Delivery Agents

## Goal

Run Pitcrew's ticket-delivery roles immediately from durable Python lifecycle
events, rather than polling them on a timer. Keep periodic scheduling only for
agents whose job is to discover, observe, or reconcile work.

## Scope

The change applies only to work created and transitioned by Pitcrew's Python
processes. It does not add GitLab webhooks, dashboard buttons, or external-event
polling.

## Scheduling policy

These roles remain periodically scheduled because their input is time-based or
requires a recurring scan:

- `research-run`, `security-run`, `architecture-run`, and
  `product-discovery-run` discover new work.
- `ops-run`, `qa-run`, `coverage-run`, and `dev-verify-run` observe configured
  environments or test flows.
- `stale-sweep` periodically reconciles lifecycle drift that can arise outside
  the normal chain.

These roles have no LaunchAgent schedule. They are admitted only by the durable
delivery chain:

- `manager-run`
- `implementer-run`
- `reviewer-run`
- `validator-run`
- `investigate-run`
- `unblock`

`releaser-run` remains explicitly armed/human-gated and
`preprod-review-run` remains manual-only.

## Durable event chain

The run coordinator remains the single owner of admission, deduplication,
capacity limiting, cancellation, and history. When a coordinated worker reaches
a terminal result, Python derives the next eligible lifecycle action from the
authoritative outcome and enqueues it in `runs.sqlite3`. It immediately drains
the queue, so the next worker starts without waiting for a schedule.

The chain uses existing explicit state rather than inferring from a model
transcript:

```text
proposal approved -> manager-run
todo / recoverable processing -> implementer-run
open agent-authored change -> reviewer-run
current-SHA reviewer sign-off -> validator-run
review finding or failed validation -> implementer-run
human decision persisted -> unblock
sensitive or investigation-labelled work -> investigate-run
```

Each transition must be idempotent. Repeated terminal handling for the same
source run may return the existing active/queued next run, but must never create
two workers for the same target. A role with no eligible successor stops the
chain as a normal no-op.

## Failure and safety behaviour

The global stop switch continues to reject admission and cancel active workers.
Provider, parsing, or state-validation failures fail closed: they record a
structured result and do not enqueue a guessed successor. Existing per-skill
capacity limits, target binding, state validation, and seven-day coordinator
history remain unchanged.

`stale-sweep` is the periodic recovery path for missed or externally altered
GitLab state; it may enqueue a valid delivery-chain entry after reconciliation.

## Dashboard and documentation

The dashboard presents chain roles as event-driven, with no interval or restart
schedule. It continues to show queued/running/failed durable runs and identifies
the admission source as `chain`. Periodic roles retain their current cadence and
controls. Documentation describes the two groups and removes claims that the
delivery roles poll every fifteen minutes.

## Tests

Tests cover the role classification, omitted LaunchAgent entries, durable next
run admission, idempotent repeated completion, no successor on a normal
terminal no-op, fail-closed errors, global-stop behaviour, and dashboard status
for event-driven roles. Existing schedule and coordinator tests continue to
cover periodic roles and queue capacities.
