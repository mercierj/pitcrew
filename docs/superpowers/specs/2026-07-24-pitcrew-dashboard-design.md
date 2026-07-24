# Pitcrew Local Dashboard Design

**Date:** 2026-07-24  
**Status:** Approved in conversation, pending written-spec review

## Goal

Provide a small local interface that makes Pitcrew understandable and operable
without using GitLab as a proxy for runtime health. The operator must be able to
see what every enabled agent did, inspect seven days of bounded activity, follow
Pitcrew work in GitLab, and safely trigger, stop, or restart an agent.

## Scope

The dashboard is part of the Pitcrew fork, runs manually on the operator's Mac,
and monitors the configured `getbill` project. It does not modify the GetBill
application.

Included:

- local health for every configured Pitcrew role;
- last result and seven-day activity history;
- GitLab Pitcrew issues and related merge requests;
- allowlisted trigger, stop, and restart controls;
- clear degraded states when a local dependency or GitLab is unavailable.

Excluded:

- production or preproduction access;
- release and deployment controls;
- full Codex transcripts;
- remote access to the dashboard;
- permanent analytics or a new database;
- changes to GetBill itself.

## Architecture

The operator starts the dashboard manually with:

```bash
./bin/pitcrew-dashboard
```

The command starts a lightweight Python HTTP server bound exclusively to
`127.0.0.1` and prints the local URL. The browser uses a small HTML, CSS, and
JavaScript frontend served by that process.

The server exposes narrow JSON endpoints backed by four independent adapters:

1. **Schedule adapter:** reads the configured schedule and current `launchd`
   state.
2. **History adapter:** reads bounded local run records and applies the
   seven-day retention window.
3. **GitLab adapter:** uses the existing authenticated `glab` CLI to fetch
   Pitcrew issues and related merge requests.
4. **Control adapter:** performs only allowlisted trigger, stop, and restart
   operations for roles present in the configured schedule.

No GitLab token is stored by the dashboard or returned to the browser.

## Run History

The scheduled runner records one JSON Lines entry after every attempted pass.
Each entry contains:

- project and skill;
- start and finish timestamps in UTC;
- elapsed duration;
- outcome (`success`, `noop`, `failed`, or `interrupted`);
- process exit code when available;
- the bounded final summary.

Full Codex transcripts remain discarded. The history writer must serialize
concurrent updates safely, tolerate a missing or malformed history file, and
remove entries older than seven days. The existing per-skill
`<skill>.last.txt` summaries remain available for compatibility.

The dashboard derives agent health from both scheduler state and recent history:

- **running:** a process for the role is currently active;
- **healthy:** loaded and the latest pass succeeded or produced an expected
  no-op;
- **warning:** loaded but its latest bounded result reports a recoverable
  configuration or provider problem;
- **failed:** latest pass exited unsuccessfully;
- **stopped:** the `launchd` job is not loaded.

An expected no-op such as "no eligible item" is healthy. A no-op requiring
operator action, such as a missing required file, is a warning.

## Interface

The page has four sections.

### Overview

Summary counts show active, stopped, running, warning, and failed roles. A global
banner highlights actionable local problems and GitLab synchronization failures.

### Agents

Each enabled role has a card showing:

- current health and whether it is running;
- last pass time, duration, outcome, and bounded summary;
- configured interval and estimated next pass;
- `Trigger`, `Stop`, and `Restart` controls;
- a link to its filtered history.

Human-gated or unconfigured roles may be shown in a collapsed "Not enabled"
section with the configuration reason, but they have no action controls.

### Recent Activity

A seven-day timeline can be filtered by role and outcome. Failures and warnings
are visually prominent. Empty history and malformed records produce explicit,
non-fatal states.

### GitLab Work

Pitcrew-labelled issues are grouped by the configured lifecycle labels:
`todo`, `processing`, `review`, `blocked`, and `done`. Each item shows its
agent or investigation route, source, title, update time, related merge request
when discoverable, and a direct GitLab link.

The page polls local status every ten seconds. GitLab data may use a longer cache
to avoid excessive API requests, with a manual refresh button available.

## Controls

All control requests are POST operations and accept only project and skill values
resolved from the server-side schedule:

- **Trigger:** start one bounded scheduled-mode pass immediately. The existing
  non-overlap lock turns an overlapping request into a no-op.
- **Stop:** unload the role's `launchd` service and stop its current process.
  The UI requires confirmation.
- **Restart:** reinstall and load only the selected role, without disturbing
  other agents.

The dashboard cannot invoke gated roles, releases, deployments, remote database
access, or prod/preprod operations.

## Security

- Bind only to `127.0.0.1`; startup fails if configured otherwise.
- Generate an in-memory session token at startup and require it on every control
  request.
- Reject non-local `Origin` and `Host` headers.
- Validate project and skill against the loaded schedule, never shell input.
- Execute commands as argument arrays without a shell.
- Return scrubbed operational errors, never environment variables, credentials,
  complete command output, or Codex transcripts.
- Continue to respect the target repository's `AGENTS.md`.

## Failure Handling

Local monitoring remains available when GitLab is offline or `glab`
authentication has expired. The GitLab panel reports the degraded state and the
last successful refresh time.

A failed control request leaves the current state visible and returns a concise
error. History corruption is isolated to invalid lines; valid entries still
render. Atomic writes and the existing crash-safe role lock prevent a dashboard
request from introducing overlapping agent passes.

## Verification

Automated tests cover:

- history serialization, concurrent append, malformed records, and seven-day
  retention;
- schedule and `launchd` state interpretation;
- healthy no-op versus actionable warning classification;
- GitLab success, empty, unauthenticated, and unavailable responses;
- allowlisted controls and rejection of disabled or unknown roles;
- localhost, host/origin, session-token, and shell-injection protections;
- dashboard API responses and empty/error UI states;
- trigger non-overlap and stop/restart behavior through faked process adapters.

A local smoke check starts the server on an ephemeral port, loads the page, and
exercises read-only refreshes without contacting prod or preprod.

## Success Criteria

The feature is complete when the operator can run one command and, from a local
browser:

1. identify whether all seven enabled roles are running normally;
2. understand the latest warning or failure without opening a terminal;
3. inspect the previous seven days of bounded runs;
4. follow Pitcrew tickets and merge requests into GitLab;
5. safely trigger, stop, or restart one enabled role;
6. receive no exposed secrets, full transcripts, or remote-environment controls.
