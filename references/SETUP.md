# Project setup

Pitcrew reads one validated project configuration from
`${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/config.json`.

## Create a project

```bash
./bin/configure.sh example --profile generic
./bin/configure.sh getbill --profile getbill
```

Use the generic profile for a new integration. Use the GetBill profile for
`/Users/jo/Prog/getbill`; it supplies GitLab routing, project references, and
strict approval gates without enabling release autonomy.

Inspect one pass before enabling scheduled work:

```bash
./bin/pitcrew-codex.sh research-run getbill --dry-run
```

The equivalent discovered skill is `$pitcrew:research-run`.

## Required fields

```json
{
  "schema_version": 1,
  "project_name": "example",
  "providers": {
    "forge": "github",
    "tracker": "linear"
  },
  "repos": [
    {
      "name": "example-app",
      "path": "/absolute/path/to/example-app",
      "default_branch": "main",
      "lang": "ts",
      "tags": ["web"]
    }
  ],
  "release": {"autonomy": "off"}
}
```

- `project_name` is a safe single path component.
- `providers.forge` is `github` or `gitlab`.
- `providers.tracker` is `linear`, `github`, `gitlab`, or `none`.
- `repos[].path` must be a non-empty path without control characters; the runner
  requires it to resolve to an available directory before invoking Codex.
- `release.autonomy` defaults to `off`.

Provider-specific identities, teams, owners/groups, repositories, labels, and
states may be added beneath provider/role configuration as required. A skill must
return a structured no-op when its required capability is absent; it must never
guess or fall back to another provider.

## Optional role configuration

Role blocks such as `qa`, `researcher`, `manager`, `coverage`, `dev_verify`,
`slack`, repository `health`, and repository `release` enable their corresponding
skills. Omit a block to make the role exit cleanly when it has nothing configured.

Paths for findings and state should remain under the selected Codex runtime or an
explicit project-owned directory. Store no credentials in checked-in examples.

## Safety configuration

```json
{
  "release": {"autonomy": "off"},
  "safety": {
    "confirm_each_remote_action": ["prod", "preprod"],
    "allow_database_writes": false,
    "allow_destructive_git": false,
    "allow_secret_reads": false,
    "stage_only_owned_files": true,
    "worktree_on_dirty_checkout": false
  }
}
```

These values narrow a workflow; they never override repository `AGENTS.md` or a
caller approval policy. Pitcrew must still ask before every action covered by
repository policy.

## Provider setup

Read [PROVIDERS.md](PROVIDERS.md), then the selected provider reference:

- [GitHub with optional Linear tracker](providers/github-linear.md)
- [GitLab forge and/or tracker](providers/gitlab.md)

[LINEAR-ACCESS.md](LINEAR-ACCESS.md) applies only when
`providers.tracker=linear`. Tracker `none` means tracker-dependent roles return a
structured no-op.

## Migration

Import an upstream runtime explicitly:

```bash
python3 scripts/pitcrew_config.py migrate --project example
```

Migration refuses unsafe or existing destinations. Validate afterward:

```bash
python3 scripts/pitcrew_config.py validate \
  "${CODEX_HOME:-$HOME/.codex}/pitcrew/example/config.json"
```

## Scheduled execution

A task prompt must name one project, invoke one namespaced skill, and request one
bounded pass:

```text
Use $pitcrew:reviewer-run for project getbill. Perform one bounded pass.
```

Start with research/review tasks and leave `$pitcrew:releaser-run` unscheduled for
GetBill. See [SCHEDULED-TASKS.md](SCHEDULED-TASKS.md).
