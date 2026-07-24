# Linear tracker access

This reference applies only when `providers.tracker=linear`. For GitHub or GitLab
issues, use the matching forge provider reference. For tracker `none`, any
tracker-dependent role returns a structured no-op.

## Binding

Before any tracker operation:

1. read the validated project configuration;
2. select the configured Linear connector by capability, not by a remembered tool
   prefix;
3. validate the configured workspace and team;
4. use that same validated binding for the whole bounded pass.

If the available connector is authenticated to another workspace, treat the
configured tracker as unavailable. Never fall back to another workspace, team,
provider, or account.

## Capabilities

Skills use generic tracker capabilities such as:

- list eligible work;
- inspect an item and its comments;
- claim or update an item;
- add a comment;
- close lifecycle.

Resolve these through [providers/github-linear.md](providers/github-linear.md).
Provider-specific tool names belong in that adapter, not in acting skills.

## Degraded mode

When the configured Linear capability is unavailable:

- do not claim that a write succeeded;
- do not take an external side effect whose safety depends on a fresh tracker read;
- return the structured no-op from [CODEX-RUNTIME.md](CODEX-RUNTIME.md), naming
  `linear` and the missing capability;
- let a later caller-controlled pass retry after authentication or configuration is
  repaired.

A local state snapshot may support diagnostic, read-only reporting, but it is never
a competing source of truth and is never replayed as writes.

## Scheduled tasks

A scheduled task invokes a namespaced skill and one project:

```text
Use $pitcrew:manager-run for project example. Perform one bounded pass.
```

Grant connector/network access only when that role needs Linear. Do not broaden the
sandbox from inside the skill.
