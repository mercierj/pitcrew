# Dashboard Agent Models and Usage Design

**Date:** 2026-07-25

## Goal

Extend the local Pitcrew dashboard so an operator can see and change the Codex
model assigned to each agent, inspect token usage per agent, and view an
estimated API-equivalent cost even though scheduled runs use a Codex
subscription.

## Scope

The feature covers:

- an explicit model assignment for every Pitcrew role;
- a constrained model selector on every enabled agent card;
- interruption and immediate restart when an active agent's model changes;
- per-run token capture without retaining Codex transcripts;
- last-run and rolling seven-day usage per agent;
- a rolling seven-day global usage summary;
- API-equivalent cost estimates based on a versioned pricing snapshot.

The feature does not discover models dynamically from an OpenAI account, expose
arbitrary model strings in the dashboard, change subscription billing, or
retain full Codex event streams.

## Model Catalog and Defaults

Pitcrew owns a constrained catalog containing the current GPT-5.6 variants:

| Model | UI profile | Intended use |
| --- | --- | --- |
| `gpt-5.6-sol` | Quality | Difficult implementation, review, investigation, and decision-heavy work |
| `gpt-5.6-terra` | Balance | General reasoning, validation, research, and release checks |
| `gpt-5.6-luna` | Speed/cost | Frequent, bounded, operational, and bookkeeping work |

The default assignment is:

| Role | Default model |
| --- | --- |
| `research-run` | `gpt-5.6-terra` |
| `manager-run` | `gpt-5.6-luna` |
| `implementer-run` | `gpt-5.6-sol` |
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

Project configuration gains an `agents` object keyed by role. Each entry
contains a `model` from the constrained catalog. Existing configurations that
omit `agents` remain valid and resolve to the defaults above. When configuration
is next changed through the dashboard, Pitcrew writes the selected explicit
override while leaving unrelated configuration untouched.

The dashboard API returns both the configured model and the model from the
latest measured run. This distinction makes configuration drift or a failed
model launch visible.

## Execution Contract

The bounded runner resolves the selected model for the requested project and
role, then passes it to Codex with:

```text
codex exec --model <model> --json ...
```

Scheduled execution continues to write only the final bounded summary to the
existing summary file. The JSONL event stream is consumed by the locked runner
for usage metadata and is not copied into run history or another persistent
transcript.

An unsupported or malformed model value fails closed before Codex starts. A
model unavailable to the current Codex account produces a normal failed run
whose error is bounded and scrubbed by the existing error-handling path.

## Model Change Flow

An enabled agent card exposes a model selector. Selecting a different model
opens a confirmation that states that any active work will be interrupted and
the agent will restart immediately.

After confirmation, the local dashboard performs one serialized control
operation for that role:

1. Validate the role and model against server-owned allowlists.
2. Stop the scheduled role, which interrupts its active process when present.
3. Atomically persist the selected model in the project runtime
   `config.json`.
4. Reinstall the scheduled role so its launch configuration remains loaded.
5. Trigger an immediate run with the new model.
6. Refresh dashboard state and report the outcome.

The selector and other controls for the role remain disabled during this
operation.

The operation does not attempt a transactional rollback across process control
and configuration:

- If stopping fails, configuration is not changed and no restart is attempted.
- If persistence fails after a successful stop, the old configuration remains
  on disk and the agent stays stopped.
- If persistence succeeds but reinstall or trigger fails, the new model remains
  configured and the dashboard reports the agent as stopped or degraded with a
  scrubbed error. The operator can retry using existing controls.

Disabled roles show their resolved default or configured model but do not expose
an editable selector.

## Usage Record

Every new scheduled history record includes the resolved `model`. It also
includes `usage` when Codex emits a usable final usage event:

```json
{
  "model": "gpt-5.6-terra",
  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
  }
}
```

All usage values are non-negative integers. `total_tokens` is the total reported
by Codex when available; otherwise Pitcrew derives it consistently from the
reported categories and documents that behavior in tests.

The history schema remains backward compatible. Records without `model` or
`usage` remain readable and display usage as unavailable. Malformed optional
usage metadata is ignored without invalidating the required run record.

Interrupted and failed runs retain any final usage event emitted before process
termination. If no final usage exists, the run remains valid with unavailable
usage.

## Aggregation and Pricing

The dashboard backend computes usage rather than trusting totals supplied by
the browser. It returns:

- usage and cost for the latest run of each agent;
- usage and cost for each agent over the retained rolling seven-day window;
- usage and cost across all agents over that same window.

Costs are computed per history record using the model recorded on that run.
This preserves accuracy when an agent changes models during the seven-day
window.

The initial standard API pricing snapshot, in USD per one million tokens, is:

| Model | Input | Cached input | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| `gpt-5.6-sol` | 5.00 | 0.50 | 6.25 | 30.00 |
| `gpt-5.6-terra` | 2.50 | 0.25 | 3.125 | 15.00 |
| `gpt-5.6-luna` | 1.00 | 0.10 | 1.25 | 6.00 |

The pricing snapshot lives in one server-side catalog alongside its effective
date and currency. The API returns that metadata so the UI can label estimates
honestly. Monetary calculations avoid binary floating-point accumulation and
round only for display.

The dashboard always labels the result as an estimated API equivalent, not an
amount billed through the Codex subscription. It does not include tool-call,
container, regional-processing, priority-tier, or other non-token charges.

## Dashboard Presentation

Each enabled agent card shows:

- the editable model selector and its Quality, Balance, or Speed/cost profile;
- the model recorded for the latest run;
- input, cached-input, cache-write, output, and total tokens for the latest run;
- the same usage categories aggregated over seven days;
- latest-run and seven-day API-equivalent cost estimates.

The overview adds seven-day total tokens and total API-equivalent estimated
cost. Missing usage is shown explicitly instead of as zero. Mixed availability
is represented as a measured subtotal so older unmeasured records are not
silently treated as free.

The activity list shows the recorded model and total tokens for each measured
run. Detailed category breakdown remains on the agent card to keep the history
readable.

All UI text is inserted using DOM text APIs. No model, usage, or error value is
rendered as HTML.

## Local API and Safety

The dashboard gains a dedicated model-change endpoint. It uses the same
localhost-only server, session token, same-origin checks, request-size limits,
and serialized per-role controls as existing dashboard actions.

The server validates:

- the role is known and enabled;
- the requested model is an exact member of the server-owned catalog;
- the body contains no unsupported control action.

Configuration writes are atomic and preserve restrictive runtime file
permissions. Error responses are bounded and scrubbed. The endpoint never
accepts a command, path, pricing value, or arbitrary Codex argument from the
browser.

## Testing

Automated tests cover:

- default resolution for every role and compatibility with configurations that
  predate the `agents` object;
- rejection of unknown roles, unknown models, malformed `agents` entries, and
  duplicate or unsupported input;
- preservation of unrelated configuration during atomic model persistence;
- inclusion of the resolved `--model` argument in scheduled Codex execution;
- JSONL usage extraction without transcript retention;
- partial, missing, malformed, interrupted, and failed usage streams;
- backward-compatible history parsing;
- per-run, per-agent seven-day, and global aggregation across mixed models;
- pricing calculations for input, cached input, cache writes, and output;
- measured-subtotal behavior when old history lacks usage;
- stop, persist, reinstall, and immediate-trigger ordering;
- failure behavior at every model-change stage;
- session, origin, and allowlist enforcement on the new endpoint;
- safe DOM rendering, disabled controls during requests, confirmation behavior,
  and responsive agent cards.

Focused unit and integration tests run before the full repository test suite.

## Documentation

The runtime configuration example and scheduled-task reference document:

- the model catalog and role defaults;
- the `agents.<role>.model` override;
- interruption and restart semantics;
- optional history usage fields;
- the seven-day aggregation window;
- the pricing snapshot date and exclusions;
- the distinction between subscription usage and API-equivalent estimates.
