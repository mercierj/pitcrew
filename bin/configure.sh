#!/usr/bin/env bash
# Initialize one Pitcrew project from a safe, versioned profile.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT="${1:-example}"
if (($#)); then
  shift
fi
PROFILE="generic"

while (($#)); do
  case "$1" in
    --profile)
      PROFILE="${2:?--profile requires generic or getbill}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$PROFILE" in
  generic|getbill) ;;
  *)
    echo "Unknown profile: $PROFILE (expected generic or getbill)" >&2
    exit 2
    ;;
esac

exec python3 "$REPO_ROOT/scripts/pitcrew_config.py" \
  init --profile "$PROFILE" --project "$PROJECT"
