# Codex runtime contract

Pitcrew runtime data lives under `${CODEX_HOME:-$HOME/.codex}/pitcrew`. Bootstrap a
run exactly as follows:

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
PITCREW_ROOT="$CODEX_HOME_DIR/pitcrew"
PROJECT="${PITCREW_PROJECT:-$(tr -d '\r\n' < "$PITCREW_ROOT/default.txt" 2>/dev/null)}"
CONFIG_DIR="$PITCREW_ROOT/$PROJECT"
CONFIG_FILE="$CONFIG_DIR/config.json"
STATE_DIR="$CONFIG_DIR/state"
```

Always resolve and validate the project before repository or provider work: it must be one
explicit configured project with a valid config and repository path. Create runtime
directories with owner-only permissions and keep state beneath that project.

This bootstrap is a logical environment/path mapping, not permission to traverse
those paths with shell reads during initial validation. Default-project and
repository resolution must use the hardened `scripts/pitcrew_config.py` helper
(`project` and `repo --project <project>`) or an equivalent descriptor-based
`O_NOFOLLOW` / no-symlink implementation. Reject symlinked runtime, project,
default, and config components and fail closed before invoking Codex.

After that validation, `bin/pitcrew-codex.sh` grants Codex access to the selected
project's runtime directory and configured repository through `--add-dir`. The
runtime directory lets skills persist local state; the repository grant lets
Git-backed worktrees update metadata such as `.git/FETCH_HEAD`. The
`implementer-run` role additionally uses Codex's `danger-full-access` sandbox
because macOS can reject Git metadata writes outside the Pitcrew workspace;
this exception is limited to the configured local repository and implementer
role. All other roles retain `workspace-write`.

## Execution boundary

Each invocation performs **one bounded pass**: select one eligible item, complete
only the documented read or write operations for that item, then return. A run must
preserve the caller's sandbox and approval policy; it must never widen either one.
Read the applicable `AGENTS.md` files before selecting work and obey the nearest
applicable instructions.

Cadence remains caller-controlled: a skill neither schedules itself nor changes a
scheduler's interval.

## State and no-ops

Scheduled runs perform a deterministic preflight before resolving the repository
or launching Codex. A project-scoped provider circuit-breaker state suppresses
repeat launches for 30 minutes after a summary reports an authentication failure.
The state is stored atomically under `state/provider-circuit.json` with owner-only
permissions. An active cooldown returns the structured no-op directly; it does not
consume model tokens. The cooldown is a cost-control guard, not an authentication
replacement: the first provider failure still reports the configured remediation.

Write state atomically: write a temporary sibling with restrictive permissions,
validate it, then rename it into place. Do not partially update a queue or retry an
external side effect after an uncertain result.

When there is no eligible work, return a **structured no-op** result rather than
inventing work:

```json
{
  "status": "noop",
  "reason": "no eligible item",
  "project": "getbill",
  "skill": "research-run",
  "next_action": "run again after the configured interval"
}
```

The result always includes `status`, `reason`, `project`, `skill`, and
`next_action`.
