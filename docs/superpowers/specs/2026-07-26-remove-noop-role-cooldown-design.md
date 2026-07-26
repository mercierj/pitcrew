# Remove no-op role cooldown

## Goal

Ensure every scheduled role evaluates newly eligible work at its configured
interval, even after its preceding pass returned a normal no-op.

## Design

The runner will stop persisting per-skill cooldown entries for structured
`noop` and `blocked` results. A no-op is ordinary queue state, not a provider
failure; it must remain observable in history but must not suppress the next
scheduled pass.

The project-wide provider authentication circuit-breaker remains unchanged: it
continues to prevent repeat model launches for thirty minutes after an actual
authentication failure. Existing state files remain readable; the preflight
will ignore legacy `skills` entries rather than block a role because of them.

## Verification

- A normal `record-noop` result does not block the same skill's next preflight.
- A provider authentication failure still blocks scheduled work until expiry.
- The CLI test verifies a structured `noop` or `blocked` result does not create
  a per-skill cooldown state.
