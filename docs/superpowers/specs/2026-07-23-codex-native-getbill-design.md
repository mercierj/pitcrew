# Codex-native Pitcrew with a GetBill profile

## Status

Approved direction: preserve Pitcrew as a reusable upstream-derived project, make Codex its primary supported harness, and add a guarded GetBill profile.

## Goals

- Preserve the upstream MIT project history and make future upstream changes mergeable.
- Package Pitcrew as a supported Codex plugin rather than a collection of legacy custom-prompt symlinks.
- Keep the generic crew workflows reusable across repositories.
- Add an explicit GetBill profile that teaches the crew the repository's architecture, commands, safety rules, and verification workflow.
- Make unattended operation fail closed when credentials, tools, repository state, or permissions are missing.
- Keep production, preproduction, deployment, migration, database-write, secret, and destructive Git actions human-gated.

## Non-goals

- Replacing GetBill's `AGENTS.md`, existing Codex skills, or Superpowers workflows.
- Enabling automatic production releases.
- Building a new issue tracker or replacing GitLab with GitHub.
- Copying GetBill secrets or environment-specific credentials into Pitcrew.
- Making every upstream Claude-specific feature work through compatibility shims.

## Repository and upstream strategy

The canonical fork is `mercierj/pitcrew`. Its `origin` remote points to the fork and its `upstream` remote points to `fcarrar/pitcrew`.

Adaptation work lives on `codex-native-getbill` until reviewed. Upstream attribution and the MIT license remain intact. Changes should be organized so generic Codex improvements can be proposed upstream independently from the GetBill profile.

## Architecture

Pitcrew becomes a Codex plugin with this conceptual layout:

```text
pitcrew/
├── .codex-plugin/plugin.json
├── skills/
│   └── <crew-role>/SKILL.md
├── profiles/
│   ├── generic/
│   └── getbill/
├── scripts/
│   ├── install-or-update helpers
│   ├── configuration validation
│   └── one-pass runner
├── references/
├── tests/
└── docs/
```

The plugin manifest exposes the skill directory. Codex discovers installed crew skills using their native namespaced `$pitcrew:<skill>` identifiers. The legacy `~/.codex/prompts` installer is removed from the primary path and retained only as a clearly deprecated migration aid if compatibility testing shows it is still useful.

Runtime state defaults to `$CODEX_HOME/pitcrew/<project>`, with `$CODEX_HOME` defaulting to `~/.codex`. No primary Codex path writes into `~/.claude`. A migration command may copy an existing Pitcrew configuration from the old location after showing the source and destination and without overwriting existing state.

## Components

### Plugin manifest and marketplace metadata

Add a validated `.codex-plugin/plugin.json` and repository marketplace metadata suitable for local installation and testing. The plugin bundles skills and their references without requiring an MCP-backed app.

### Codex-native skills

Each crew role remains focused on one responsibility. Skill metadata is rewritten for reliable Codex triggering and `$` invocation. Instructions use Codex terminology and available tool surfaces:

- `AGENTS.md` for repository-local rules;
- installed skills for repeatable workflows;
- Codex collaboration tools only when delegation is explicitly allowed;
- scheduled tasks for supported desktop/web recurring workflows;
- `codex exec` for documented CLI or CI one-pass execution.

Skills must not assume Claude tool names, Claude command directories, `/loop`, or a specific Linear MCP binding identifier.

### Configuration and profiles

Generic configuration defines tracker, repository, validation, notification, health, and release capabilities without hard-coding a provider.

Profiles layer repository-specific defaults and constraints on top:

- `generic` contains conservative examples;
- `getbill` targets `/Users/jo/Prog/getbill` by default and declares GitLab, Symfony 6.4, PHP, Stimulus, Vite, MySQL, RTK, Graphify, and Playwright conventions.

Profile values never contain credentials. User/runtime configuration remains outside the repository.

### Provider adapters

Tracker and forge behavior is represented as explicit adapters instead of Linear/GitHub assumptions embedded throughout every skill.

The initial supported paths are:

- GitHub plus optional Linear, preserving upstream behavior;
- GitLab for GetBill merge requests and repository workflows.

When a configured adapter is unavailable, a crew member exits without mutating state and reports the missing capability. It must never silently fall back to a different workspace, organization, repository, or tracker.

### Scheduling and one-pass execution

Codex scheduled tasks are the documented primary recurring interface where available. Templates provide conservative example cadences and require an explicit project/profile selection.

The one-pass runner remains available for `codex exec`, local schedulers, or CI. It:

1. resolves `CODEX_HOME` and the selected project;
2. validates configuration and repository paths;
3. selects the namespaced plugin skill;
4. runs from the target repository;
5. grants only the required writable roots;
6. preserves the caller's sandbox and approval policy unless an explicitly named safe profile is selected.

The runner must not recommend `danger-full-access` as the default solution for network access.

## GetBill safety contract

The GetBill profile incorporates, but does not duplicate or override, the repository's active `AGENTS.md` and relevant installed skills.

Hard constraints:

- Ask before every production or preproduction action.
- Perform remote actions one at a time and reconfirm before the next action.
- Never read or print secret files or credential material.
- Never execute migrations, fixtures, schema writes, raw SQL writes, or mutating `app:*` commands without explicit approval.
- Never run destructive Git cleanup or revert unrelated changes.
- Stage only files created or changed by the active crew task.
- Do not create worktrees merely because the checkout is dirty.
- Keep tenant scoping, encrypted PII handling, webhook signature validation, and asynchronous webhook processing intact.
- Read the relevant GetBill project references before security, accessibility, performance, SEO, schema, infrastructure, modal, or background-job work.
- Read Graphify's report/wiki before architecture questions and rebuild Graphify after code changes.
- Prefer RTK for noisy commands and targeted native commands where exact output matters.

Release autonomy for GetBill defaults to `off`. No provided example may enable production or preproduction mutation.

## Workflow and data flow

```text
Codex scheduled task or explicit invocation
            |
            v
  Load plugin skill and project config
            |
            v
 Validate profile, repository, adapters, and permissions
            |
      +-----+------+
      | invalid    | valid
      v            v
 Report and stop   Read AGENTS.md and required project references
                         |
                         v
                 Perform one bounded crew pass
                         |
                         v
                 Write local state/artifacts
                         |
                         v
             External write only through its gate
```

Crew roles continue coordinating through durable issue/merge-request state and local ledgers, not direct agent-to-agent conversations. The GetBill profile maps the generic lifecycle to GitLab without changing the role boundaries.

## Error handling

- Missing config, unavailable adapter, unauthenticated CLI, unexpected repository, dirty conflicting files, or insufficient permission produces a structured no-op result.
- State writes use atomic replacement where practical so interrupted runs do not corrupt ledgers.
- External writes include stable idempotency markers or deduplication keys where the provider supports them.
- A failed notification never changes the underlying ticket or merge-request outcome.
- A partial provider outage never causes fallback to another environment.
- Ambiguous scope moves work to a human decision point rather than guessing.
- Existing user changes are preserved and reported when they prevent safe progress.

## Compatibility and migration

The conversion includes a compatibility audit of all thirteen upstream skills and scripts. Claude-only paths are classified as:

- replace with a native Codex surface;
- isolate behind an optional legacy adapter;
- remove when no safe Codex equivalent exists.

The old runtime configuration can be detected and migrated explicitly. Migration is idempotent, never overwrites an existing destination, and prints no secrets.

The generic GitHub/Linear mode remains supported to avoid turning the fork into a one-project codebase.

## Verification

Automated checks should cover behavior whose failure would make unattended operation unsafe or unusable:

- plugin manifest and marketplace validation;
- skill frontmatter and namespacing;
- installer/update idempotency;
- `CODEX_HOME` path resolution;
- configuration/profile schema validation;
- legacy configuration migration without overwrite;
- GetBill safety defaults;
- runner argument construction and sandbox preservation;
- provider selection and fail-closed behavior;
- absence of active Claude-only paths from the Codex-native workflow.

Shell scripts receive syntax checks. JSON and TOML examples are parsed. A dry-run smoke scenario installs the plugin into a temporary Codex home, selects the GetBill profile, resolves the repository, and stops before any external write.

Manual verification confirms that Codex can discover and explicitly invoke every bundled skill from a fresh session.

## Documentation

The README leads with Codex installation and usage, explains the plugin model, distinguishes scheduled tasks from `codex exec`, and documents the GetBill profile as an example of guarded project specialization.

Claude compatibility, if retained, is documented as secondary and must not constrain the native Codex design.

## Delivery criteria

The adaptation is complete when:

- the fork installs as a validated Codex plugin;
- all thirteen crew skills are discoverable and usable through native Codex skill invocation;
- no primary workflow depends on `~/.claude`, `~/.codex/prompts`, Claude tool names, or `/loop`;
- a GetBill dry run loads current repository instructions and refuses gated actions without approval;
- generic GitHub/Linear operation remains available;
- targeted automated and manual verification passes;
- documentation describes installation, migration, safe configuration, scheduling, and GetBill usage accurately.
