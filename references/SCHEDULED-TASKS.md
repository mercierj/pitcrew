# Scheduled Codex tasks

Schedulers invoke namespaced Pitcrew skills from a configured project directory.
Every scheduled invocation is a single bounded pass and must emit the runtime
structured result.

## Agent model and usage controls

Each configured role can pin `agents.<role>.model` to one of the catalog slugs.
`agents.<role>.fallback` describes the built-in fallback used only when an older
configuration omits that role; it is not a writable configuration field. The
profiles pin all roles so model selection stays explicit:

| Model | Input | Cache read | Cache write | Output | Why |
|---|---:|---:|---:|---:|---|
| `gpt-5.6-sol` | 5 | 0.5 | 6.25 | 30 | strongest quality profile |
| `gpt-5.6-terra` | 2.5 | 0.25 | 3.125 | 15 | balanced quality and cost |
| `gpt-5.6-luna` | 1 | 0.1 | 1.25 | 6 | fastest, lowest-cost profile |

| Role | Model |
|---|---|
| `security-run` | `gpt-5.6-sol` |
| `architecture-run` | `gpt-5.6-sol` |
| `preprod-review-run` | `gpt-5.6-sol` |
| `product-discovery-run` | `gpt-5.6-terra` |
| `research-run` | `gpt-5.6-terra` |
| `manager-run` | `gpt-5.6-luna` |
| `implementer-run` | `gpt-5.6-sol` |
| `bugfixer-run` | `gpt-5.6-sol` |
| `reviewer-run` | `gpt-5.6-sol` |
| `validator-run` | `gpt-5.6-terra` |
| `investigate-run` | `gpt-5.6-sol` |
| `stale-sweep` | `gpt-5.6-luna` |
| `qa-run` | `gpt-5.6-terra` |
| `coverage-run` | `gpt-5.6-terra` |
| `dev-verify-run` | `gpt-5.6-terra` |
| `ops-run` | `gpt-5.6-luna` |
| `unblock` | `gpt-5.6-sol` |
| `releaser-run` | `gpt-5.6-terra` |

The dashboard submits the selected slug with `--model`.

The runtime records the selected model and token usage in JSONL history, without
retaining a full transcript. When usage is available, each record includes
`input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`, and
`total_tokens`; the dashboard shows the last run and rolling seven-day measured
subtotal. This is API-equivalent metering, not a subscription charge. It excludes
tools, container execution, regional pricing, and priority pricing.

Pricing snapshot effective 2026-07-24, in USD per million tokens.

## Manual Preprod review (not a scheduled task)

`$pitcrew:preprod-review-run` is manual-only and **never scheduled**. It has no
LaunchAgent, interval, restart, automatic invocation, or scheduler template. The
recommended operator path is the local dashboard's confirmed **Lancer la revue
complète** button. The direct manual CLI path is also supported:

```bash
./bin/pitcrew-codex.sh preprod-review-run getbill
```

Both use the locked, ephemeral local execution boundary. `--scheduled`,
`--coordinated-run`, and `--target` are refused. The global stop switch blocks a
new run. **Arrêter la revue** sends SIGTERM to the tracked helper; a failure or
interrupt marks an older local report stale.

The role is fixed to `gpt-5.6-sol` with reasoning effort `xhigh`; neither is a
role-level configuration choice. Before review, it fetches and captures remote
`origin/preprod...origin/develop` SHAs and merge-base. It excludes the working
tree, untracked files, and ignored files. The manifest is exhaustive: every file
is reviewed exactly once, including deletion, rename, copy, typechange, binary,
and generated entries, followed by cross-change synthesis.

This is a read-only local report workflow. It never creates a GitLab issue,
comment, or MR; never commits, branches, pushes, merges, deploys, accesses the
Preprod environment, or uses a database. Private local writes are limited to the
manifest, result, report, live marker, and bounded history. The helper alone can
return `ready` / **PRÊT** after complete valid coverage. High or critical findings
return `changes_required` / **CORRECTIONS REQUISES**; policy, schema, coverage,
helper, fetch, or interruption failures return `incomplete` / **INCOMPLET**.
History is local, bounded to 10 reports, and replaces a previous record for the
same captured SHA pair.

## Architecture template

`architecture-run` is a weekly role: its configured interval is exactly `604800`
seconds. It uses `gpt-5.6-sol` with reasoning effort `high`. A manual dashboard
Trigger is also available; the schedule never causes it to reschedule itself.

Each pass scans one rotating tracked repository area, records at most maximum 3
high-confidence structured suggestions, and uses the stable architecture category.
Coverage lives separately in `architecture-state.json`: a valid completed scan,
including zero findings, advances the selected area; invalid configuration or
inputs, an incomplete pass, or interruption does not advance coverage state.

Architecture-run is read-only for the repository and tracker. It writes no code,
MR/PR, issue, merge, deploy, or tracker record. Its only writes are local: the
proposal ledger and the separate `architecture-state.json` coverage state. Human
dashboard review is required: the flow is local proposal approval → manager →
tracker. A dismissal stays local, while the manager paces and deduplicates approved
or investigate proposals before attaching tracker metadata.

Suggested weekly task prompt:

```text
Use $pitcrew:architecture-run for project getbill. Perform exactly one bounded,
read-only architecture scan of the selected rotating area, record at most three
high-confidence local proposals, and stop. Do not create code or tracker work.
```

## Research template

Run at a low, predictable cadence such as once per business day:

```text
Use $pitcrew:research-run for project getbill. Perform one bounded read-only pass,
record only high-confidence findings, then stop. Read applicable AGENTS.md
instructions and return a structured no-op if no grounded improvement is eligible.
```

## Reviewer template

Run after reviewable changes are expected, for example every two hours on workdays:

```text
Use $pitcrew:reviewer-run for project getbill. Review one eligible merge request,
respect GetBill approval gates, then stop. Return a structured no-op when none is
eligible.
```

## GetBill policy

For GetBill, keep schedules read-oriented unless the profile explicitly authorizes a
bounded action. Never schedule production or preproduction access implicitly.
GetBill release scheduling is disabled: release preparation and deployment remain
human-initiated, with the required approval for each remote action.
The operator must review the first few scheduled runs before enabling additional
acting roles.

### Bugfixer schedule

`$pitcrew:bugfixer-run` has a bounded 900-second (15-minute) cadence and handles
only `agent + bug + todo` work. GetBill enables it only when the native tracker,
validated bugfixer policy, configured repository, and every required lifecycle
and routing label are present. A missing label returns a structured no-op; the
agent never creates or approximates tracker labels.

Before enabling the role, the operator must create the exact
`pitcrew-risk::approved` label in the configured tracker and verify it through
the selected provider. This label does not authorize a sensitive fix by itself:
completed `$pitcrew:investigate-run` findings and the human approval marker from
`$pitcrew:unblock` are also mandatory.

Implementation requires a valid red reproduction, then the same check green.
Merge requires `$pitcrew:reviewer-run`, `$pitcrew:validator-run`, human `go`, and
green required CI checks on the current head. `implementer-run excludes bugs`
from todo, processing recovery, and review continuation.

## Local headless scheduler

The fork includes an idempotent macOS `launchd` adapter for periodic discovery,
observation, and reconciliation roles. Delivery roles (`manager-run`,
`implementer-run`, `reviewer-run`, `validator-run`, `investigate-run`, and
`unblock`) are event-driven: dashboard actions and validated lifecycle events
admit them through the durable coordinator. They have no interval or restartable
LaunchAgent. `stale-sweep` remains the periodic recovery path for missed or
externally changed lifecycle state.

### Durable ticket-run coordinator

Ticket runs are stored in
`${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/runs.sqlite3`. The default is
three concurrent tickets per role; optionally configure an override with
`execution.max_concurrent_per_skill.<role>`. Additional tickets stay durable in
FIFO order (for example, the fourth ticket waits for a slot). Repeated admission
of the same target returns the active run rather than creating another worker.

The dashboard reads this store, so queued, running, and failed states survive a
page reload. Queued and running ticket CTAs are disabled; failed runs are retried
only by an explicit operator action. Terminal coordinator records are retained
for seven days.

```bash
python3 bin/pitcrew-schedule.py list --project getbill
python3 bin/pitcrew-schedule.py install --project getbill
python3 bin/pitcrew-schedule.py status --project getbill
```

### Global token stop

The scheduler has a persistent per-project stop switch for emergency token
control:

```bash
python3 bin/pitcrew-schedule.py stop-all --project getbill
python3 bin/pitcrew-schedule.py status --project getbill
python3 bin/pitcrew-schedule.py resume-all --project getbill
```

`stop-all` records the stopped state and unloads every enabled launchd job. It
cancels active coordinated ticket workers and suspends queued tickets.
The runner checks that state before repository/model resolution and returns a
structured no-op without invoking Codex. This blocks both launchd and manual
scheduled-mode starts. `resume-all` is explicit, reinstalls the enabled jobs,
and resumes only the queued ticket runs; it does not restart cancelled running
work or trigger an immediate pass.
The dashboard's **Tout arrêter** button
performs the same action after confirmation and shows **Exécutions bloquées**
while the switch is active.

The runner also has a deterministic provider-failure circuit breaker. When a
scheduled pass reports an authentication failure, later passes for the same
project are returned as no-ops for 30 minutes before Codex starts. This avoids
repeating an expensive model call while GitLab authentication is known to be
invalid; the cooldown state is local, atomic, and owner-only.

The safe GetBill core enables research, manager, implementer, reviewer,
validator, investigate, stale-sweep, and unblock. QA, coverage, dev verification,
and ops remain defined but disabled until their required configuration exists.
Unblock runs automatically to publish one pending question when a blocked item
needs a human decision; it never selects the answer itself. Releaser remains
disabled.

Each job invokes `bin/pitcrew-codex.sh` with `--scheduled`. The runner uses a
per-role lock, an ephemeral Codex session, a role-scoped sandbox, explicit
network access for provider-backed roles, and `approval_policy=never`; it never
bypasses the sandbox. On macOS, provider-backed roles use the network-capable
sandbox because workspace-write network access is not effective for subprocesses.
Full Codex transcripts are discarded. Only the role's bounded final summary is
kept at `${CODEX_HOME:-$HOME/.codex}/pitcrew/getbill/logs/<skill>.last.txt`.

## Run history contract

Every attempted scheduled pass appends one JSON Lines record to
`${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/history.jsonl`. Each record has
these fields:

- `project`: configured project name;
- `skill`: the invoked role;
- `started_at` and `finished_at`: UTC timestamps;
- `duration_ms`: elapsed time in milliseconds;
- `outcome`: `success`, `noop`, `failed`, or `interrupted`;
- `exit_code`: process exit code when available;
- `summary`: bounded final summary, never the full transcript.

The history file and its sibling lock file use mode `0600`. The lock serializes
concurrent writers, malformed records are skipped safely, and retention is seven
days; older valid records are removed.

Scheduler health combines the latest record with the loaded/running state. A
successful pass is healthy. An expected `noop`, such as **no eligible item**, is
also healthy. An actionable `noop` caused by missing configuration,
authentication failure, or provider authorization is a warning. A non-zero exit
or `failed` outcome is failed, and a job not loaded in `launchd` is stopped.

## Local dashboard contract

The operator starts the dashboard manually:

```bash
cd /Users/jo/Prog/pitcrew
./bin/pitcrew-dashboard
```

It serves only `http://127.0.0.1:8765` and shuts down with Ctrl-C. The page shows
local scheduler and agent health, seven days of history, and GitLab work. If
GitLab is unavailable, its panel is marked degraded and the local panels and
controls continue to operate.

Controls are restricted to enabled periodic skills:

- **Trigger** starts one bounded scheduled-mode pass; the role lock prevents an
  overlapping pass.
- **Stop** stops the current pass and unloads that role's schedule.
- **Restart** installs or reloads that role's schedule.

Changing an enabled role's model follows the same safe sequence: Stop the running
role, atomically persist its configuration, install the refreshed schedule, then
issue an immediate Trigger. A failure stops at that boundary: there is no rollback
or implicit retry, and the operator sees the actionable error.

These controls invoke only local allowlisted commands with server-resolved
project and skill values. Release, prod, and preprod environment/deployment
controls are absent. The explicit exception is the local, read-only,
manual-only Preprod review panel: it does not deploy, access a remote environment,
or bypass the normal approval and scheduling policy. For scripts and terminals,
the status CLI remains available:

```bash
python3 bin/pitcrew-schedule.py status --project getbill
```
