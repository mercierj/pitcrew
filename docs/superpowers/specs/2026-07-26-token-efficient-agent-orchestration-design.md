# Token-Efficient Agent Orchestration Design

**Date:** 2026-07-26

## Goal

Reduce scheduled Pitcrew token consumption without lowering delivery quality. The
system must avoid model launches when a read-only probe can prove that no work is
eligible, explicitly tune reasoning effort per role, and collect enough quality
evidence before changing the model used for substantive work.

## Success criteria

- A confirmed empty queue never starts Codex.
- Provider ambiguity or failure never hides potentially eligible work.
- Every scheduled attempt is observable, including attempts that stop before
  model selection.
- Scheduled roles do not inherit the operator's global reasoning effort.
- Sol remains the execution baseline until an observed or controlled experiment
  proves that another model preserves role-specific quality.
- Existing approval, sandbox, provider, and GetBill safety boundaries remain
  unchanged.

## Non-goals

- No automatic downgrade of substantive implementation, review, investigation,
  or security work in the first rollout.
- No hard token cutoff before workflows have cooperative checkpoints and remote
  action reconciliation.
- No GitLab webhook service or always-on event listener.
- No removal or consolidation of the reviewer and validator roles.
- No widening of filesystem, provider, deployment, or database permissions.

## Rollout strategy

The work is delivered in three independently verifiable phases.

### Phase A — deterministic savings

Add tri-state eligibility probes, pre-model history records, explicit reasoning
effort, a strict final-result schema, and exact result classification. These
changes save tokens without changing which model handles substantive work.

### Phase B — adaptive pacing

Add per-role exponential empty backoff, suspend unattended unblock while a
persisted human decision is pending, and gate research on repository change or a
daily fallback. Fixed `launchd` intervals remain as a recovery heartbeat; the
preflight gate decides whether a model is needed.

### Phase C — quality-measured routing and progressive context

Record a candidate model and routing reason without changing the execution model.
After sufficient observation, allow a small, reversible Terra cohort for
explicitly low-risk work. Split the largest skills so eligibility and routing are
loaded before the selected execution branch.

## Architecture

### Eligibility engine

Create `scripts/pitcrew_eligibility.py`. It owns read-only role probes and returns
one of three decisions:

```json
{
  "decision": "eligible",
  "project": "getbill",
  "skill": "reviewer-run",
  "target_id": "getbill1/getbill!123",
  "fingerprint": "sha256:...",
  "reason": "one authored merge request has a new head SHA"
}
```

```json
{
  "decision": "empty",
  "project": "getbill",
  "skill": "reviewer-run",
  "target_id": null,
  "fingerprint": "sha256:...",
  "reason": "no authored merge request requires review"
}
```

```json
{
  "decision": "unavailable",
  "project": "getbill",
  "skill": "reviewer-run",
  "target_id": null,
  "fingerprint": null,
  "reason": "configured provider probe failed"
}
```

Only `empty` suppresses Codex. `unavailable` preserves the existing launch path
so a network, authentication, schema, or tool failure cannot silently hide work.
Probe stderr and persisted reasons use the existing bounded redaction rules.

### Conservative role probes

| Role | Authoritative empty signal | Eligible signal | Unavailable behavior |
|---|---|---|---|
| implementer-run | Successful provider queries find no agent `todo` or actionable `review` ticket | First provider-ordered candidate | Launch existing workflow |
| reviewer-run | Successful query finds no authored open MR requiring the current SHA to be reviewed | First updated MR | Launch existing workflow |
| validator-run | Successful query finds no agent ticket in review awaiting validation | First review candidate | Launch existing workflow |
| investigate-run | Successful query finds no investigate-routed todo ticket | First routed ticket | Launch existing workflow |
| unblock | Persisted pending question is awaiting a human, or successful query finds no blocked candidate | Answered pending question or first blocked candidate | Launch existing workflow |
| manager-run | All configured local sources have no unfiled finding | At least one unfiled finding | Launch existing workflow |
| research-run | Next cell fingerprint is unchanged and the daily fallback is not due | Changed next cell or daily fallback due | Launch existing workflow |
| stale-sweep | No due local cleanup and no provider lifecycle candidate is confirmed | Due cleanup or lifecycle candidate | Launch existing workflow |
| security-run | Repository fingerprint is unchanged and daily fallback is not due | Changed repository or daily fallback due | Launch existing workflow |
| product-discovery-run | Repository/product fingerprint is unchanged and weekly fallback is not due | Changed input or weekly fallback due | Launch existing workflow |

Roles disabled by configuration stay disabled. QA, coverage, dev verification,
ops, and release retain their existing configuration gates until their runtime
inputs are enabled.

### Preflight and backoff

`scripts/pitcrew_preflight.py` remains responsible for global/provider cooldown
state and gains per-role empty streaks:

```json
{
  "skills": {
    "reviewer-run": {
      "recorded_at": "2026-07-26T10:00:00Z",
      "reason": "no authored merge request requires review",
      "empty_streak": 3,
      "fingerprint": "sha256:..."
    }
  }
}
```

The delay is `base * 2^(empty_streak - 1)`, capped per role. A changed
fingerprint, an `eligible` decision, or a result with `did_work=true` clears the
streak. Provider failure cooldown remains separate from empty backoff.

Suggested initial caps:

- 4 hours: implementer, reviewer, validator, investigate.
- 8 hours: manager, unblock.
- 24 hours: research, security, product discovery, stale sweep.

These caps affect model launches, not the `launchd` heartbeat.

### Runner flow

For scheduled runs, `bin/pitcrew-codex.sh` performs:

1. Validate project and global execution state.
2. Check provider and empty backoff state.
3. Run the eligibility engine.
4. On `empty`, append a no-model history record and return.
5. On `unavailable`, retain the current workflow launch behavior.
6. On `eligible`, resolve model and reasoning effort.
7. Add the validated target to the prompt.
8. Start Codex with the strict output schema.
9. Normalize the final result and update history/backoff.

Directed human-triggered targets still use `references/DIRECTED-TARGET.md` and
skip automatic target selection after validating the supplied target.

### Model and reasoning configuration

Extend each `agents.<role>` entry:

```json
{
  "model": "gpt-5.6-sol",
  "reasoning_effort": "high",
  "routing_mode": "observe"
}
```

Supported reasoning efforts are `low`, `medium`, and `high` for the initial
contract. Defaults:

| Role group | Effort |
|---|---|
| security, implementer, reviewer, sensitive investigation | high |
| research, validator, product discovery, ordinary investigation, release preparation | medium |
| manager, stale sweep, ops, mechanical triage | low |
| unblock | medium |

The runner always passes `-c model_reasoning_effort="<value>"`, preventing global
operator settings from changing scheduled behavior.

`routing_mode="observe"` computes and records `candidate_model` and
`routing_reason`, but continues to execute the configured `model`. A later
`experiment` mode may route an allowlisted low-risk cohort after the quality
gate is satisfied. Security work and risk-regex matches never enter a downgrade
cohort.

### Strict result contract

Add `references/run-result.schema.json` and pass it through `--output-schema`.
The final result contains:

```json
{
  "status": "success",
  "reason": "review posted and verified",
  "project": "getbill",
  "skill": "reviewer-run",
  "target_id": "getbill1/getbill!123",
  "did_work": true,
  "work_kind": "review",
  "quality_outcome": "signed-off",
  "next_action": "validator may inspect the reviewed change"
}
```

`status` is one of `success`, `noop`, `blocked`, or `failed`. The locked runner
maps `noop` and `blocked` to a non-failing history outcome while preserving the
structured status in the summary. Regex classification remains only as a
backward-compatibility fallback for older records.

### History and dashboard data

Every scheduled attempt records:

- `model_invoked`;
- `model` and `reasoning_effort` when invoked;
- `candidate_model` and `routing_reason` in observe/experiment modes;
- `target_id`, `work_kind`, `did_work`, and `quality_outcome`;
- `gate_decision` and `gate_reason`;
- usage when a model ran.

Preflight empty records have `model_invoked=false`, no model usage, and
`outcome=noop`. Existing history records remain readable.

The dashboard can expose the new fields after the core contracts land. Existing
in-progress dashboard changes are preserved and integrated rather than replaced.

### Progressive skill loading

After Phase A and B stabilize, shorten the largest `SKILL.md` files to:

1. runtime and safety bootstrap;
2. supplied-target revalidation;
3. branch selection;
4. explicit reference load;
5. structured result requirement.

Detailed branches move without semantic deletion:

- implementer: review/merge gate, new-ticket implementation, recovery;
- validator: smart flow, local launch, basic/docs fallback;
- unblock: pending decision, question creation, answer execution, plan drafting;
- reviewer: trivial review and substantive single-change review.

Scheduled reviewer handles one MR per pass. It never spawns an agent team.

## Error handling

- Valid empty provider responses may suppress Codex.
- Network errors, timeouts, invalid JSON, missing provider tools, and auth
  uncertainty return `unavailable`.
- Probe failures do not mutate provider or repository state.
- Empty backoff state uses atomic owner-only writes.
- A model result that cannot satisfy the output schema is a failed run and does
  not advance empty backoff.
- Hard token or wall-clock termination is not enabled until workflows expose
  cooperative checkpoints and reconciliation rules.

## Quality gates

Before any role changes from `observe` to `experiment`, establish a baseline and
define rollback thresholds for:

- reviewer findings accepted by the implementer or human;
- validator pass/fail/inconclusive distribution;
- re-review rounds per MR;
- escaped defects or rollback/revert events;
- interrupted or blocked runs;
- tokens and duration per `did_work=true` unit.

The first experiment is restricted to low-risk docs/tests or explicitly labeled
quick wins. Sol remains the control. Any quality metric crossing its rollback
threshold disables experiment routing for that role.

## Testing

Use test-driven development for every behavior change.

1. Unit tests cover every eligibility decision, provider ambiguity, fingerprint
   change, pending unblock state, and empty streak transition.
2. Runner tests use a fake Codex binary and prove `empty` never invokes it while
   `unavailable` retains the existing launch path.
3. Configuration tests cover reasoning defaults, explicit overrides, invalid
   values, and backward compatibility.
4. Locked-runner/history tests cover schema normalization, no-model records,
   structured outcomes, and legacy summaries.
5. Schedule and skill-contract tests cover one-MR reviewer behavior, research
   fallback cadence, and removal of scheduled multi-agent instructions.
6. The deterministic full repository suite runs before completion.

## Rollback

Phase A can disable eligibility suppression with one configuration flag while
retaining observability and explicit effort. Phase B can reset empty streaks and
restore fixed behavior without changing job definitions. Phase C defaults to
observation and can disable experiments per role without changing the configured
baseline model.
