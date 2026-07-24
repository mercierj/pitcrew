#!/usr/bin/env bash
# Run one bounded Pitcrew skill through Codex without changing its safety policy.

set -euo pipefail

readonly SKILLS=(
  coverage-run dev-verify-run implementer-run investigate-run manager-run ops-run
  qa-run releaser-run research-run reviewer-run stale-sweep unblock validator-run
)

usage() {
  echo "usage: pitcrew-codex.sh <skill> [project] [--dry-run]" >&2
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
while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=true
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
[[ -n "$REPO" && -d "$REPO" ]] || {
  echo "pitcrew-codex: configured repository is unavailable: $REPO" >&2
  exit 2
}

PROMPT="Use \$pitcrew:$SKILL for project '$PROJECT'. Read $CONFIG, perform exactly one bounded pass in $REPO, then stop. Fail closed when a configured provider or permission is unavailable."

if "$DRY_RUN"; then
  printf '%s\n' "cd=$REPO" "prompt=$PROMPT"
  exit 0
fi

exec "${CODEX_BIN:-codex}" exec \
  --cd "$REPO" \
  --add-dir "$RUNTIME_ROOT/$PROJECT" \
  "$PROMPT"
