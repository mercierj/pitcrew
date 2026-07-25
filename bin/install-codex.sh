#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PROJECT="example"
PROJECT_SET=0
PROFILE="generic"
DRY_RUN=0

while (($#)); do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { echo "--profile requires generic or getbill" >&2; exit 2; }
      PROFILE="$2"
      shift 2
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    --*) echo "Unknown argument: $1" >&2; exit 2 ;;
    *)
      [[ "$PROJECT_SET" == 0 ]] || { echo "Unexpected project argument: $1" >&2; exit 2; }
      PROJECT="$1"
      PROJECT_SET=1
      shift
      ;;
  esac
done

case "$PROFILE" in
  generic|getbill) ;;
  *) echo "Unsupported profile: $PROFILE" >&2; exit 2 ;;
esac

MARKETPLACE="$HOME/.agents/plugins/marketplace.json"
PLUGIN_LINK="$HOME/plugins/pitcrew"
CONFIG="${CODEX_HOME:-$HOME/.codex}/pitcrew/$PROJECT/config.json"

if [[ "$DRY_RUN" == 1 ]]; then
  printf '%s\n' \
    "plugin_source=$REPO_ROOT" \
    "plugin_link=$PLUGIN_LINK" \
    "marketplace=$MARKETPLACE" \
    "marketplace_source=./plugins/pitcrew" \
    "runtime_config=$CONFIG" \
    "profile=$PROFILE"
  exit 0
fi

python3 -m unittest tests.test_plugin_contract -v

mkdir -p "$(dirname "$PLUGIN_LINK")" "$(dirname "$MARKETPLACE")"
if [[ -e "$PLUGIN_LINK" || -L "$PLUGIN_LINK" ]]; then
  [[ -L "$PLUGIN_LINK" && "$(readlink "$PLUGIN_LINK")" == "$REPO_ROOT" ]] || {
    echo "Refusing to replace existing plugin path: $PLUGIN_LINK" >&2
    exit 2
  }
else
  ln -s "$REPO_ROOT" "$PLUGIN_LINK"
fi

MARKETPLACE_NAME="$(
  python3 - "$MARKETPLACE" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
else:
    payload = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

name = payload.get("name")
if not isinstance(name, str) or not name:
    raise SystemExit(f"{path} must contain a non-empty name")
interface = payload.setdefault("interface", {})
if not isinstance(interface, dict):
    raise SystemExit(f"{path} interface must be an object")
interface.setdefault("displayName", "Personal")
plugins = payload.setdefault("plugins", [])
if not isinstance(plugins, list):
    raise SystemExit(f"{path} plugins must be an array")

entry = {
    "name": "pitcrew",
    "source": {"source": "local", "path": "./plugins/pitcrew"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Developer Tools",
}
if not all(isinstance(item, dict) for item in plugins):
    raise SystemExit(f"{path} plugin entries must be objects")
matches = [index for index, item in enumerate(plugins) if item.get("name") == "pitcrew"]
if len(matches) > 1:
    raise SystemExit(f"{path} contains duplicate pitcrew entries")
if matches:
    plugins[matches[0]] = entry
else:
    plugins.append(entry)

path.parent.mkdir(parents=True, exist_ok=True)
fd, temp_name = tempfile.mkstemp(prefix=".marketplace-", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temp_name, path)
finally:
    if os.path.exists(temp_name):
        os.unlink(temp_name)
print(name)
PY
)"

if [[ ! -f "$CONFIG" ]]; then
  "$REPO_ROOT/bin/configure.sh" "$PROJECT" --profile "$PROFILE"
else
  echo "Preserving existing runtime config: $CONFIG"
fi

echo "Installed local plugin source: $PLUGIN_LINK"
echo "Marketplace: $MARKETPLACE"
echo "Refresh with: codex plugin add pitcrew@$MARKETPLACE_NAME"
echo "Start a new Codex thread after refreshing the plugin."
