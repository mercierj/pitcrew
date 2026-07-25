# Cost-aware no-op gate

## Goal

Prevent scheduled Pitcrew runs from invoking Codex when a deterministic local or
provider-health check already proves that the pass cannot do useful work.

## Design

The runner gains a preflight phase before model resolution and Codex launch. It
checks the persistent global stop, required local runtime inputs, and a project
provider circuit-breaker state. A failed provider health result records a
time-limited degraded state; subsequent scheduled runs return a structured no-op
until the cooldown expires. Existing role prompts remain responsible for
role-specific eligibility because that logic is currently encoded in skills.

The first implementation deliberately avoids duplicating provider-specific issue
queries in the shell runner. It therefore prevents repeated known failures and
configuration no-ops immediately, while preserving the current provider and
role behavior. The history record distinguishes preflight no-ops from model runs.

## Safety and compatibility

- No repository or provider mutation occurs during preflight.
- Existing global-stop behavior remains unchanged.
- Existing model assignments remain unchanged for real work.
- Legacy runtime state and history remain readable.
- Cooldown state is atomic, owner-only, and scoped to one project.

## Verification

Tests cover: healthy preflight, active cooldown, expired cooldown, malformed
state fail-closed behavior, and the runner not invoking a fake Codex binary when
preflight blocks the run.
