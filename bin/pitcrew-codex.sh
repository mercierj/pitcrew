#!/usr/bin/env bash
# Run one bounded Pitcrew skill through Codex without changing its safety policy.

set -euo pipefail
umask 077

readonly SKILLS=(
  coverage-run dev-verify-run implementer-run investigate-run manager-run ops-run
  qa-run releaser-run research-run reviewer-run stale-sweep unblock validator-run
)

usage() {
  echo "usage: pitcrew-codex.sh <skill> [project] [--dry-run] [--scheduled]" >&2
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
while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      ;;
    --scheduled)
      SCHEDULED=true
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
CONFIG="$RUNTIME_ROOT/$PROJECT/config.json"
REPO="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" repo --project "$PROJECT")"
MODEL="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" model --project "$PROJECT" --skill "$SKILL")"
[[ -n "$REPO" && -d "$REPO" ]] || {
  echo "pitcrew-codex: configured repository is unavailable: $REPO" >&2
  exit 2
}

PROMPT="Use \$pitcrew:$SKILL for project '$PROJECT'. Read $CONFIG, perform exactly one bounded pass in $REPO, then stop. Fail closed when a configured provider or permission is unavailable."

if "$DRY_RUN"; then
  printf '%s\n' "cd=$REPO" "model=$MODEL" "prompt=$PROMPT"
  exit 0
fi

CODEX_ARGS=(
  exec
  --cd "$REPO"
  --add-dir "$RUNTIME_ROOT/$PROJECT"
  --model "$MODEL"
  --json
)

if "$SCHEDULED"; then
  LOCK_ROOT="${PITCREW_LOCK_ROOT:-$RUNTIME_ROOT/$PROJECT/locks}"
  SUMMARY_DIR="$RUNTIME_ROOT/$PROJECT/logs"
  SUMMARY_FILE="$SUMMARY_DIR/$SKILL.last.txt"
  HISTORY_FILE="$RUNTIME_ROOT/$PROJECT/history.jsonl"
  LOCK_FILE="$LOCK_ROOT/$SKILL.lock"
  mkdir -p "$LOCK_ROOT" "$SUMMARY_DIR"
  chmod 700 "$LOCK_ROOT" "$SUMMARY_DIR"
  if [[ -e "$SUMMARY_FILE" ]]; then
    chmod 600 "$SUMMARY_FILE"
  fi
  CODEX_ARGS+=(
    --ephemeral
    --sandbox workspace-write
    -c sandbox_workspace_write.network_access=true
    -c approval_policy='"never"'
    --color never
    --output-last-message "$SUMMARY_FILE"
  )
fi

CODEX_ARGS+=("$PROMPT")

if "$SCHEDULED"; then
  python3 "$REPO_ROOT/scripts/pitcrew_locked_exec.py" \
    --lock-file "$LOCK_FILE" \
    --project "$PROJECT" \
    --skill "$SKILL" \
    --summary-file "$SUMMARY_FILE" \
    --history-file "$HISTORY_FILE" \
    -- "${CODEX_BIN:-codex}" "${CODEX_ARGS[@]}"
  chmod 600 "$SUMMARY_FILE" 2>/dev/null || true
else
  exec "${CODEX_BIN:-codex}" "${CODEX_ARGS[@]}"
fi
