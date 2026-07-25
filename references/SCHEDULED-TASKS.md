# Scheduled Codex tasks

Schedulers invoke namespaced Pitcrew skills from a configured project directory.
Every scheduled invocation is a single bounded pass and must emit the runtime
structured result.

## Agent model and usage controls

Each configured role can pin `agents.<role>.model` to one of the catalog slugs.
`agents.<role>.fallback` describes the built-in fallback used only when an older
configuration omits that role; it is not a writable configuration field. The
profiles pin all roles so model selection stays explicit:

| Model | Intended use | Why |
|---|---|---|
| `gpt-5.6-sol` | quality-critical implementation, review, investigation, unblock | strongest quality profile |
| `gpt-5.6-terra` | research, validation, QA, coverage, dev verification, release | balanced quality and cost |
| `gpt-5.6-luna` | management, stale sweep, operations | fastest, lowest-cost profile |

The fallback mapping is: research `gpt-5.6-terra`; manager `gpt-5.6-luna`;
implementer, reviewer, investigate, and unblock `gpt-5.6-sol`; validator, QA,
coverage, dev verification, and releaser `gpt-5.6-terra`; stale sweep and ops
`gpt-5.6-luna`. The dashboard submits the selected slug with `--model`.

The runtime records the selected model and token usage in JSONL history, without
retaining a full transcript. When usage is available, each record includes
`input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`, and
`total_tokens`; the dashboard shows the last run and rolling seven-day measured
subtotal. This is API-equivalent metering, not a subscription charge. It excludes
tools, container execution, regional pricing, and priority pricing.

Pricing snapshot effective 2026-07-24, in USD per million tokens (input / cache
read / cache write / output): Sol 5 / .5 / 6.25 / 30; Terra 2.5 / .25 / 3.125 /
15; Luna 1 / .1 / 1.25 / 6.

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

## Local headless scheduler

The fork includes an idempotent macOS `launchd` adapter for the same independent
cadence model as upstream Pitcrew:

```bash
python3 bin/pitcrew-schedule.py list --project getbill
python3 bin/pitcrew-schedule.py install --project getbill
python3 bin/pitcrew-schedule.py status --project getbill
```

The safe GetBill core enables research, manager, implementer, reviewer,
validator, investigate, and stale-sweep. QA, coverage, dev verification, and ops
remain defined but disabled until their required configuration exists. Unblock
remains human-driven, and releaser remains disabled.

Each job invokes `bin/pitcrew-codex.sh` with `--scheduled`. The runner uses a
per-role lock, an ephemeral Codex session, workspace-write sandboxing, explicit
network access, and `approval_policy=never`; it never bypasses the sandbox.
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

Controls are restricted to enabled skills:

- **Trigger** starts one bounded scheduled-mode pass; the role lock prevents an
  overlapping pass.
- **Stop** stops the current pass and unloads that role's schedule.
- **Restart** installs or reloads that role's schedule.

Changing an enabled role's model follows the same safe sequence: Stop the running
role, atomically persist its configuration, install the refreshed schedule, then
issue an immediate Trigger. A failure stops at that boundary: there is no rollback
or implicit retry, and the operator sees the actionable error.

These controls invoke only local allowlisted commands with server-resolved
project and skill values. Release, prod, and preprod controls are absent. The
dashboard cannot deploy, access a remote environment, or bypass the normal
approval and scheduling policy. For scripts and terminals, the status CLI
remains available:

```bash
python3 bin/pitcrew-schedule.py status --project getbill
```
