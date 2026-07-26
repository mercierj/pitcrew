# Pitcrew for Codex

Pitcrew is a Codex-native plugin for running a small software-delivery crew as safe,
one-pass workflows. Each skill selects at most one bounded unit of work, follows the
target repository's `AGENTS.md`, preserves the caller's sandbox and approval policy,
and returns without scheduling itself.

This fork adds explicit GitHub/GitLab provider adapters and a hardened **GetBill
profile**. Claude compatibility is retained as a secondary harness; Codex is the
primary installation, runtime, and documentation path.

## The crew

| Skill | Role |
|---|---|
| `$pitcrew:research-run` | Record one grounded codebase finding. |
| `$pitcrew:security-run` | Record one grounded security proposal. |
| `$pitcrew:product-discovery-run` | Record one feature proposal for dashboard approval. |
| `$pitcrew:architecture-run` | Scan one rotating repository area and record high-confidence architecture proposals. |
| `$pitcrew:preprod-review-run` | Manually review the unmerged Preprod delta before a human promotion decision. |
| `$pitcrew:qa-run` | Exercise one configured QA flow. |
| `$pitcrew:manager-run` | Pace findings into the configured tracker. |
| `$pitcrew:implementer-run` | Claim and implement one eligible item. |
| `$pitcrew:bugfixer-run` | Reproduce and fix one eligible bug through human-gated delivery. |
| `$pitcrew:reviewer-run` | Review one open change. |
| `$pitcrew:validator-run` | Validate one change locally. |
| `$pitcrew:unblock` | Surface one blocked decision to a human. |
| `$pitcrew:investigate-run` | Investigate one blocker without code writes. |
| `$pitcrew:coverage-run` | Identify one meaningful test-flow gap. |
| `$pitcrew:dev-verify-run` | Verify one landed change on dev. |
| `$pitcrew:ops-run` | Observe one configured health surface. |
| `$pitcrew:releaser-run` | Prepare or release one eligible change within explicit gates. |
| `$pitcrew:stale-sweep` | Reconcile one stale lifecycle item. |

## Install from this fork

```bash
git clone git@github.com:mercierj/pitcrew.git
cd pitcrew
./bin/install-codex.sh getbill --profile getbill
```

The installer registers this checkout in the personal Codex marketplace, validates
the plugin, and initializes runtime configuration only when it does not already
exist. Run the exact `Refresh with:` command it prints; that command uses the
marketplace's configured name. The installer never installs legacy prompt files
and never overwrites an existing GetBill configuration.

Initialize another project explicitly:

```bash
./bin/configure.sh example --profile generic
./bin/configure.sh getbill --profile getbill
```

Runtime configuration and state live under
`${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/`.

## Run one bounded pass

Invoke a discovered skill in Codex:

```text
$pitcrew:research-run
```

Or inspect the exact headless invocation without running Codex:

```bash
./bin/pitcrew-codex.sh research-run getbill --dry-run
```

The runner validates the selected project and repositories before starting Codex.
It adds only the selected runtime directory and repository paths, and it does not
widen the configured sandbox or approval policy.

## Codex scheduled tasks

Codex scheduled tasks should request one skill, one project, and one bounded pass.

### Durable ticket runs

Ticket-targeted work uses a durable local queue at
`${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>/runs.sqlite3`. Each role runs up
to **3** tickets concurrently by default; `execution.max_concurrent_per_skill`
can optionally override that limit per role. A fourth ticket remains in FIFO
order until a slot is released. Re-submitting the same ticket returns its
existing queued or running run instead of starting another worker.

The dashboard keeps queued, running, and failed ticket state across reloads.
Queued/running tickets cannot be launched again; a failed ticket requires an
explicit manual retry. Terminal rows are retained for seven days.

**Tout arrêter** cancels active coordinated workers and suspends queued tickets.
`resume-all` (**Réactiver**) resumes only those queued tickets; it does not
immediately launch a new scheduled pass.
Start with read-only or review-oriented roles:

```text
Use $pitcrew:research-run for project getbill. Perform one bounded pass.
Use $pitcrew:reviewer-run for project getbill. Perform one bounded pass.
Use $pitcrew:architecture-run for project getbill. Perform one bounded read-only pass.
```

Observe the first runs before enabling implementation workflows. Delivery roles
(`manager-run`, `implementer-run`, `reviewer-run`, `validator-run`,
`investigate-run`, and `unblock`) are event-driven: dashboard actions and
validated lifecycle events admit them through the durable coordinator. They
have no polling interval or restartable LaunchAgent; `stale-sweep` remains the
periodic recovery path for missed or externally changed lifecycle state. Do not schedule
`$pitcrew:releaser-run` for GetBill: release autonomy is off and every prod or
preprod action requires a fresh explicit approval.

`$pitcrew:bugfixer-run` runs every 15 minutes when the configured native issue
tracker, bugfixer risk policy, repository, and labels are all available. Its
exclusive route is `agent + bug + todo`; `implementer-run excludes bugs` in
both todo and review. A valid red reproduction is mandatory before production
code changes, and the same reproduction must turn green. Merge still requires
`$pitcrew:reviewer-run`, `$pitcrew:validator-run`, human `go`, and green required
CI checks.

See [the Codex guide](docs/CODEX.md) and
[scheduled-task templates](references/SCHEDULED-TASKS.md).

`$pitcrew:architecture-run` is scheduled weekly (604800 seconds) and can also be
started manually from the local dashboard. It scans exactly one rotating repository
area per pass and records at most three high-confidence, structured architecture
proposals. It is read-only for both the repository and tracker: it writes no code,
MR/PR, issue, merge, or deploy. Its only writes are local: the proposal ledger for
human review and the separate `architecture-state.json` coverage state.

For a persistent local GetBill installation on macOS, install the safe core
loops with:

```bash
python3 bin/pitcrew-schedule.py install --project getbill
python3 bin/pitcrew-schedule.py status --project getbill
```

### Manual review before Preprod

Use the **Lancer la revue complète** dashboard button as the recommended
operator path for `$pitcrew:preprod-review-run`; it requires confirmation. A
direct manual CLI launch is also supported:

```bash
./bin/pitcrew-codex.sh preprod-review-run getbill
```

Both paths use the locked, ephemeral local execution boundary. This role is
strictly manual-only: it is never scheduled, has no LaunchAgent, interval,
restart, or automatic invocation. `--scheduled`, `--coordinated-run`, and
`--target` are refused. The global stop switch blocks its trigger; **Arrêter la
revue** sends SIGTERM to the tracked local helper and an interruption makes any
prior result stale.

The fixed, non-configurable runtime is `gpt-5.6-sol` with reasoning effort
`xhigh`. It captures remote `origin/preprod...origin/develop` SHAs and their
merge-base before analysis. The working tree, untracked files, and ignored files
are excluded. Every manifest entry is reviewed exactly once, including deletion,
rename, copy, typechange, binary, and generated entries, followed by one
cross-change synthesis.

It is read-only: no GitLab issue, comment, MR, merge, commit, branch, push,
deploy, Preprod environment, or database action is permitted. Its only writes
are private local manifest, result, report, live-status, and bounded-history
files. Only the helper can produce `ready` / **PRÊT**. It returns
`changes_required` / **CORRECTIONS REQUISES** for high or critical findings, and
`incomplete` / **INCOMPLET** for failed policy, schema, coverage, helper, or
interrupted runs. Local history retains at most 10 reports and replaces a report
for the same captured SHA pair.

To stop all scheduled agents and block future manual or scheduled runs before
they can invoke Codex, use the persistent project stop switch:

```bash
python3 bin/pitcrew-schedule.py stop-all --project getbill
python3 bin/pitcrew-schedule.py resume-all --project getbill
```

The dashboard exposes the same safety switch as **Tout arrêter** and requires
confirmation. While stopped, it displays **Exécutions bloquées** and disables
per-agent controls. Resuming is explicit and reinstalls schedules without
starting an immediate pass.

The GitLab panel also lists open merge requests with their source and target
branches, author, and pipeline status. **Fusionner et supprimer la branche**
merges the selected MR even when its pipeline is not green, then deletes the
source branch after a successful merge. Merge requests targeting `preprod` or
`prod` remain blocked and require the release workflow.

The installer deliberately omits release, production, and not-yet-configured
QA/ops roles. `unblock` is scheduled, but remains human-gated: its automatic
pass only publishes a pending question and never chooses an answer. Each
installed job performs one bounded pass,
uses a non-overlapping per-role lock, and returns. Scheduled transcripts are
discarded; only the latest bounded role summary is retained.

## Local operator dashboard

Start the dashboard manually when you need an operational view:

```bash
cd /Users/jo/Prog/pitcrew
./bin/pitcrew-dashboard
```

Open `http://127.0.0.1:8765`; the local server runs until you stop it with
Ctrl-C. **Pilotage** shows human decisions first and groups the configured native
forge work into `À faire`, `En cours`, `En revue`, and `Bloqué`; it follows the
configured provider for GitHub and GitLab, and work completed today is folded
below the board. Eligible `agent + bug + todo` work exposes **Corriger ce bug**.
Queued and running state comes from the durable ticket coordinator, not from
provider prose.

The provider-neutral work panel reads `/api/forge-work`. A GitHub or GitLab
failure does not break local agent status, history, or decisions: the provider panel degrades independently, retains its last successful data, and marks the remote source as degraded while local monitoring and controls continue to work.
The /api/gitlab compatibility alias is temporary and available only for a GitLab configuration.

**Agents** contains compact operational rows with expandable model, usage,
trigger mode, and control details. Event-driven delivery roles show no cadence
or restart schedule. **Historique** keeps the seven-day run log and
filters.

For a periodic enabled agent, **Trigger** starts one bounded pass immediately
(the per-agent lock prevents overlap), **Stop** stops its current pass and
unloads its schedule, and **Restart** installs or reloads its schedule.
Event-driven delivery roles are admitted by the durable coordinator instead of a
LaunchAgent schedule. Release, prod, and
remote Preprod environment/deployment controls are explicitly absent. The sole
exception is the local, read-only, manual-only Preprod review panel; it cannot
deploy or perform remote environment actions. The status CLI remains available:

```bash
python3 bin/pitcrew-schedule.py status --project getbill
```

### Models and usage

The dashboard shows each agent's configured `gpt-5.6-sol`, `gpt-5.6-terra`, or
`gpt-5.6-luna` model, plus last-run and rolling seven-day measured usage. The
amount is API-equivalent metering rather than a subscription charge. See the
[model catalogue, controls, and pricing assumptions](references/SCHEDULED-TASKS.md).

### Scheduled token controls

Each `agents.<role>` entry pins `model`, `reasoning_effort`, and `routing_mode`.
Scheduled runs therefore do not inherit the operator's global reasoning setting.

Before Codex starts, queue-backed roles run a read-only eligibility probe. Only a
confirmed `empty` decision suppresses Codex. Provider errors, malformed
responses, and roles without a deterministic Phase A probe return `unavailable`
and preserve the existing workflow launch.

`routing_mode: observe` records routing evidence but does not change the
configured execution model.

## State and providers

The generic lifecycle is:

```text
todo -> processing -> review -> done
           |            |
           +-> blocked <-+
```

Provider bindings are explicit in `config.json`:

```json
{
  "providers": {
    "forge": "gitlab",
    "tracker": "gitlab"
  }
}
```

Forge choices are `github` and `gitlab`; tracker choices are `linear`, `github`,
`gitlab`, and `none`. Pitcrew validates the configured host, workspace, owner/group,
and repository and never falls back to a different provider. Generic workflow terms
map to pull requests on GitHub and merge requests on GitLab. Details live in
[provider selection](references/PROVIDERS.md) and
[crew topology](references/TOPOLOGY.md).

### Native GitHub Issues

A native GitHub issue tracker requires the matched
`providers.forge=github` and `providers.tracker=github` pair plus an explicit,
reviewed binding:

```bash
./bin/configure.sh example --profile generic
$EDITOR /absolute/path/github-binding.json
./bin/configure.sh bind-github example --binding /absolute/path/github-binding.json
python3 scripts/pitcrew_config.py validate \
  "${CODEX_HOME:-$HOME/.codex}/pitcrew/example/config.json"
```

The binding file contains the complete reviewed `github` object documented by
the schema example. Pitcrew does not infer a host, identity, owner, repository,
label, or state from `gh auth`, Git metadata, or the current directory.
`bugfixer-run remains disabled` until its lifecycle configuration supplies and
validates the sensitivity policy.

## Safety and GetBill

Autonomy is explicit, bounded, and fail-closed:

- repository `AGENTS.md` instructions are authoritative;
- every run handles at most one selected item;
- missing configuration or provider capabilities produce a structured no-op;
- unrelated working-tree changes are preserved;
- destructive Git, secret reads, and database writes are disabled by default;
- prod and preprod actions require approval and are never chained.

The GetBill profile resolves `/Users/jo/Prog/getbill`, uses GitLab for forge and
tracker operations, requires the project security/accessibility/performance/schema
references for their matching areas, and refreshes Graphify after code changes.
Release autonomy remains `off`.

## Migrating an upstream Claude runtime

Migrate a legacy project without overwriting an existing Codex config:

```bash
python3 scripts/pitcrew_config.py migrate --project getbill
```

The migration is explicit, rejects unsafe path shapes, and preserves the legacy
runtime as its own compatibility source.

## Secondary Claude compatibility

The upstream Claude installer and `.claude-plugin` metadata remain for contributors
who need the original harness. They are not part of the Codex installation path,
and Codex runtime state is never shared implicitly with that harness.

## Development

Run the complete deterministic verification:

```bash
bash tests/run.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License and attribution

MIT, see [LICENSE](LICENSE). This fork is derived from
[fcarrar/pitcrew](https://github.com/fcarrar/pitcrew); original authorship and
license metadata are preserved.
