#!/usr/bin/env bash
# Run one bounded Pitcrew skill through Codex without changing its safety policy.

set -euo pipefail
umask 077

readonly SKILLS=(
  coverage-run dev-verify-run implementer-run bugfixer-run investigate-run manager-run ops-run
  architecture-run qa-run releaser-run research-run reviewer-run stale-sweep unblock validator-run
  product-discovery-run preprod-review-run security-run
)

# macOS currently ignores workspace-write network access for Codex subprocesses.
# These roles call the configured forge/tracker from inside the worker, so they
# need the network-capable sandbox. Read-only/local roles retain workspace-write.
readonly NETWORKED_SKILLS=(
  coverage-run dev-verify-run implementer-run bugfixer-run investigate-run manager-run
  ops-run releaser-run reviewer-run stale-sweep unblock validator-run
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

uses_provider_network() {
  local candidate="$1"
  local skill
  for skill in "${NETWORKED_SKILLS[@]}"; do
    [[ "$candidate" == "$skill" ]] && return 0
  done
  return 1
}

release_is_armed() {
  python3 -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
raise SystemExit(0 if config.get("release", {}).get("autonomy", "off") != "off" else 1)
' "$CONFIG"
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
TARGET_SOURCE=""
GATE_DECISION=""
GATE_REASON=""
GATE_FINGERPRINT=""
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
    --target-source)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      TARGET_SOURCE="$1"
      ;;
    --gate-decision)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      GATE_DECISION="$1"
      ;;
    --gate-reason)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      GATE_REASON="$1"
      ;;
    --gate-fingerprint)
      shift
      [[ $# -gt 0 && "$1" != --* ]] || { usage; exit 2; }
      GATE_FINGERPRINT="$1"
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

unset PITCREW_RUN_ID

if [[ "$SKILL" == "preprod-review-run" ]] && { "$SCHEDULED" || [[ -n "$COORDINATED_RUN" || -n "$TARGET" ]]; }; then
  echo "pitcrew-codex: preprod-review-run is manual-only; scheduled, coordinated, and directed execution are refused" >&2
  exit 2
fi

# Preprod review is deliberately manual-only, but it still needs the private
# lock, live marker, history and ephemeral execution boundary used by workers.
LOCKED_RUN=false
if "$SCHEDULED" || [[ "$SKILL" == "preprod-review-run" ]]; then
  LOCKED_RUN=true
fi

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
if {
  [[ -n "$TARGET_SOURCE" ]] ||
    [[ -n "$GATE_DECISION" ]] ||
    [[ -n "$GATE_REASON" ]] ||
    [[ -n "$GATE_FINGERPRINT" ]]
} && [[ -z "$COORDINATED_RUN" ]]; then
  echo "pitcrew-codex: internal gate metadata requires a coordinated run" >&2
  exit 2
fi
if [[ -n "$TARGET_SOURCE" ]] && [[ -z "$TARGET" ]]; then
  echo "pitcrew-codex: target source requires a target" >&2
  exit 2
fi
if [[ -n "$TARGET_SOURCE" ]] &&
  [[ "$TARGET_SOURCE" != "directed" && "$TARGET_SOURCE" != "eligibility" ]]; then
  echo "pitcrew-codex: target source is invalid" >&2
  exit 2
fi
HISTORY_FILE="$RUNTIME_ROOT/$PROJECT/history.jsonl"

record_gate() {
  local decision="$1"
  local reason="$2"
  local outcome="$3"
  local target_id="${4:-}"
  local fingerprint="${5:-}"
  local args=(
    record-gate
    --project "$PROJECT"
    --skill "$SKILL"
    --decision "$decision"
    --reason "$reason"
    --outcome "$outcome"
  )
  if [[ -n "$target_id" ]]; then
    args+=(--target-id "$target_id")
  fi
  if [[ -n "$fingerprint" ]]; then
    args+=(--fingerprint "$fingerprint")
  fi
  python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" "${args[@]}"
}

fail_pre_model() {
  local reason="$1"
  if "$SCHEDULED"; then
    record_gate error "$reason" failed "$TARGET" "$GATE_FINGERPRINT" \
      >/dev/null 2>&1 || true
  fi
  echo "pitcrew-codex: $reason" >&2
  exit 2
}

if ! EXECUTION_STATE="$(python3 "$REPO_ROOT/scripts/pitcrew_runtime_state.py" status --project "$PROJECT")"; then
  fail_pre_model "execution state is unavailable"
fi
if [[ "$EXECUTION_STATE" == "stopped" ]]; then
  if "$SCHEDULED"; then
    record_gate stopped "global stop is active" noop "$TARGET" \
      "$GATE_FINGERPRINT"
  else
    printf '%s\n' '{"status":"noop","reason":"global stop is active"}'
  fi
  exit 0
fi
CONFIG="$RUNTIME_ROOT/$PROJECT/config.json"

if [[ -z "$COORDINATED_RUN" ]]; then
  if [[ -n "$TARGET" ]]; then
    TARGET_SOURCE="directed"
    GATE_DECISION="directed"
    GATE_REASON="human supplied directed target"
  else
    GATE_DECISION="not-checked"
    GATE_REASON="scheduled eligibility was not checked"
  fi
fi

# Preflight belongs to the public entry point: a noop must not leave a claimed
# coordinated row behind.  The internal worker has already passed this gate.
if "$SCHEDULED" && [[ -z "$COORDINATED_RUN" ]]; then
  PREFLIGHT="$(python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" check --project "$PROJECT" --skill "$SKILL")" || {
    fail_pre_model "preflight is unavailable"
  }
  PREFLIGHT_DECISION="$(python3 -c \
    'import json,sys; print(json.load(sys.stdin)["decision"])' \
    <<<"$PREFLIGHT")" || {
    fail_pre_model "preflight response is invalid"
  }
  if [[ "$PREFLIGHT_DECISION" == "noop" ]]; then
    PREFLIGHT_REASON="$(python3 -c \
      'import json,sys; print(json.load(sys.stdin)["reason"])' \
      <<<"$PREFLIGHT")" || {
      fail_pre_model "preflight response is invalid"
    }
    if [[ -n "$TARGET" && "$PREFLIGHT_REASON" == "no-op cooldown is active for this skill" ]]; then
      PREFLIGHT_DECISION="run"
    else
    record_gate cooldown "$PREFLIGHT_REASON" noop "$TARGET" \
      "$GATE_FINGERPRINT" >/dev/null
    printf '%s\n' "$PREFLIGHT" | python3 -c '
import json, sys
value = json.load(sys.stdin)
value["status"] = "noop"
value["next_action"] = value.get(
    "next_action",
    "retry after the provider cooldown",
)
value.pop("decision", None)
print(json.dumps(value, separators=(",", ":")))
'
    exit 0
    fi
  elif [[ "$PREFLIGHT_DECISION" != "run" ]]; then
    fail_pre_model "preflight response is invalid"
  fi

  if [[ -z "$TARGET" ]]; then
    ELIGIBILITY="$(python3 "$REPO_ROOT/scripts/pitcrew_eligibility.py" \
      check --project "$PROJECT" --skill "$SKILL")" || {
      fail_pre_model "eligibility probe is unavailable"
    }
    ELIGIBILITY="$(printf '%s' "$ELIGIBILITY" | \
      python3 "$REPO_ROOT/scripts/pitcrew_eligibility.py" \
        normalize --project "$PROJECT" --skill "$SKILL")" || {
      fail_pre_model "eligibility response is invalid"
    }
    GATE_DECISION="$(python3 -c \
      'import json,sys; print(json.load(sys.stdin)["decision"])' \
      <<<"$ELIGIBILITY")" || {
      fail_pre_model "eligibility response is invalid"
    }
    GATE_REASON="$(python3 -c \
      'import json,sys; print(json.load(sys.stdin)["reason"])' \
      <<<"$ELIGIBILITY")" || {
      fail_pre_model "eligibility response is invalid"
    }
    GATE_FINGERPRINT="$(python3 -c \
      'import json,sys; print(json.load(sys.stdin).get("fingerprint") or "")' \
      <<<"$ELIGIBILITY")" || {
      fail_pre_model "eligibility response is invalid"
    }
    if [[ "$GATE_DECISION" == "empty" ]]; then
      record_gate empty "$GATE_REASON" noop "" "$GATE_FINGERPRINT"
      exit 0
    elif [[ "$GATE_DECISION" == "eligible" ]]; then
      TARGET="$(python3 -c \
        'import json,sys; print(json.load(sys.stdin).get("target_id") or "")' \
        <<<"$ELIGIBILITY")" || {
        fail_pre_model "eligibility response is invalid"
      }
      if [[ -z "$TARGET" ]]; then
        fail_pre_model "eligibility response is invalid"
      fi
      TARGET_SOURCE="eligibility"
    elif [[ "$GATE_DECISION" != "unavailable" ]]; then
      fail_pre_model "eligibility response is invalid"
    fi
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
  if [[ -n "$TARGET_SOURCE" ]]; then
    DISPATCH_ARGS+=(--target-source "$TARGET_SOURCE")
  fi
  if [[ -n "$GATE_DECISION" ]] && [[ "$GATE_DECISION" != "not-checked" ]]; then
    DISPATCH_ARGS+=(
      --gate-decision "$GATE_DECISION"
      --gate-reason "$GATE_REASON"
    )
  fi
  if [[ -n "$GATE_FINGERPRINT" ]]; then
    DISPATCH_ARGS+=(--gate-fingerprint "$GATE_FINGERPRINT")
  fi
  DISPATCH_RESULT="$("${DISPATCH_ARGS[@]}")" || {
    fail_pre_model "dispatcher is unavailable"
  }
  DISPATCH_STATE="$(python3 -c \
    'import json,sys; print(json.load(sys.stdin)["state"])' \
    <<<"$DISPATCH_RESULT")" || {
    fail_pre_model "dispatcher response is invalid"
  }
  if [[ "$DISPATCH_STATE" == "failed" || "$DISPATCH_STATE" == "cancelled" ]]; then
    record_gate error "dispatcher rejected the scheduled run" failed \
      "$TARGET" "$GATE_FINGERPRINT" >/dev/null
  elif [[ "$DISPATCH_STATE" != "queued" &&
    "$DISPATCH_STATE" != "running" &&
    "$DISPATCH_STATE" != "succeeded" ]]; then
    fail_pre_model "dispatcher response is invalid"
  fi
  printf '%s\n' "$DISPATCH_RESULT"
  exit 0
fi

RUN_DB=""
if [[ -n "$COORDINATED_RUN" ]]; then
  RUN_DB="$RUNTIME_ROOT/$PROJECT/runs.sqlite3"
  if ! python3 -c '
import sys, uuid
from pathlib import Path
sys.path.insert(0, sys.argv[10])
from scripts.pitcrew_run_store import RunStore, RunStoreError

(
    database,
    project,
    skill,
    target,
    run_id,
    target_source,
    gate_decision,
    gate_reason,
    gate_fingerprint,
    _repo_root,
) = sys.argv[1:]
try:
    uuid.UUID(run_id)
    row = RunStore(Path(database)).get(run_id)
except (ValueError, RunStoreError):
    raise SystemExit(1)
if row is None or row["project"] != project or row["skill"] != skill:
    raise SystemExit(1)
if (
    (row["target"] or "") != target
    or (row["target_source"] or "") != target_source
    or (row["gate_decision"] or "") != gate_decision
    or (row["gate_reason"] or "") != gate_reason
    or (row["gate_fingerprint"] or "") != gate_fingerprint
    or row["state"] != "running"
):
    raise SystemExit(1)
' "$RUN_DB" "$PROJECT" "$SKILL" "$TARGET" "$COORDINATED_RUN" \
    "$TARGET_SOURCE" "$GATE_DECISION" "$GATE_REASON" "$GATE_FINGERPRINT" \
    "$REPO_ROOT"; then
    fail_pre_model "coordinated run is invalid"
  fi
fi
MODEL="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" model --project "$PROJECT" --skill "$SKILL")" ||
  fail_pre_model "model resolution is unavailable"
REASONING_EFFORT="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" reasoning --project "$PROJECT" --skill "$SKILL")" ||
  fail_pre_model "reasoning resolution is unavailable"
ROUTING_MODE="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" routing-mode --project "$PROJECT" --skill "$SKILL")" ||
  fail_pre_model "routing resolution is unavailable"
CANDIDATE_MODEL="$MODEL"
ROUTING_REASON="baseline retained during observation"
if [[ -z "$GATE_DECISION" && -z "$COORDINATED_RUN" ]]; then
  GATE_DECISION="not-checked"
  GATE_REASON="scheduled eligibility not checked"
fi
REPO="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" repo --project "$PROJECT")" ||
  fail_pre_model "repository resolution is unavailable"
[[ -n "$REPO" && -d "$REPO" ]] || {
  fail_pre_model "configured repository is unavailable: $REPO"
}

PROMPT="Use \$pitcrew:$SKILL for project '$PROJECT'. Read $CONFIG, perform exactly one bounded pass in $REPO, then stop. The Pitcrew repository root is $REPO_ROOT. When a skill refers to references/<file>, read $REPO_ROOT/references/<file>; do not resolve that path relative to the project checkout. Fail closed if the required Pitcrew reference is missing or unreadable. The Pitcrew coverage helper is at $REPO_ROOT/scripts/research_coverage.py; use it when the research skill requires coverage rotation. Fail closed when a configured provider or permission is unavailable."
if [[ -n "$TARGET" && "$TARGET_SOURCE" == "directed" ]]; then
  PROMPT+=" Operate on exactly this directed target: $TARGET. Validate it with references/DIRECTED-TARGET.md before any provider lookup."
elif [[ -n "$TARGET" && "$TARGET_SOURCE" == "eligibility" ]]; then
  PROMPT+=" The read-only eligibility probe preselected this target: $TARGET. Revalidate that exact target against the skill's authoritative source before any mutation; if it is stale, return a structured noop."
fi
if [[ -n "$COORDINATED_RUN" ]]; then
  if [[ -n "$TARGET" ]]; then
    BIND_TARGET="$TARGET"
    BIND_SELECTION="Even though the dispatcher preselected or directed this target,"
  else
    BIND_TARGET="<canonical-url>"
    BIND_SELECTION="After selecting one canonical target,"
  fi
  PROMPT+=" This execution is coordinated as PITCREW_RUN_ID=$COORDINATED_RUN. $BIND_SELECTION run exactly: python3 $REPO_ROOT/scripts/pitcrew_run_dispatcher.py bind-target --project $PROJECT --run-id $COORDINATED_RUN --target $BIND_TARGET before the first tracker mutation or checkout write. This bind is idempotent. If binding reports a conflict, reports the target stale, or is unavailable, select another eligible ticket or return a structured no-op without a tracker mutation or checkout write."
fi
if "$SCHEDULED" && [[ "$SKILL" == "unblock" ]]; then
  PROMPT+=" This is an unattended scheduled run. When a human decision is required, do not leave the question only in the final response: atomically persist the exact pending question, choices, ticket context, and status=blocked in $RUNTIME_ROOT/$PROJECT/unblock-state.json as required by skills/unblock/SKILL.md, then stop. The dashboard reads that file and cannot read this log."
fi

# Implementer must update the configured repository's Git metadata to create
# isolated worktrees. The other roles retain the normal workspace boundary.
SANDBOX_MODE="workspace-write"
if [[ "$SKILL" == "implementer-run" || "$SKILL" == "bugfixer-run" ]]; then
  SANDBOX_MODE="danger-full-access"
elif { uses_provider_network "$SKILL" \
    && { [[ "$SKILL" != "releaser-run" ]] || release_is_armed; }; }; then
  SANDBOX_MODE="danger-full-access"
fi

if "$DRY_RUN"; then
  printf '%s\n' "cd=$REPO" "model=$MODEL" "reasoning_effort=${REASONING_EFFORT:-inherited}" "routing_mode=$ROUTING_MODE" "sandbox=$SANDBOX_MODE" "prompt=$PROMPT"
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
if [[ -n "$REASONING_EFFORT" ]]; then
  CODEX_ARGS+=( -c "model_reasoning_effort=\"$REASONING_EFFORT\"" )
fi

if "$LOCKED_RUN"; then
  LOCK_ROOT="${PITCREW_LOCK_ROOT:-$RUNTIME_ROOT/$PROJECT/locks}"
  SUMMARY_DIR="$RUNTIME_ROOT/$PROJECT/logs"
  LIVE_DIR="$RUNTIME_ROOT/$PROJECT/live"
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
  if "$SCHEDULED"; then
    CODEX_ARGS+=(--output-schema "$REPO_ROOT/references/run-result.schema.json")
  fi
fi

CODEX_ARGS+=("$PROMPT")

if "$LOCKED_RUN"; then
  set +e
  LOCKED_ARGS=(
    python3 "$REPO_ROOT/scripts/pitcrew_locked_exec.py"
    --lock-file "$LOCK_FILE"
    --project "$PROJECT"
    --skill "$SKILL"
    --model "$MODEL"
    --reasoning-effort "$REASONING_EFFORT"
    --routing-mode "$ROUTING_MODE"
    --candidate-model "$CANDIDATE_MODEL"
    --routing-reason "$ROUTING_REASON"
    --summary-file "$SUMMARY_FILE"
    --live-file "$LIVE_FILE"
    --history-file "$HISTORY_FILE"
  )
  if [[ -n "$COORDINATED_RUN" ]]; then
    LOCKED_ARGS+=(
      --run-db "$RUN_DB"
      --run-id "$COORDINATED_RUN"
      --launcher-pid "$$"
    )
  fi
  if [[ -n "$TARGET" ]]; then
    LOCKED_ARGS+=(--target-id "$TARGET")
  fi
  if [[ -n "$GATE_DECISION" ]]; then
    LOCKED_ARGS+=(--gate-decision "$GATE_DECISION" --gate-reason "$GATE_REASON")
  fi
  if [[ -n "$GATE_FINGERPRINT" ]]; then
    LOCKED_ARGS+=(--fingerprint "$GATE_FINGERPRINT")
  fi
  if "$SCHEDULED"; then
    LOCKED_ARGS+=(--require-structured-result)
  fi
  LOCKED_ARGS+=(-- "${CODEX_BIN:-codex}" "${CODEX_ARGS[@]}")
  if [[ -n "$COORDINATED_RUN" ]]; then
    PITCREW_RUN_ID="$COORDINATED_RUN" "${LOCKED_ARGS[@]}"
  else
    if [[ "$SKILL" == "preprod-review-run" ]]; then
      exec "${LOCKED_ARGS[@]}"
    fi
    "${LOCKED_ARGS[@]}"
  fi
  EXIT_CODE=$?
  set -e
  STRUCTURED_STATUS=""
  SUMMARY_PARSE_CODE=2
  if "$SCHEDULED" && [[ -f "$SUMMARY_FILE" ]]; then
    if STRUCTURED_STATUS="$(python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from pitcrew_locked_exec import parse_structured_result, read_summary

summary = read_summary(Path(sys.argv[2]))
try:
    decoded = json.loads(summary)
except (json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(2)
if not isinstance(decoded, dict):
    raise SystemExit(2)
result = parse_structured_result(summary, strict=True)
if result is None:
    raise SystemExit(1)
print(result["status"])
' "$REPO_ROOT/scripts" "$SUMMARY_FILE")"; then
      SUMMARY_PARSE_CODE=0
    else
      SUMMARY_PARSE_CODE=$?
    fi
  fi
  if "$SCHEDULED" && [[ "$SUMMARY_PARSE_CODE" -eq 0 ]] && { [[ "$STRUCTURED_STATUS" == "noop" ]] || [[ "$STRUCTURED_STATUS" == "blocked" ]]; }; then
    STRUCTURED_REASON="$(python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from pitcrew_locked_exec import parse_structured_result, read_summary

result = parse_structured_result(read_summary(Path(sys.argv[2])), strict=True)
print(result["reason"])
' "$REPO_ROOT/scripts" "$SUMMARY_FILE")"
    python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" record-noop \
      --project "$PROJECT" --skill "$SKILL" --reason "$STRUCTURED_REASON" >/dev/null || true
  elif "$SCHEDULED" && [[ "$SUMMARY_PARSE_CODE" -eq 2 ]] && [[ -f "$SUMMARY_FILE" ]] && rg -qi 'authentication|invalid_grant|oauth grant' "$SUMMARY_FILE"; then
    python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" record-provider-failure \
      --project "$PROJECT" --reason "provider authentication failure" >/dev/null || true
  elif "$SCHEDULED" && [[ "$SUMMARY_PARSE_CODE" -eq 2 ]] && [[ -f "$SUMMARY_FILE" ]] && rg -qi 'no eligible item|nothing missing|no permissions required' "$SUMMARY_FILE"; then
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
