# Running Pitcrew on Codex

Pitcrew is installed as a native Codex plugin. Skills are discovered under the
`pitcrew` namespace, runtime state belongs to Codex, and every invocation performs
one bounded pass.

## Install

From the fork checkout:

```bash
./bin/install-codex.sh getbill --profile getbill
```

The installer updates only the `pitcrew` entry in
`$HOME/.agents/plugins/marketplace.json`, links this checkout as the local plugin
source, validates the manifest, and preserves existing runtime configuration.
Run the exact `Refresh with:` command printed by the installer; it includes the
actual configured marketplace name. Start a new Codex thread after adding or
refreshing the plugin.

Available profiles:

```bash
./bin/configure.sh example --profile generic
./bin/configure.sh getbill --profile getbill
```

The GetBill profile selects `/Users/jo/Prog/getbill`, GitLab forge/tracker adapters,
the project `AGENTS.md`, Graphify references, and release autonomy `off`.

## Invoke

In a Codex thread:

```text
$pitcrew:research-run
```

For a manual, read-only architecture review:

```text
$pitcrew:architecture-run
```

For the manual-only review of work not yet merged to Preprod, use the local
dashboard's confirmed **Lancer la revue complète** button as the recommended
operator path for:

```text
$pitcrew:preprod-review-run
```

The direct manual CLI path is also supported:

```bash
./bin/pitcrew-codex.sh preprod-review-run getbill
```

Both paths use the locked, ephemeral local execution boundary. `--scheduled`,
`--coordinated-run`, and `--target` are refused.

It is read-only and never scheduled: no LaunchAgent, automatic trigger, GitLab
issue/comment/MR/merge, commit, branch, push, deploy, Preprod environment, or
database action is allowed. It is fixed to `gpt-5.6-sol` with reasoning effort
`xhigh`, captures remote `origin/preprod...origin/develop` SHAs and merge-base,
and excludes working-tree, untracked, and ignored content. The helper requires
every manifest file exactly once, including rename/deletion/copy/typechange/binary
and generated entries, plus cross-change synthesis, before it can report `ready`.
High/critical findings report `changes_required`; invalid coverage, policy, schema,
helper failure, or interruption reports `incomplete` and makes an old report stale.
Only private local manifest/result/report/live/history files are written; history
is limited to 10 reports and replaces the same captured SHA pair. The dashboard
Stop action SIGTERMs the tracked helper, while the global stop switch blocks a new
trigger.

Ask for project `getbill` and one bounded pass. For headless inspection:

```bash
./bin/pitcrew-codex.sh research-run getbill --dry-run
./bin/pitcrew-codex.sh architecture-run getbill --dry-run
```

The runner validates the project with `scripts/pitcrew_config.py`, rejects
symlinked or malformed runtime components, and passes repository/runtime access
through `--add-dir`. It preserves the caller's sandbox and approval policy.

## Scheduled tasks

A Codex scheduled task owns cadence. The skill never reschedules itself. Use prompts
like:

```text
Use $pitcrew:research-run for project getbill. Perform exactly one bounded pass,
respect AGENTS.md, and return a structured no-op when nothing is eligible.
```

```text
Use $pitcrew:reviewer-run for project getbill. Perform exactly one bounded,
review-oriented pass and preserve all GetBill approval gates.
```

Start with research and review, observe several runs, then enable other roles only
when their provider permissions and expected side effects are understood. Release
scheduling is disabled for GetBill. Every prod or preprod action requires a new
explicit approval and remote actions must not be chained.

See [scheduled task guidance](../references/SCHEDULED-TASKS.md).

`$pitcrew:architecture-run` has a weekly `604800`-second schedule and remains
available for a manual dashboard Trigger for human review. It uses `gpt-5.6-sol`
with high reasoning effort, scans one rotating area, and writes only local state: a
proposal ledger plus separate `architecture-state.json` coverage state; it never
writes repository code or creates tracker work.

## Providers and network access

Provider selection is configuration, not tool-name inference:

```json
{
  "providers": {
    "forge": "gitlab",
    "tracker": "gitlab"
  }
}
```

GitHub maps generic changes to pull requests; GitLab maps them to merge requests.
Linear is optional and is used only when `providers.tracker` is `linear`. If a
configured provider, identity, workspace, owner/group, repository, or operation
cannot be validated, Pitcrew fails closed with a structured no-op. It never falls
back to another account or provider.

Use supported Codex `sandbox_mode` and `approval_policy` settings in your normal
configuration. Network/provider access remains optional and should be granted by
the caller only for a pass that needs it. Pitcrew does not request a broader sandbox
from inside a skill.

See [provider selection](../references/PROVIDERS.md) and the
[Codex config example](../references/codex-config.example.toml).

## Runtime and migration

Runtime files are stored at:

```text
${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/
```

An existing upstream runtime can be migrated explicitly:

```bash
python3 scripts/pitcrew_config.py migrate --project getbill
```

Migration never silently replaces an existing destination. The canonical runtime
contract is [CODEX-RUNTIME.md](../references/CODEX-RUNTIME.md).

## GetBill operating boundary

For GetBill, Pitcrew must:

- re-read `/Users/jo/Prog/getbill/AGENTS.md`;
- preserve unrelated working-tree changes and stage only owned files;
- read the required security, accessibility, performance, SEO, schema, or
  infrastructure reference before changing that area;
- refresh Graphify after code changes;
- ask before every prod or preprod action;
- ask before migrations, database writes, mutating console commands, or rollback;
- never read secret files.

These rules are enforced by the profile and
[GetBill reference](../references/profiles/getbill.md), not by widening permissions.

## Troubleshooting

Validate configuration and inspect a dry run:

```bash
python3 scripts/pitcrew_config.py validate profiles/getbill.json
./bin/pitcrew-codex.sh reviewer-run getbill --dry-run
```

If discovery is stale, re-run the installer's printed `Refresh with:` command and
start a new thread. If a provider is unavailable, correct its configured
authentication rather than switching providers implicitly.
