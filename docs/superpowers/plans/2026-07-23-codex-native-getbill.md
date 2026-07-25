# Codex-native Pitcrew with GetBill Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Pitcrew into an installable, Codex-native plugin while retaining generic GitHub/Linear operation and adding a fail-closed GitLab/GetBill profile.

**Architecture:** The repository root becomes the `pitcrew` plugin. Runtime configuration lives under `${CODEX_HOME:-$HOME/.codex}/pitcrew/<project>`, while checked-in JSON profiles provide safe defaults that the configuration command merges into a user-owned config. The thirteen skills remain the workflow layer; shared runtime, provider, scheduling, and safety contracts move into references and small deterministic Python/shell helpers.

**Tech Stack:** Codex plugins and skills, Markdown, Bash, Python 3 standard library, JSON, TOML examples, `jq`, GitHub CLI, GitLab CLI, Python `unittest`.

---

## File map

### Plugin package

- Create `.codex-plugin/plugin.json`: Codex plugin identity, discovery metadata, and skill path.
- Preserve `.claude-plugin/plugin.json`: secondary Claude compatibility metadata, updated only after Codex works.
- Create `agents/openai.yaml` only if plugin validation requires plugin-level agent metadata; otherwise omit it.

### Runtime and configuration

- Create `profiles/generic.json`: conservative provider-neutral defaults.
- Create `profiles/getbill.json`: GetBill repository metadata, GitLab mapping, project references, commands, and hard safety gates.
- Create `scripts/pitcrew_config.py`: resolve `CODEX_HOME`, merge profiles, validate config, and migrate the legacy runtime.
- Modify `bin/configure.sh`: thin interactive wrapper around `pitcrew_config.py`.
- Modify `bin/pitcrew-codex.sh`: invoke a namespaced installed skill through `codex exec` without widening the caller's sandbox.
- Modify `bin/install-codex.sh`: install/update the local plugin through the personal marketplace and initialize runtime state.

### Shared contracts

- Create `references/CODEX-RUNTIME.md`: project resolution, invocation, state, locking, scheduled-task, and no-op result contract.
- Create `references/SCHEDULED-TASKS.md`: conservative Codex scheduled-task templates and cadence guidance.
- Create `references/PROVIDERS.md`: provider selection and fail-closed rules.
- Create `references/providers/github-linear.md`: GitHub plus Linear operations and capability checks.
- Create `references/providers/gitlab.md`: GitLab issue/MR operations and capability checks.
- Create `references/profiles/getbill.md`: human-readable GetBill constraints and required project references.
- Modify `references/TOPOLOGY.md`, `references/DIRECTED-TARGET.md`, `references/LINEAR-ACCESS.md`, and `references/SETUP.md`: align terminology and link to the new shared contracts.
- Modify `references/config.example.json`: provider-neutral runtime example using the new schema.
- Modify `references/codex-config.example.toml`: safe Codex profile and optional MCP examples.

### Skills

- Modify all thirteen `skills/*/SKILL.md` files: Codex trigger metadata, native `$pitcrew:` cross-references, shared runtime bootstrap, provider abstraction, `AGENTS.md` precedence, and removal of Claude-only active instructions.
- Preserve each role's responsibility and state-machine behavior.

### Tests and documentation

- Create `tests/test_plugin_contract.py`: plugin manifest and thirteen-skill discovery.
- Create `tests/test_config.py`: profile merge, validation, runtime path, and migration.
- Create `tests/test_skill_contracts.py`: forbidden active Claude surfaces and required Codex/GetBill contracts.
- Create `tests/test_cli.py`: installer/configurator/runner dry runs and shell syntax.
- Create `tests/fixtures/legacy-config.json`: non-secret legacy migration fixture.
- Create `tests/fixtures/invalid-config.json`: validation fixture.
- Create `tests/run.sh`: one deterministic test entry point.
- Modify `README.md`, `docs/CODEX.md`, `CONTRIBUTING.md`, and `.gitignore`.

### Existing test policy

These tests are worth their maintenance cost because a broken manifest, path resolver, provider fallback, or safety default would make unattended operation unusable or unsafe. They cover contracts rather than duplicating prose.

---

### Task 1: Add the native Codex plugin manifest

**Files:**
- Create: `.codex-plugin/plugin.json`
- Create: `tests/test_plugin_contract.py`

- [ ] **Step 1: Write the failing manifest test**

```python
# tests/test_plugin_contract.py
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "coverage-run",
    "dev-verify-run",
    "implementer-run",
    "investigate-run",
    "manager-run",
    "ops-run",
    "qa-run",
    "releaser-run",
    "research-run",
    "reviewer-run",
    "stale-sweep",
    "unblock",
    "validator-run",
}


class PluginContractTest(unittest.TestCase):
    def test_manifest_exposes_all_skills(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pitcrew", manifest["name"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("MIT", manifest["license"])
        self.assertEqual("https://github.com/mercierj/pitcrew", manifest["repository"])
        self.assertEqual(SKILLS, {
            path.parent.name
            for path in (ROOT / "skills").glob("*/SKILL.md")
        })

    def test_manifest_has_codex_interface_metadata(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        interface = manifest["interface"]
        self.assertEqual("Pitcrew", interface["displayName"])
        self.assertIn("Development", interface["category"])
        self.assertGreaterEqual(len(interface["capabilities"]), 3)
        self.assertTrue(interface["defaultPrompt"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and confirm the missing manifest failure**

Run:

```bash
python3 -m unittest tests.test_plugin_contract -v
```

Expected: `FileNotFoundError` for `.codex-plugin/plugin.json`.

- [ ] **Step 3: Create the minimal valid manifest**

```json
{
  "name": "pitcrew",
  "version": "0.2.0",
  "description": "Codex-native development crew for recurring research, implementation, review, validation, and delivery workflows.",
  "author": {
    "name": "Felix Carrard and contributors",
    "url": "https://github.com/fcarrar"
  },
  "homepage": "https://github.com/mercierj/pitcrew",
  "repository": "https://github.com/mercierj/pitcrew",
  "license": "MIT",
  "keywords": [
    "codex",
    "automation",
    "code-review",
    "validation",
    "delivery"
  ],
  "skills": "./skills/",
  "interface": {
    "displayName": "Pitcrew",
    "shortDescription": "A Codex crew for continuous repository work",
    "longDescription": "Run focused research, implementation, review, validation, operations, and release workflows with explicit safety gates and project profiles.",
    "developerName": "Pitcrew contributors",
    "category": "Developer Tools",
    "capabilities": [
      "Repository research",
      "Issue and merge-request workflows",
      "Code review and validation",
      "Guarded release preparation"
    ],
    "defaultPrompt": [
      "Use Pitcrew to research one high-confidence improvement.",
      "Use Pitcrew to review the next eligible change.",
      "Use Pitcrew to validate the GetBill project configuration."
    ]
  }
}
```

- [ ] **Step 4: Validate the manifest and run the test**

Run:

```bash
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python3 -m unittest tests.test_plugin_contract -v
```

Expected: plugin validation passes and both tests report `ok`.

- [ ] **Step 5: Commit**

```bash
git add .codex-plugin/plugin.json tests/test_plugin_contract.py
git commit -m "feat: add native Codex plugin manifest"
```

---

### Task 2: Implement runtime paths, profile merging, and validation

**Files:**
- Create: `profiles/generic.json`
- Create: `profiles/getbill.json`
- Create: `scripts/pitcrew_config.py`
- Create: `tests/test_config.py`
- Create: `tests/fixtures/invalid-config.json`

- [ ] **Step 1: Write failing tests for runtime resolution and fail-closed validation**

```python
# tests/test_config.py
import json
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_config import ConfigError, load_profile, runtime_root, validate


ROOT = Path(__file__).resolve().parents[1]


class ConfigTest(unittest.TestCase):
    def test_runtime_root_uses_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(Path(temp) / "pitcrew", runtime_root({"CODEX_HOME": temp}))

    def test_runtime_root_defaults_to_dot_codex(self):
        self.assertEqual(
            Path("/tmp/example-home/.codex/pitcrew"),
            runtime_root({"HOME": "/tmp/example-home"}),
        )

    def test_getbill_profile_fails_closed(self):
        profile = load_profile(ROOT / "profiles/getbill.json")
        self.assertEqual("gitlab", profile["providers"]["forge"])
        self.assertEqual("off", profile["release"]["autonomy"])
        self.assertEqual(
            ["prod", "preprod"],
            profile["safety"]["confirm_each_remote_action"],
        )
        self.assertFalse(profile["safety"]["allow_database_writes"])
        self.assertFalse(profile["safety"]["allow_destructive_git"])

    def test_invalid_provider_is_rejected(self):
        fixture = json.loads(
            (ROOT / "tests/fixtures/invalid-config.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ConfigError, "providers.forge"):
            validate(fixture)


if __name__ == "__main__":
    unittest.main()
```

`tests/fixtures/invalid-config.json`:

```json
{
  "project_name": "broken",
  "providers": {"forge": "unknown", "tracker": "none"},
  "repos": []
}
```

- [ ] **Step 2: Run the test and confirm the import failure**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.pitcrew_config'`.

- [ ] **Step 3: Create the generic and GetBill profiles**

`profiles/generic.json`:

```json
{
  "schema_version": 1,
  "project_name": "example",
  "providers": {"forge": "github", "tracker": "linear"},
  "repos": [],
  "release": {"autonomy": "off"},
  "safety": {
    "confirm_each_remote_action": [],
    "allow_database_writes": false,
    "allow_destructive_git": false,
    "allow_secret_reads": false
  }
}
```

`profiles/getbill.json`:

```json
{
  "schema_version": 1,
  "project_name": "getbill",
  "providers": {"forge": "gitlab", "tracker": "gitlab"},
  "repos": [
    {
      "name": "getbill",
      "path": "/Users/jo/Prog/getbill",
      "default_branch": "main",
      "lang": "php",
      "tags": ["symfony", "stimulus", "vite", "mysql"]
    }
  ],
  "release": {"autonomy": "off"},
  "project": {
    "instructions": ["AGENTS.md"],
    "architecture_index": "graphify-out/wiki/index.md",
    "architecture_report": "graphify-out/GRAPH_REPORT.md",
    "references": {
      "security": ".claude/SECURITY.md",
      "accessibility": ".claude/ACCESSIBILITY.md",
      "performance": ".claude/PERFORMANCE.md",
      "seo": ".claude/SEO.md",
      "schema": ".claude/SCHEMA.md",
      "infrastructure": ".claude/docs/infrastructure.md",
      "modals": ".claude/docs/modal-system.md",
      "background_jobs": ".claude/docs/background-jobs.md"
    },
    "commands": {
      "test": "php bin/phpunit",
      "build": "npm run build",
      "graph_refresh": "bash scripts/rebuild-graphify.sh",
      "schema_dump": "php bin/console app:dump-schema"
    },
    "preferred_skills": ["getbill", "rtk", "playwright", "verify"]
  },
  "safety": {
    "confirm_each_remote_action": ["prod", "preprod"],
    "allow_database_writes": false,
    "allow_destructive_git": false,
    "allow_secret_reads": false,
    "stage_only_owned_files": true,
    "worktree_on_dirty_checkout": false
  }
}
```

- [ ] **Step 4: Implement the minimal standard-library configuration module**

```python
# scripts/pitcrew_config.py
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping


FORGES = {"github", "gitlab"}
TRACKERS = {"linear", "github", "gitlab", "none"}


class ConfigError(ValueError):
    pass


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    codex_home = values.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "pitcrew"
    home = values.get("HOME")
    if not home:
        raise ConfigError("HOME or CODEX_HOME is required")
    return Path(home).expanduser() / ".codex" / "pitcrew"


def load_profile(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    validate(value)
    return value


def validate(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    project_name = config.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise ConfigError("project_name must be a non-empty string")
    providers = config.get("providers")
    if not isinstance(providers, dict):
        raise ConfigError("providers must be an object")
    if providers.get("forge") not in FORGES:
        raise ConfigError("providers.forge must be github or gitlab")
    if providers.get("tracker") not in TRACKERS:
        raise ConfigError("providers.tracker is unsupported")
    repos = config.get("repos")
    if not isinstance(repos, list):
        raise ConfigError("repos must be an array")
    release = config.get("release", {})
    if release.get("autonomy", "off") not in {"off", "prepare", "dev", "full"}:
        raise ConfigError("release.autonomy is unsupported")
    safety = config.get("safety", {})
    for key in (
        "allow_database_writes",
        "allow_destructive_git",
        "allow_secret_reads",
    ):
        if safety.get(key, False) is not False:
            raise ConfigError(f"safety.{key} must default to false")


def write_project(profile_path: Path, project: str, env: Mapping[str, str] | None = None) -> Path:
    profile = load_profile(profile_path)
    profile["project_name"] = project
    destination = runtime_root(env) / project / "config.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ConfigError(f"refusing to overwrite {destination}")
    destination.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    default_file = runtime_root(env) / "default.txt"
    default_file.parent.mkdir(parents=True, exist_ok=True)
    default_file.write_text(project + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--profile", choices=("generic", "getbill"), required=True)
    init_parser.add_argument("--project", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.command == "validate":
        validate(json.loads(args.config.read_text(encoding="utf-8")))
        print(f"Valid config: {args.config}")
        return 0
    destination = write_project(
        root / "profiles" / f"{args.profile}.json",
        args.project,
    )
    print(f"Created {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: four tests report `ok`.

- [ ] **Step 6: Commit**

```bash
git add profiles scripts/pitcrew_config.py tests/test_config.py tests/fixtures/invalid-config.json
git commit -m "feat: add Codex runtime profiles and validation"
```

---

### Task 3: Add safe legacy migration

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `tests/test_config.py`
- Create: `tests/fixtures/legacy-config.json`

- [ ] **Step 1: Add failing migration tests**

Append to `ConfigTest`:

```python
    def test_migration_copies_without_overwriting(self):
        from scripts.pitcrew_config import migrate_legacy

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / ".claude/agent-loop/getbill"
            source.mkdir(parents=True)
            source_config = (
                ROOT / "tests/fixtures/legacy-config.json"
            ).read_text(encoding="utf-8")
            (source / "config.json").write_text(source_config, encoding="utf-8")
            env = {"HOME": temp, "CODEX_HOME": str(root / ".codex")}
            destination = migrate_legacy("getbill", env)
            migrated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(1, migrated["schema_version"])
            self.assertEqual("github", migrated["providers"]["forge"])
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                migrate_legacy("getbill", env)
```

`tests/fixtures/legacy-config.json`:

```json
{
  "project_name": "getbill",
  "github": {"reviewer_login": "mercierj", "org": "mercierj"},
  "linear": {"use": false},
  "repos": [
    {
      "name": "getbill",
      "path": "/Users/jo/Prog/getbill",
      "default_branch": "main",
      "lang": "php"
    }
  ]
}
```

- [ ] **Step 2: Run the focused test and confirm `migrate_legacy` is missing**

Run:

```bash
python3 -m unittest tests.test_config.ConfigTest.test_migration_copies_without_overwriting -v
```

Expected: `ImportError` for `migrate_legacy`.

- [ ] **Step 3: Implement explicit, non-overwriting migration**

Add to `scripts/pitcrew_config.py`:

```python
def migrate_legacy(project: str, env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = values.get("HOME")
    if not home:
        raise ConfigError("HOME is required for legacy migration")
    source = Path(home) / ".claude" / "agent-loop" / project / "config.json"
    destination = runtime_root(values) / project / "config.json"
    if not source.is_file():
        raise ConfigError(f"legacy config not found: {source}")
    if destination.exists():
        raise ConfigError(f"refusing to overwrite {destination}")
    legacy = json.loads(source.read_text(encoding="utf-8"))
    migrated = dict(legacy)
    migrated["schema_version"] = 1
    migrated["providers"] = {
        "forge": "github",
        "tracker": "linear" if legacy.get("linear", {}).get("use") else "none",
    }
    migrated.setdefault("release", {"autonomy": "off"})
    migrated.setdefault("safety", {})
    migrated["safety"].update({
        "allow_database_writes": False,
        "allow_destructive_git": False,
        "allow_secret_reads": False,
    })
    validate(migrated)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(migrated, indent=2) + "\n", encoding="utf-8")
    return destination
```

Add the `migrate` subcommand:

```python
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--project", required=True)
```

Handle it before `init`:

```python
    if args.command == "migrate":
        destination = migrate_legacy(args.project)
        print(f"Migrated config to {destination}")
        return 0
```

- [ ] **Step 4: Run all configuration tests**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: five tests report `ok`.

- [ ] **Step 5: Commit**

```bash
git add scripts/pitcrew_config.py tests/test_config.py tests/fixtures/legacy-config.json
git commit -m "feat: migrate legacy Pitcrew config safely"
```

---

### Task 4: Make configuration and one-pass execution Codex-native

**Files:**
- Modify: `bin/configure.sh`
- Modify: `bin/pitcrew-codex.sh`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI dry-run tests**

```python
# tests/test_cli.py
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run(
            [str(ROOT / args[0]), *args[1:]],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_configure_getbill_writes_under_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            result = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(Path(temp, "pitcrew/getbill/config.json").is_file())

    def test_runner_dry_run_uses_namespaced_skill_and_preserves_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "CODEX_HOME": temp}
            init = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, init.returncode, init.stderr)
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "research-run",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Use $pitcrew:research-run", result.stdout)
            self.assertNotIn("danger-full-access", result.stdout)
            self.assertIn("/Users/jo/Prog/getbill", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and confirm they fail against the legacy scripts**

Run:

```bash
python3 -m unittest tests.test_cli -v
```

Expected: both tests fail because the scripts still use `~/.claude` and legacy prompt files.

- [ ] **Step 3: Replace `bin/configure.sh` with a thin profile initializer**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PROJECT="${1:-example}"
shift || true
PROFILE="generic"

while (($#)); do
  case "$1" in
    --profile) PROFILE="${2:?--profile requires generic or getbill}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

exec python3 "$REPO_ROOT/scripts/pitcrew_config.py" \
  init --profile "$PROFILE" --project "$PROJECT"
```

- [ ] **Step 4: Replace the runner with a safe Codex invocation**

```bash
#!/usr/bin/env bash
set -euo pipefail

SKILL="${1:?usage: pitcrew-codex.sh <skill> [project] [--dry-run]}"
PROJECT="${2:-}"
DRY_RUN="${3:-}"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
RUNTIME_ROOT="$CODEX_HOME_DIR/pitcrew"

if [[ -z "$PROJECT" || "$PROJECT" == "--dry-run" ]]; then
  PROJECT="$(tr -d '\r\n' < "$RUNTIME_ROOT/default.txt")"
  [[ "${2:-}" == "--dry-run" ]] && DRY_RUN="--dry-run"
fi

CONFIG="$RUNTIME_ROOT/$PROJECT/config.json"
[[ -f "$CONFIG" ]] || {
  echo "pitcrew-codex: config missing: $CONFIG" >&2
  exit 2
}
python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/pitcrew_config.py" \
  validate "$CONFIG" >/dev/null

REPO="$(jq -r '.repos[0].path // empty' "$CONFIG")"
[[ -n "$REPO" && -d "$REPO" ]] || {
  echo "pitcrew-codex: configured repository is unavailable: $REPO" >&2
  exit 2
}

PROMPT="Use \$pitcrew:$SKILL for project '$PROJECT'. Read $CONFIG, perform exactly one bounded pass in $REPO, then stop. Fail closed when a configured provider or permission is unavailable."

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  printf '%s\n' "cd=$REPO" "prompt=$PROMPT"
  exit 0
fi

exec codex exec \
  --cd "$REPO" \
  --add-dir "$RUNTIME_ROOT/$PROJECT" \
  "$PROMPT"
```

- [ ] **Step 5: Run shell syntax and CLI tests**

Run:

```bash
bash -n bin/configure.sh bin/pitcrew-codex.sh
python3 -m unittest tests.test_cli -v
```

Expected: shell syntax succeeds and both tests report `ok`.

- [ ] **Step 6: Commit**

```bash
git add bin/configure.sh bin/pitcrew-codex.sh tests/test_cli.py
git commit -m "feat: add Codex-native configuration and runner"
```

---

### Task 5: Define provider-neutral runtime and GetBill contracts

**Files:**
- Create: `references/CODEX-RUNTIME.md`
- Create: `references/SCHEDULED-TASKS.md`
- Create: `references/PROVIDERS.md`
- Create: `references/providers/github-linear.md`
- Create: `references/providers/gitlab.md`
- Create: `references/profiles/getbill.md`
- Create: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write a failing reference-contract test**

```python
# tests/test_skill_contracts.py
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CLAIMS = {
    "references/CODEX-RUNTIME.md": [
        "${CODEX_HOME:-$HOME/.codex}/pitcrew",
        "one bounded pass",
        "structured no-op",
    ],
    "references/SCHEDULED-TASKS.md": [
        "$pitcrew:research-run",
        "$pitcrew:reviewer-run",
        "GetBill",
        "release scheduling is disabled",
    ],
    "references/PROVIDERS.md": [
        "providers.forge",
        "providers.tracker",
        "Never fall back",
    ],
    "references/providers/github-linear.md": ["gh auth status", "Linear"],
    "references/providers/gitlab.md": ["glab auth status", "merge request"],
    "references/profiles/getbill.md": [
        "AGENTS.md",
        "prod",
        "preprod",
        "Graphify",
        "RTK",
    ],
}


class SkillContractTest(unittest.TestCase):
    def test_shared_references_contain_required_contracts(self):
        for relative_path, claims in REFERENCE_CLAIMS.items():
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for claim in claims:
                self.assertIn(claim, text, f"{relative_path} missing {claim}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and confirm missing reference files**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: `FileNotFoundError` for `references/CODEX-RUNTIME.md`.

- [ ] **Step 3: Write the shared runtime contract**

`references/CODEX-RUNTIME.md` must define this exact bootstrap:

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
PITCREW_ROOT="$CODEX_HOME_DIR/pitcrew"
PROJECT="${PITCREW_PROJECT:-$(tr -d '\r\n' < "$PITCREW_ROOT/default.txt" 2>/dev/null)}"
CONFIG_DIR="$PITCREW_ROOT/$PROJECT"
CONFIG_FILE="$CONFIG_DIR/config.json"
STATE_DIR="$CONFIG_DIR/state"
```

It must state:

- resolve one explicit project;
- validate before repository or provider work;
- read repository `AGENTS.md` and closer nested files;
- perform one bounded pass;
- use atomic local state writes;
- return a structured no-op with `status`, `reason`, `project`, `skill`, and `next_action`;
- leave cadence to Codex scheduled tasks or the caller;
- never widen sandbox or approval settings inside a skill.

- [ ] **Step 4: Write provider contracts**

Before the provider files, create `references/SCHEDULED-TASKS.md` with these tested templates:

```text
Research:
Use $pitcrew:research-run for project getbill. Perform one bounded read-only pass,
record only high-confidence findings, then stop.

Review:
Use $pitcrew:reviewer-run for project getbill. Review one eligible merge request,
respect GetBill approval gates, then stop.
```

State that GetBill release scheduling is disabled and that the operator must review the first few scheduled runs before enabling additional acting roles.

- [ ] **Step 5: Write provider contracts**

`references/PROVIDERS.md` must map:

| Config | Allowed values | Rule |
|---|---|---|
| `providers.forge` | `github`, `gitlab` | Select exactly one forge |
| `providers.tracker` | `linear`, `github`, `gitlab`, `none` | Select exactly one tracker |

It must say: “Never fall back to another provider, workspace, owner, or repository.”

`references/providers/github-linear.md` must require `gh auth status`, configured owner/repository checks, Linear team identity checks, and idempotent issue/PR markers.

`references/providers/gitlab.md` must require `glab auth status`, configured host/project checks, GitLab issue/MR terminology, and idempotent labels/comments.

- [ ] **Step 6: Write the GetBill contract**

`references/profiles/getbill.md` must contain:

- repository root `/Users/jo/Prog/getbill`;
- `AGENTS.md` as the authoritative live policy;
- required `.claude` references by domain;
- Graphify read-before-architecture and rebuild-after-code rules;
- RTK command preference;
- GitLab rather than GitHub PR operations;
- one-at-a-time approval for every prod/preprod action;
- explicit approval for migrations, fixtures, schema mutations, raw SQL writes, and mutating `app:*`;
- secret-file denylist;
- destructive Git denylist;
- release autonomy `off`.

- [ ] **Step 7: Run the reference-contract test**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: the reference-contract test reports `ok`.

- [ ] **Step 8: Commit**

```bash
git add references/CODEX-RUNTIME.md references/SCHEDULED-TASKS.md references/PROVIDERS.md references/providers references/profiles tests/test_skill_contracts.py
git commit -m "docs: define Codex runtime and provider contracts"
```

---

### Task 6: Convert all skill metadata and cross-references to Codex

**Files:**
- Modify: all thirteen `skills/*/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Add failing skill metadata and forbidden-surface tests**

Append:

```python
    def test_all_skills_are_codex_native(self):
        forbidden = (
            "~/.claude",
            "AskUserQuestion",
            "ScheduleWakeup",
            "mcp__claude",
            "/loop",
            "~/.codex/prompts",
        )
        for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            self.assertIn("description: Use when", frontmatter, skill_file)
            self.assertIn("references/CODEX-RUNTIME.md", text, skill_file)
            self.assertIn("references/PROVIDERS.md", text, skill_file)
            for token in forbidden:
                self.assertNotIn(token, text, f"{skill_file} contains {token}")

    def test_cross_skill_invocations_are_namespaced(self):
        skill_names = [path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")]
        for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            for name in skill_names:
                self.assertNotIn(f"/{name}", text, skill_file)
```

- [ ] **Step 2: Run the test and capture the current contract failures**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: failures list the current Claude paths, tool names, `/loop`, and slash commands.

- [ ] **Step 3: Rewrite every frontmatter description as a trigger**

Use third-person `Use when ...` descriptions:

| Skill | Trigger |
|---|---|
| `research-run` | scanning one configured repository for high-confidence drift or hardening findings |
| `qa-run` | replaying configured dev smoke flows and recording failures without fixing them |
| `manager-run` | pacing local findings into the configured issue tracker |
| `implementer-run` | implementing one eligible configured issue through a reviewed change |
| `reviewer-run` | reviewing one eligible authored change for spec compliance and code quality |
| `validator-run` | validating one eligible change locally before merge |
| `unblock` | resolving one blocked crew item that requires a human decision |
| `investigate-run` | performing a read-only investigation of one routed blocker |
| `coverage-run` | finding and filling one grounded test-flow coverage gap |
| `dev-verify-run` | checking recently deployed development behavior against targeted flows |
| `ops-run` | observing configured health endpoints and recording confirmed degradation |
| `releaser-run` | preparing or executing an explicitly armed release through configured gates |
| `stale-sweep` | repairing stale crew lifecycle state and pruning owned artifacts |

- [ ] **Step 4: Apply the canonical Codex bootstrap to every skill**

Replace each legacy “load project config” section with:

```markdown
## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md` and the configured provider reference before any external lookup.
Read the target repository's applicable `AGENTS.md` files before acting.
If the active profile is GetBill, also read `references/profiles/getbill.md` and the project
references it requires for the task area.

Perform exactly one bounded pass. If configuration, identity, provider, scope, or permission
validation fails, return the structured no-op from `references/CODEX-RUNTIME.md` and stop.
```

Preserve role-specific variable extraction only where later instructions consume the value.

- [ ] **Step 5: Convert active cross-skill references**

Apply these exact mappings throughout skill prose and examples:

```text
/research-run   -> $pitcrew:research-run
/qa-run         -> $pitcrew:qa-run
/manager-run    -> $pitcrew:manager-run
/implementer-run-> $pitcrew:implementer-run
/reviewer-run   -> $pitcrew:reviewer-run
/validator-run  -> $pitcrew:validator-run
/unblock        -> $pitcrew:unblock
/investigate-run-> $pitcrew:investigate-run
/coverage-run   -> $pitcrew:coverage-run
/dev-verify-run -> $pitcrew:dev-verify-run
/ops-run        -> $pitcrew:ops-run
/releaser-run   -> $pitcrew:releaser-run
/stale-sweep    -> $pitcrew:stale-sweep
```

Remove dynamic self-pacing sections. Replace them with one sentence: “Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.”

- [ ] **Step 6: Replace Claude-only interaction wording**

In `unblock`, replace tool-specific interaction with:

```markdown
Ask one concise question at a time in the current Codex thread. In unattended `codex exec`,
do not wait for input: persist the pending decision in local state, return `status=blocked`
with the exact question and choices, and stop.
```

In every skill, refer to provider capabilities by operation rather than MCP tool identifier. Provider-specific details belong only in `references/providers/*.md`.

- [ ] **Step 7: Run contract and plugin validation**

Run:

```bash
python3 -m unittest tests.test_skill_contracts tests.test_plugin_contract -v
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: all tests and plugin validation pass.

- [ ] **Step 8: Commit**

```bash
git add skills tests/test_skill_contracts.py
git commit -m "refactor: make Pitcrew skills Codex-native"
```

---

### Task 7: Add GitLab routing and GetBill enforcement to acting skills

**Files:**
- Modify: `skills/manager-run/SKILL.md`
- Modify: `skills/implementer-run/SKILL.md`
- Modify: `skills/reviewer-run/SKILL.md`
- Modify: `skills/validator-run/SKILL.md`
- Modify: `skills/unblock/SKILL.md`
- Modify: `skills/investigate-run/SKILL.md`
- Modify: `skills/stale-sweep/SKILL.md`
- Modify: `skills/ops-run/SKILL.md`
- Modify: `skills/releaser-run/SKILL.md`
- Modify: `references/DIRECTED-TARGET.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Add failing GitLab/GetBill acting-skill tests**

Append:

```python
    def test_acting_skills_support_gitlab_and_getbill_gates(self):
        acting = {
            "manager-run",
            "implementer-run",
            "reviewer-run",
            "validator-run",
            "unblock",
            "investigate-run",
            "stale-sweep",
            "ops-run",
            "releaser-run",
        }
        for name in acting:
            text = (ROOT / f"skills/{name}/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("providers.forge", text, name)
            self.assertIn("providers.tracker", text, name)
            self.assertIn("references/providers/gitlab.md", text, name)
            self.assertIn("Never fall back", text, name)

        releaser = (ROOT / "skills/releaser-run/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("release.autonomy is `off`", releaser)
        self.assertIn("prod or preprod", releaser)
        self.assertIn("explicit approval", releaser)
```

- [ ] **Step 2: Run the focused test and confirm missing GitLab contracts**

Run:

```bash
python3 -m unittest tests.test_skill_contracts.SkillContractTest.test_acting_skills_support_gitlab_and_getbill_gates -v
```

Expected: failure on the first acting skill without the provider fields.

- [ ] **Step 3: Add the provider dispatch block to every acting skill**

```markdown
## Provider dispatch

Read `providers.forge` and `providers.tracker` from the validated config.

- For `github`, read `references/providers/github-linear.md` and use GitHub pull-request terminology.
- For `gitlab`, read `references/providers/gitlab.md` and use GitLab merge-request terminology.
- For `linear`, validate the configured Linear team before reading or writing tickets.
- For `none`, skip tracker work and return a structured no-op when this role requires a tracker.

Never fall back to another provider, workspace, owner, project, repository, or environment.
```

- [ ] **Step 4: Map generic operations instead of CLI-specific commands**

Use this operation vocabulary in all nine skills:

| Generic operation | GitHub/Linear | GitLab |
|---|---|---|
| list eligible work | Linear/GitHub issue query | GitLab issue query |
| claim work | issue state/labels | issue labels/assignee |
| create change | pull request | merge request |
| review change | PR review/comment | MR review/comment |
| merge change | guarded PR merge | guarded MR merge |
| close lifecycle | tracker state and labels | GitLab issue labels/state |

Keep exact CLI/tool calls in provider references so skills remain capability-driven.

- [ ] **Step 5: Add GetBill preflight to code-changing and release roles**

Add to `implementer-run`, `validator-run`, `stale-sweep`, and `releaser-run`:

```markdown
When the active profile is GetBill:

1. Re-read the repository `AGENTS.md`.
2. Preserve all unrelated working-tree changes.
3. Never create a worktree only because the checkout is dirty.
4. Read the required domain reference before changing that area.
5. After code changes, rebuild Graphify before completion.
6. Stage only files changed by this crew item.
```

Add to `releaser-run`:

```markdown
For GetBill, release.autonomy is `off`. Do not deploy automatically. Every prod or preprod
action requires explicit approval immediately before that single action. Do not chain remote
actions. Migrations, database writes, mutating console commands, and rollback execution each
require their own explicit approval.
```

- [ ] **Step 6: Extend directed targets**

Update `references/DIRECTED-TARGET.md` to recognize:

```text
GitHub PR: https://github.com/<owner>/<repo>/pull/<number>
GitLab MR: https://<host>/<group>/<project>/-/merge_requests/<number>
Linear issue: https://linear.app/<workspace>/issue/<id>
GitLab issue: https://<host>/<group>/<project>/-/issues/<number>
Short change: <configured-repo>!<number>
Short issue: <configured-repo>#<number>
```

Require provider, host, owner/group, and configured repository validation before acting.

- [ ] **Step 7: Run focused and full contract tests**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: all skill contract tests report `ok`.

- [ ] **Step 8: Commit**

```bash
git add skills references/DIRECTED-TARGET.md tests/test_skill_contracts.py
git commit -m "feat: add GitLab routing and GetBill gates"
```

---

### Task 8: Replace the legacy installer with personal-marketplace installation

**Files:**
- Modify: `bin/install-codex.sh`
- Modify: `tests/test_cli.py`
- Modify: `.gitignore`

- [ ] **Step 1: Add a failing installer dry-run test**

Append:

```python
    def test_installer_dry_run_targets_personal_marketplace(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "HOME": temp,
                "CODEX_HOME": str(Path(temp) / ".codex"),
            }
            result = self.run_cli(
                "bin/install-codex.sh",
                "getbill",
                "--profile",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(".agents/plugins/marketplace.json", result.stdout)
            self.assertIn("plugins/pitcrew", result.stdout)
            self.assertNotIn(".codex/prompts", result.stdout)
            self.assertNotIn(".claude", result.stdout)
```

- [ ] **Step 2: Run the test and confirm legacy path output**

Run:

```bash
python3 -m unittest tests.test_cli.CliTest.test_installer_dry_run_targets_personal_marketplace -v
```

Expected: failure because the old installer targets `.codex/prompts`.

- [ ] **Step 3: Implement install/update behavior**

Replace `bin/install-codex.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PROJECT="${1:-example}"
shift || true
PROFILE="generic"
DRY_RUN=0

while (($#)); do
  case "$1" in
    --profile) PROFILE="${2:?--profile requires generic or getbill}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
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
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
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
```

This preserves existing marketplace metadata and entries, updates only the `pitcrew` entry atomically, refuses to replace an unrelated plugin path, validates before writing, initializes runtime without overwriting, and never touches Claude paths or legacy prompt directories.

- [ ] **Step 4: Update ignored runtime artifacts**

Keep real config files excluded while allowing checked-in fixtures:

```gitignore
config.json
**/config.json
!references/config.example.json
!tests/fixtures/*.json
*.log
*.tmp
```

- [ ] **Step 5: Run installer, CLI, and plugin validation**

Run:

```bash
bash -n bin/install-codex.sh
python3 -m unittest tests.test_cli -v
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: all tests pass and plugin validation succeeds.

- [ ] **Step 6: Commit**

```bash
git add bin/install-codex.sh tests/test_cli.py .gitignore
git commit -m "feat: install Pitcrew through Codex marketplace"
```

---

### Task 9: Align examples, topology, and user documentation

**Files:**
- Modify: `references/config.example.json`
- Modify: `references/codex-config.example.toml`
- Modify: `references/TOPOLOGY.md`
- Modify: `references/LINEAR-ACCESS.md`
- Modify: `references/SETUP.md`
- Modify: `README.md`
- Modify: `docs/CODEX.md`
- Modify: `CONTRIBUTING.md`
- Modify: `.claude-plugin/plugin.json`
- Create: `tests/test_docs.py`

- [ ] **Step 1: Write a failing documentation test**

```python
# tests/test_docs.py
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsTest(unittest.TestCase):
    def test_primary_docs_are_codex_native(self):
        paths = [
            "README.md",
            "docs/CODEX.md",
            "references/SETUP.md",
            "references/TOPOLOGY.md",
        ]
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("$pitcrew:", text, relative)
            self.assertNotIn("~/.codex/prompts", text, relative)
            self.assertNotIn("CODEX_SANDBOX=danger-full-access", text, relative)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("GetBill profile", readme)
        self.assertIn("Codex scheduled", readme)

    def test_examples_parse(self):
        import json
        json.loads(
            (ROOT / "references/config.example.json").read_text(encoding="utf-8")
        )
        json.loads((ROOT / "profiles/getbill.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and confirm legacy documentation failures**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: failures for legacy prompt paths, unsafe sandbox examples, and missing GetBill content.

- [ ] **Step 3: Rewrite the README around Codex**

Order the README sections:

1. Codex-native overview;
2. crew role table using `$pitcrew:<role>`;
3. install from the fork clone;
4. initialize `generic` or `getbill`;
5. manual one-pass invocation;
6. Codex scheduled-task templates;
7. state machine and provider adapters;
8. safety/autonomy model;
9. migration from the upstream Claude runtime;
10. secondary Claude compatibility;
11. license and upstream attribution.

- [ ] **Step 4: Rewrite Codex and setup references**

`docs/CODEX.md` must document:

```bash
./bin/install-codex.sh getbill --profile getbill
codex plugin add pitcrew@personal
./bin/pitcrew-codex.sh research-run getbill --dry-run
python3 scripts/pitcrew_config.py migrate --project getbill
```

Scheduled-task examples must invoke `$pitcrew:<skill>`, select one project, request one bounded pass, and start with research/review roles. Release scheduling stays disabled for GetBill.

`references/codex-config.example.toml` must use supported `sandbox_mode` and `approval_policy` keys, keep network/provider setup optional, and never recommend `danger-full-access` as the standard path.

- [ ] **Step 5: Align topology and provider documentation**

Keep the existing state machine, replace “PR” with “change (PR/MR)” where generic, link provider-specific terms, and explain that `LINEAR-ACCESS.md` applies only when `providers.tracker=linear`.

Update `.claude-plugin/plugin.json` description to identify Claude as the secondary compatibility harness without changing its name or license.

- [ ] **Step 6: Run documentation and contract tests**

Run:

```bash
python3 -m unittest tests.test_docs tests.test_skill_contracts -v
```

Expected: all tests report `ok`.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/CODEX.md CONTRIBUTING.md .claude-plugin/plugin.json references tests/test_docs.py
git commit -m "docs: document Codex-native Pitcrew and GetBill"
```

---

### Task 10: Add the complete verification entry point and smoke test

**Files:**
- Create: `tests/run.sh`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Add a failing end-to-end temporary-home test**

Append:

```python
    def test_getbill_dry_run_never_requests_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "HOME": temp,
                "CODEX_HOME": str(Path(temp) / ".codex"),
            }
            configured = self.run_cli(
                "bin/configure.sh", "getbill", "--profile", "getbill", env=env
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            config_path = Path(temp, ".codex/pitcrew/getbill/config.json")
            config = config_path.read_text(encoding="utf-8")
            self.assertIn('"autonomy": "off"', config)
            result = self.run_cli(
                "bin/pitcrew-codex.sh",
                "releaser-run",
                "getbill",
                "--dry-run",
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("danger-full-access", result.stdout)
            self.assertNotIn("deploy", result.stdout.lower().split("prompt=", 1)[0])
```

- [ ] **Step 2: Run the focused smoke test**

Run:

```bash
python3 -m unittest tests.test_cli.CliTest.test_getbill_dry_run_never_requests_mutation -v
```

Expected before final runner hardening: failure if dry-run output widens permissions or triggers any action.

- [ ] **Step 3: Add one deterministic verification command**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash -n bin/*.sh tests/run.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/pitcrew_config.py validate profiles/generic.json
python3 scripts/pitcrew_config.py validate profiles/getbill.json

echo "Pitcrew verification passed"
```

- [ ] **Step 4: Run the complete suite**

Run:

```bash
bash tests/run.sh
```

Expected final line: `Pitcrew verification passed`.

- [ ] **Step 5: Perform a forbidden-term audit**

Run:

```bash
rg -n '~/.claude|~/.codex/prompts|AskUserQuestion|ScheduleWakeup|mcp__claude|CODEX_SANDBOX=danger-full-access|/loop' \
  skills bin docs/CODEX.md README.md references
```

Expected: no active Codex workflow matches. Any retained historical Claude compatibility mention must be confined to an explicitly labeled compatibility section and excluded from executable instructions.

- [ ] **Step 6: Validate repository state**

Run:

```bash
git diff --check
git status --short
```

Expected: only Task 10 files are uncommitted and `git diff --check` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add tests/run.sh tests/test_cli.py README.md
git commit -m "test: verify Codex-native Pitcrew end to end"
```

---

### Task 11: Install the development build and perform manual Codex discovery

**Files:**
- Modify only if verification exposes a defect: `.codex-plugin/plugin.json`, `skills/*/SKILL.md`, or installation documentation.

- [ ] **Step 1: Validate before installation**

Run:

```bash
bash tests/run.sh
```

Expected final line: `Pitcrew verification passed`.

- [ ] **Step 2: Install into the personal marketplace**

Run:

```bash
./bin/install-codex.sh getbill --profile getbill
```

Expected: plugin validation succeeds, personal marketplace contains `pitcrew`, and an existing GetBill runtime config is preserved rather than overwritten.

- [ ] **Step 3: Install or refresh the plugin**

Read the personal marketplace name with the plugin-creator helper:

```bash
python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/read_marketplace_name.py
codex plugin add pitcrew@personal
```

Expected: Codex confirms the `pitcrew` plugin installation.

- [ ] **Step 4: Start a fresh Codex thread and verify discovery**

In a new thread, invoke:

```text
$pitcrew:research-run
```

Request a GetBill configuration-only dry run. Confirm:

- the skill is discovered under the `pitcrew` namespace;
- it resolves `/Users/jo/Prog/getbill`;
- it reads `AGENTS.md`;
- it reports one bounded pass;
- it performs no external write.

- [ ] **Step 5: Verify all thirteen skills are present**

Use the Codex skill/plugin browser and compare the displayed names to the `SKILLS` set in `tests/test_plugin_contract.py`.

Expected: all thirteen names are present with no legacy prompt duplicates.

- [ ] **Step 6: Run final local verification**

Run:

```bash
bash tests/run.sh
git status --short --branch
```

Expected: verification passes and the worktree is clean.

- [ ] **Step 7: Commit only if manual verification required a correction**

```bash
git add <files-corrected-during-manual-verification>
git commit -m "fix: address Codex plugin discovery verification"
```

If no correction was needed, do not create an empty commit.
