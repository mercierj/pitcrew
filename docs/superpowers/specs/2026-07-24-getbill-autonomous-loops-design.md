# GetBill Autonomous Pitcrew Loops Design

## Goal

Run the Codex-native Pitcrew roles for GetBill continuously, with the same
independent cadence model as the upstream Claude `/loop` setup.

## Architecture

Each role remains a one-pass skill. A macOS `launchd` job invokes the existing
headless runner at the role's cadence. The runner validates the GetBill runtime,
uses a per-role non-blocking lock to prevent overlapping runs, and writes a
bounded log. GitLab issues and labels provide the shared lifecycle board.

The acting roles use GitLab project `getbill1/getbill`. GitLab scoped labels map
Pitcrew's lifecycle states (`agent::todo`, `agent::processing`,
`agent::review`, `agent::blocked`, `agent::done`). The release role remains
disabled. Prod and preprod mutations remain approval-gated and are never
performed by unattended loops.

## Enabled roles

- Research, manager, implementer, reviewer, validator, investigator, and stale
  sweep form the core delivery loop.
- QA, coverage, dev verification, and ops are installed but disabled until
  their required flow repository or health configuration exists.
- Unblock is installed but disabled because it requires a human response.
- Releaser is neither installed nor scheduled for GetBill.

## Safety

- Every invocation handles at most one eligible item.
- Concurrent invocations of the same role exit as structured no-ops.
- The runner uses Codex workspace-write sandboxing and enables network only for
  the bounded headless invocation.
- No secret values are stored in Pitcrew configuration or launchd files.
- Existing GetBill working-tree changes are preserved.
- Database writes, destructive Git, release, prod, and preprod automation remain
  disabled.

## Verification

- Unit tests cover schedule rendering, role allowlists, locking, and safe
  defaults.
- The full Pitcrew suite and plugin validators must pass.
- A dry-run must resolve the installed GetBill configuration.
- One read-only research pass must complete under the same runner used by
  launchd.
- `launchctl print` must confirm each enabled job is registered.
