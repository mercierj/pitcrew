#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash -n bin/*.sh tests/run.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/pitcrew_config.py validate profiles/generic.json
python3 scripts/pitcrew_config.py validate profiles/getbill.json

echo "Pitcrew verification passed"
