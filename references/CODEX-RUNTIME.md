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

After that validation, `bin/pitcrew-codex.sh` deliberately grants Codex access only
to the selected project's runtime directory through `--add-dir`, so the skill can
read its configuration and atomically persist local state. This boundary preserves
the caller's sandbox and approval policy; it does not claim to pin filesystem
objects across the process handoff.

## Execution boundary

Each invocation performs **one bounded pass**: select one eligible item, complete
only the documented read or write operations for that item, then return. A run must
preserve the caller's sandbox and approval policy; it must never widen either one.
Read the applicable `AGENTS.md` files before selecting work and obey the nearest
applicable instructions.

Cadence remains caller-controlled: a skill neither schedules itself nor changes a
scheduler's interval.

## State and no-ops

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
