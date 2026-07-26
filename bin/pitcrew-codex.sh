#!/usr/bin/env bash
# Run one bounded Pitcrew skill through Codex without changing its safety policy.

set -euo pipefail
umask 077

readonly SKILLS=(
  coverage-run dev-verify-run implementer-run investigate-run manager-run ops-run
  qa-run releaser-run research-run reviewer-run stale-sweep unblock validator-run
  product-discovery-run security-run
)

usage() {
  echo "usage: pitcrew-codex.sh <skill> [project] [--target <ticket>] [--dry-run] [--scheduled] [--coordinated-run <uuid>]" >&2
}

is_allowed_skill() {
  local candidate="$1"
  local skill
  for skill in "${SKILLS[@]}"; do
    [[ "$candidate" == "$skill" ]] && return 0
  done
  return 1
}

SKILL="${1:-}"
[[ -n "$SKILL" ]] || { usage; exit 2; }
is_allowed_skill "$SKILL" || {
  echo "pitcrew-codex: unsupported skill: $SKILL" >&2
  exit 2
}
shift

PROJECT=""
DRY_RUN=false
SCHEDULED=false
TARGET=""
COORDINATED_RUN=""
while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      ;;
    --scheduled)
      SCHEDULED=true
      ;;
    --target)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      TARGET="$1"
      ;;
    --coordinated-run)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      COORDINATED_RUN="$1"
      ;;
    --*)
      echo "pitcrew-codex: unknown argument: $1" >&2
      exit 2
      ;;
    *)
      [[ -z "$PROJECT" ]] || { usage; exit 2; }
      PROJECT="$1"
      ;;
  esac
  shift
done

CODEX_HOME_DIR="${CODEX_HOME:-${HOME:?HOME or CODEX_HOME is required}/.codex}"
RUNTIME_ROOT="$CODEX_HOME_DIR/pitcrew"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "$PROJECT" ]]; then
  PROJECT="$(PITCREW_PROJECT="$PROJECT" python3 "$REPO_ROOT/scripts/pitcrew_config.py" project)"
else
  PROJECT="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" project)"
fi
if [[ -n "$COORDINATED_RUN" && "$SCHEDULED" != true ]]; then
  echo "pitcrew-codex: --coordinated-run requires --scheduled" >&2
  exit 2
fi
if ! EXECUTION_STATE="$(python3 "$REPO_ROOT/scripts/pitcrew_runtime_state.py" status --project "$PROJECT")"; then
  echo "pitcrew-codex: execution state is unavailable" >&2
  exit 2
fi
if [[ "$EXECUTION_STATE" == "stopped" ]]; then
  printf '%s\n' '{"status":"noop","reason":"global stop is active"}'
  exit 0
fi
CONFIG="$RUNTIME_ROOT/$PROJECT/config.json"
# Preflight belongs to the public entry point: a noop must not leave a claimed
# coordinated row behind.  The internal worker has already passed this gate.
if "$SCHEDULED" && [[ -z "$COORDINATED_RUN" ]]; then
  PREFLIGHT="$(python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" check --project "$PROJECT" --skill "$SKILL")" || {
    echo "pitcrew-codex: preflight is unavailable" >&2
    exit 2
  }
  if [[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["decision"])' <<<"$PREFLIGHT")" == "noop" ]]; then
    printf '%s\n' "$PREFLIGHT" | python3 -c '
import json, sys
value = json.load(sys.stdin)
value["status"] = "noop"
value["next_action"] = value.get("next_action", "retry after the provider cooldown")
value.pop("decision", None)
print(json.dumps(value, separators=(",", ":")))
'
    exit 0
  fi
fi

# The public scheduled entry point only admits work.  The dispatcher owns
# capacity and starts a separate, already-claimed coordinated worker.
if "$SCHEDULED" && [[ -z "$COORDINATED_RUN" ]] && [[ "$DRY_RUN" != true ]]; then
  DISPATCH_ARGS=(
    python3 "$REPO_ROOT/scripts/pitcrew_run_dispatcher.py" enqueue
    --project "$PROJECT"
    --skill "$SKILL"
  )
  if [[ -n "$TARGET" ]]; then
    DISPATCH_ARGS+=(--target "$TARGET")
  fi
  exec "${DISPATCH_ARGS[@]}"
fi

RUN_DB=""
if [[ -n "$COORDINATED_RUN" ]]; then
  RUN_DB="$RUNTIME_ROOT/$PROJECT/runs.sqlite3"
  if ! python3 -c '
import sys, uuid
from pathlib import Path
sys.path.insert(0, sys.argv[6])
from scripts.pitcrew_run_store import RunStore, RunStoreError

database, project, skill, target, run_id, _repo_root = sys.argv[1:]
try:
    uuid.UUID(run_id)
    row = RunStore(Path(database)).get(run_id)
except (ValueError, RunStoreError):
    raise SystemExit(1)
if row is None or row["project"] != project or row["skill"] != skill:
    raise SystemExit(1)
if (row["target"] or "") != target or row["state"] != "running":
    raise SystemExit(1)
' "$RUN_DB" "$PROJECT" "$SKILL" "$TARGET" "$COORDINATED_RUN" "$REPO_ROOT"; then
    echo "pitcrew-codex: coordinated run is invalid" >&2
    exit 2
  fi
fi
MODEL="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" model --project "$PROJECT" --skill "$SKILL")"
REPO="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" repo --project "$PROJECT")"
[[ -n "$REPO" && -d "$REPO" ]] || {
  echo "pitcrew-codex: configured repository is unavailable: $REPO" >&2
  exit 2
}

PROMPT="Use \$pitcrew:$SKILL for project '$PROJECT'. Read $CONFIG, perform exactly one bounded pass in $REPO, then stop. The Pitcrew coverage helper is at $REPO_ROOT/scripts/research_coverage.py; use it when the research skill requires coverage rotation. Fail closed when a configured provider or permission is unavailable."
if [[ -n "$TARGET" ]]; then
  PROMPT+=" Operate on exactly this directed target: $TARGET. Validate it with references/DIRECTED-TARGET.md before any provider lookup."
fi
if [[ -n "$COORDINATED_RUN" ]]; then
  PROMPT+=" This execution is coordinated as PITCREW_RUN_ID=$COORDINATED_RUN. Before the first tracker mutation or checkout write, bind the selected canonical ticket with: python3 $REPO_ROOT/scripts/pitcrew_run_dispatcher.py bind-target --project $PROJECT --run-id $COORDINATED_RUN --target <canonical-url>. If binding reports a conflict, select another eligible ticket or return a structured no-op without mutating the provider."
fi
if "$SCHEDULED" && [[ "$SKILL" == "unblock" ]]; then
  PROMPT+=" This is an unattended scheduled run. When a human decision is required, do not leave the question only in the final response: atomically persist the exact pending question, choices, ticket context, and status=blocked in $RUNTIME_ROOT/$PROJECT/unblock-state.json as required by skills/unblock/SKILL.md, then stop. The dashboard reads that file and cannot read this log."
fi

if "$DRY_RUN"; then
  printf '%s\n' "cd=$REPO" "model=$MODEL" "prompt=$PROMPT"
  exit 0
fi

CODEX_ARGS=(
  exec
  --cd "$REPO"
  --add-dir "$RUNTIME_ROOT/$PROJECT"
  --add-dir "$REPO_ROOT"
  --add-dir "$REPO"
  --model "$MODEL"
  --json
)

# Implementer must update the configured repository's Git metadata to create
# isolated worktrees. The other roles retain the normal workspace boundary.
SANDBOX_MODE="workspace-write"
if [[ "$SKILL" == "implementer-run" ]]; then
  SANDBOX_MODE="danger-full-access"
fi

if "$SCHEDULED"; then
  LOCK_ROOT="${PITCREW_LOCK_ROOT:-$RUNTIME_ROOT/$PROJECT/locks}"
  SUMMARY_DIR="$RUNTIME_ROOT/$PROJECT/logs"
  LIVE_DIR="$RUNTIME_ROOT/$PROJECT/live"
  HISTORY_FILE="$RUNTIME_ROOT/$PROJECT/history.jsonl"
  if [[ -n "$COORDINATED_RUN" ]]; then
    LOCK_FILE="$LOCK_ROOT/runs/$COORDINATED_RUN.lock"
    SUMMARY_FILE="$SUMMARY_DIR/runs/$COORDINATED_RUN.last.txt"
    LIVE_FILE="$LIVE_DIR/runs/$COORDINATED_RUN.json"
  else
    LOCK_FILE="$LOCK_ROOT/$SKILL.lock"
    SUMMARY_FILE="$SUMMARY_DIR/$SKILL.last.txt"
    LIVE_FILE="$LIVE_DIR/$SKILL.json"
  fi
  mkdir -p "$LOCK_ROOT" "$SUMMARY_DIR" "$LIVE_DIR"
  if [[ -n "$COORDINATED_RUN" ]]; then
    mkdir -p "$(dirname "$LOCK_FILE")" "$(dirname "$SUMMARY_FILE")" "$(dirname "$LIVE_FILE")"
  fi
  chmod 700 "$LOCK_ROOT" "$SUMMARY_DIR"
  if [[ -e "$SUMMARY_FILE" ]]; then
    chmod 600 "$SUMMARY_FILE"
  fi
  CODEX_ARGS+=(
    --ephemeral
    --sandbox "$SANDBOX_MODE"
    -c sandbox_workspace_write.network_access=true
    -c approval_policy='"never"'
    --color never
    --output-last-message "$SUMMARY_FILE"
  )
fi

CODEX_ARGS+=("$PROMPT")

if "$SCHEDULED"; then
  set +e
  LOCKED_ARGS=(
    python3 "$REPO_ROOT/scripts/pitcrew_locked_exec.py"
    --lock-file "$LOCK_FILE"
    --project "$PROJECT"
    --skill "$SKILL"
    --model "$MODEL"
    --summary-file "$SUMMARY_FILE"
    --live-file "$LIVE_FILE"
    --history-file "$HISTORY_FILE"
  )
  if [[ -n "$COORDINATED_RUN" ]]; then
    LOCKED_ARGS+=(--run-db "$RUN_DB" --run-id "$COORDINATED_RUN")
  fi
  LOCKED_ARGS+=(-- "${CODEX_BIN:-codex}" "${CODEX_ARGS[@]}")
  if [[ -n "$COORDINATED_RUN" ]]; then
    PITCREW_RUN_ID="$COORDINATED_RUN" "${LOCKED_ARGS[@]}"
  else
    "${LOCKED_ARGS[@]}"
  fi
  EXIT_CODE=$?
  set -e
  if [[ -f "$SUMMARY_FILE" ]] && rg -qi 'authentication|invalid_grant|oauth grant' "$SUMMARY_FILE"; then
    python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" record-provider-failure \
      --project "$PROJECT" --reason "provider authentication failure" >/dev/null || true
  elif [[ -f "$SUMMARY_FILE" ]] && rg -qi 'no eligible item|nothing missing|no permissions required' "$SUMMARY_FILE"; then
    python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" record-noop \
      --project "$PROJECT" --skill "$SKILL" --reason "no eligible item" >/dev/null || true
  fi
  chmod 600 "$SUMMARY_FILE" 2>/dev/null || true
  if [[ -n "$COORDINATED_RUN" ]]; then
    python3 "$REPO_ROOT/scripts/pitcrew_run_dispatcher.py" drain --project "$PROJECT" >/dev/null || true
  fi
  exit "$EXIT_CODE"
else
  exec "${CODEX_BIN:-codex}" "${CODEX_ARGS[@]}"
fi
