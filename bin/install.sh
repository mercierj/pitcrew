#!/bin/bash
# pitcrew installer — RUN THIS FROM INSIDE THE pitcrew CLONE.
#
# It wires *pitcrew itself* into Claude Code. It does NOT touch your project
# repos — those are only referenced by path inside the config (set up in step 3).
#
# What it does:
#   1. Symlinks each pitcrew skill (skills/<slug>/SKILL.md) → ~/.claude/commands/<slug>.md
#   2. Creates the loop's runtime dir ~/.claude/agent-loop/<project>/ with state/
#   3. Configures the project — runs the interactive config wizard (bin/configure.sh),
#      which asks for your tracker / repos / webhooks and writes config.json
#   4. Copies references/{TOPOLOGY,LINEAR-ACCESS,DIRECTED-TARGET}.md → the project runtime dir
#   5. Initializes ~/.claude/agent-loop/<project>/lessons.md (empty if missing)
#   6. Writes <project> to ~/.claude/agent-loop/default.txt
#
# Re-run safe (idempotent). Won't clobber an existing config.json or lessons.md.
#
# Usage (run from the repo root or its bin/ dir):
#   ./bin/install.sh [project-name] [--no-wizard]
#     project-name  default: "example"
#     --no-wizard   skip the interactive wizard; just drop config.example.json to edit by hand

set -euo pipefail

NO_WIZARD=0
PROJECT=""
for arg in "$@"; do
  case "$arg" in
    --no-wizard) NO_WIZARD=1 ;;
    -*) echo "Unknown flag: $arg" >&2; exit 2 ;;
    *) [ -z "$PROJECT" ] && PROJECT="$arg" ;;
  esac
done
PROJECT="${PROJECT:-example}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMANDS_DIR="$HOME/.claude/commands"
PROJECT_DIR="$HOME/.claude/agent-loop/$PROJECT"
DEFAULT_FILE="$HOME/.claude/agent-loop/default.txt"

# The Codex skills. Each is a directory under skills/ with SKILL.md inside.
SKILLS=(
  "architecture-run"
  "preprod-review-run"
  "security-run"
  "product-discovery-run"
  "research-run"
  "qa-run"
  "implementer-run"
  "reviewer-run"
  "validator-run"
  "unblock"
  "investigate-run"
  "stale-sweep"
  "ops-run"
  "releaser-run"
  "manager-run"
  "coverage-run"
  "dev-verify-run"
)

echo "=== pitcrew installer ==="
echo "  This wires pitcrew (this repo) into Claude Code. Your project repos are"
echo "  untouched — they're only referenced by path in the config."
echo
echo "  pitcrew repo:  $REPO_ROOT"
echo "  commands dir:  $COMMANDS_DIR   (where the 16 skills get symlinked)"
echo "  project:       $PROJECT"
echo "  runtime dir:   $PROJECT_DIR   (config + state live here, never in this repo)"
echo

# Sanity: are we actually in a pitcrew checkout?
if [ ! -d "$REPO_ROOT/skills" ] || [ ! -d "$REPO_ROOT/references" ]; then
  echo "ERROR: This doesn't look like a pitcrew checkout (missing skills/ or references/)."
  echo "       Run this script from inside the repo root or its bin/ dir."
  exit 1
fi

# Sanity: jq (skills parse config.json with it)?
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq not found. Install with: brew install jq  (macOS) / apt-get install jq (Debian)"
  exit 1
fi

# Step 1: symlink skills
echo "[1/6] Symlinking skills..."
mkdir -p "$COMMANDS_DIR"
linked_count=0
for slug in "${SKILLS[@]}"; do
  src="$REPO_ROOT/skills/$slug/SKILL.md"
  dst="$COMMANDS_DIR/$slug.md"
  if [ ! -f "$src" ]; then
    echo "  ! skipped: $slug (no SKILL.md in $REPO_ROOT/skills/$slug/)"
    continue
  fi
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    echo "  = already linked: $slug"
  else
    rm -f "$dst"
    ln -s "$src" "$dst"
    echo "  + $slug.md -> skills/$slug/SKILL.md"
  fi
  linked_count=$((linked_count + 1))
done
echo "  linked $linked_count skills"
echo

# Step 2: project runtime dir
echo "[2/6] Creating runtime dirs..."
mkdir -p "$PROJECT_DIR/state"
echo "  = $PROJECT_DIR/state/"
echo

# Step 3: configure the project (interactive wizard, or copy the example)
echo "[3/6] Configuring project '$PROJECT'..."
if [ -f "$PROJECT_DIR/config.json" ]; then
  echo "  = config.json already exists — leaving it untouched."
  echo "    Re-run the wizard any time:  ./bin/configure.sh $PROJECT"
elif [ "$NO_WIZARD" = 1 ] || [ ! -t 0 ]; then
  cp "$REPO_ROOT/references/config.example.json" "$PROJECT_DIR/config.json"
  echo "  + copied references/config.example.json (wizard skipped)."
  echo "    ! Edit it by hand: $PROJECT_DIR/config.json  (or run ./bin/configure.sh $PROJECT)"
else
  echo "  Launching the config wizard — answers your tracker / repos / webhooks."
  echo
  if ! "$REPO_ROOT/bin/configure.sh" "$PROJECT"; then
    echo "  (wizard exited early — copying the example so you can edit by hand)"
    [ -f "$PROJECT_DIR/config.json" ] || cp "$REPO_ROOT/references/config.example.json" "$PROJECT_DIR/config.json"
  fi
fi
echo

# Step 4: reference docs into the runtime dir (handy alongside the config)
echo "[4/6] Seeding reference docs..."
for doc in TOPOLOGY LINEAR-ACCESS DIRECTED-TARGET; do
  src="$REPO_ROOT/references/$doc.md"
  dst="$PROJECT_DIR/$doc.md"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    echo "  = up to date: $doc.md"
  else
    cp "$src" "$dst"
    echo "  + $doc.md"
  fi
done
echo

# Step 5: lessons.md
echo "[5/6] Initializing lessons.md..."
if [ -f "$PROJECT_DIR/lessons.md" ]; then
  echo "  = exists at $PROJECT_DIR/lessons.md (not overwriting your local lessons)"
else
  cat > "$PROJECT_DIR/lessons.md" <<EOF
# Agent-Loop Lessons (project: $PROJECT)

**Purpose:** capture corrections you give the agents over time so future fires don't
repeat the same mistakes. Each entry is a one-paragraph rule with a *why*. Skills read
this file at the top of every fire and apply the rules.

This file is **local to your machine** and never committed. Use it for corrections
specific to YOUR workflow.

## How to add an entry
- After an agent makes a decision you'd correct (rejects a PR, bails a ticket, files
  something wrong) — append a \`### <short rule>\` entry under the relevant skill's section.
- Format: rule, then **Why:** + **How to apply:** lines.
- Keep <50 entries per section. Supersede old ones rather than accumulating.

---

## Researcher (\`/research-run\`)
_(rules here)_

## Implementer (\`/implementer-run\`)
_(rules here)_

## QA Runner (\`/qa-run\`)
_(rules here)_

## Reviewer (\`/reviewer-run\`)
_(rules here)_

## Validator (\`/validator-run\`)
_(rules here)_

## Unblocker (\`/unblock\`)
_(rules here)_

## Investigator (\`/investigate-run\`)
_(rules here)_

## Stale-sweep (\`/stale-sweep\`)
_(rules here)_

## Ops (\`/ops-run\`)
_(rules here)_

## Releaser (\`/releaser-run\`)
_(rules here)_

## Manager (\`/manager-run\`)
_(rules here)_

## Coverage (\`/coverage-run\`)
_(rules here)_
EOF
  echo "  + created empty $PROJECT_DIR/lessons.md (per-operator, never committed)"
fi
echo

# Step 6: default.txt
echo "[6/6] Setting default project..."
echo "$PROJECT" > "$DEFAULT_FILE"
echo "  + $DEFAULT_FILE = $PROJECT"
echo

echo "=== Install complete ==="
echo
echo "Next steps:"
echo "  1. Review $PROJECT_DIR/config.json (the wizard filled the basics)."
echo "     Re-run the wizard any time:  ./bin/configure.sh $PROJECT"
echo "     Field-by-field reference:    references/SETUP.md"
echo "  2. Smoke one skill to validate the config:  /research-run $PROJECT"
echo "  3. Launch the loops you want from Claude Code (each on its own cadence):"
echo "       /loop 15min /implementer-run"
echo "       /loop 15min /reviewer-run"
echo "       /loop 30min /research-run"
echo "       /loop 2hours /qa-run"
echo "       /loop 15min /validator-run"
echo "       /loop 6h    /stale-sweep"
echo "       /loop 30min /unblock"
echo "       /loop 30min /investigate-run"
echo "       /loop 10min /ops-run          # needs repos[].health"
echo "       /loop 15min /releaser-run     # needs repos[].release (starts at autonomy:prepare)"
echo "       /loop 1h    /manager-run      # needs manager.sources[]"
echo "       /loop 12h   /coverage-run     # needs qa.test_flow_repo + an architecture repo"
