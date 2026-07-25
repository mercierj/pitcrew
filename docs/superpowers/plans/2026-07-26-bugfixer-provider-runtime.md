# Bugfixer Provider Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed native GitHub Issues binding and provider contract that can support the later `bugfixer-run` lifecycle with the same logical issue operations already available on GitLab.

**Architecture:** Extend schema version 1 with a conditional `github` block required only for a native GitHub tracker. Keep provider commands in references, not skills. Add one explicit binding command that atomically updates an existing runtime configuration from reviewed JSON without inferring identity or repository values.

**Tech Stack:** Python 3.13 standard library, `gh` CLI provider contract, JSON profiles, Markdown references, Python `unittest`.

**Dependencies:** Complete and verify `docs/superpowers/plans/2026-07-26-ticket-run-coordinator.md` before enabling `bugfixer-run`. This provider plan does not modify the in-progress coordinator files.

**Execution context:** Work in the current checkout. Preserve every existing modification, especially the active coordinator and dashboard work. Stage and commit only the files named by each task.

---

## File map

- Modify `scripts/pitcrew_config.py`: validate native GitHub identity/tracker mappings and atomically install an explicit reviewed binding.
- Modify `tests/test_config.py`: cover conditional validation, malformed bindings, compatibility, and atomic runtime updates.
- Modify `bin/configure.sh`: expose the `github` binding command without adding an unsafe partially configured profile.
- Modify `tests/test_cli.py`: cover the shell entry point and concise failures.
- Create `references/providers/github.md`: native GitHub Issues plus pull-request capability mapping.
- Modify `references/PROVIDERS.md`: route matched native GitHub and GitLab pairs explicitly.
- Modify `references/DIRECTED-TARGET.md`: accept native GitHub issue URLs.
- Modify acting `skills/*/SKILL.md` provider-dispatch blocks: select
  `github.md` for GitHub/GitHub and retain `github-linear.md` for
  GitHub/Linear.
- Modify `tests/test_skill_contracts.py`: lock provider and directed-target markers.
- Modify `README.md`: document native GitHub configuration and fail-closed activation.

## Task 1: Validate a complete native GitHub tracker binding

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Add these tests to `ConfigTest` in `tests/test_config.py`:

```python
def native_github_config(self):
    config = copy.deepcopy(
        json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
    )
    config["providers"] = {"forge": "github", "tracker": "github"}
    config["github"] = {
        "host": "github.com",
        "user": "octocat",
        "owner": "acme",
        "repository": "acme/payments",
        "tracker": {
            "assignee_login": "octocat",
            "ticket_prefix": "acme/payments#",
            "labels": {
                "agent": "pitcrew-agent",
                "investigate": "pitcrew-investigate",
                "quick_win": "pitcrew-quick-win",
                "bug": "bug",
                "improvement": "enhancement",
            },
            "states": {
                "todo": "pitcrew-state-todo",
                "processing": "pitcrew-state-processing",
                "review": "pitcrew-state-review",
                "blocked": "pitcrew-state-blocked",
                "done": "pitcrew-state-done",
            },
        },
    }
    config["repos"] = [{
        "name": "payments",
        "path": "/workspace/payments",
        "default_branch": "main",
        "lang": "python",
        "tags": ["api"],
    }]
    return config

def test_native_github_tracker_requires_explicit_complete_binding(self):
    config = self.native_github_config()
    validate(config)

    required_paths = (
        ("host",),
        ("user",),
        ("owner",),
        ("repository",),
        ("tracker", "ticket_prefix"),
        ("tracker", "labels", "agent"),
        ("tracker", "labels", "investigate"),
        ("tracker", "labels", "quick_win"),
        ("tracker", "labels", "bug"),
        ("tracker", "labels", "improvement"),
        ("tracker", "states", "todo"),
        ("tracker", "states", "processing"),
        ("tracker", "states", "review"),
        ("tracker", "states", "blocked"),
        ("tracker", "states", "done"),
    )
    for path in required_paths:
        broken = copy.deepcopy(config)
        cursor = broken["github"]
        for key in path[:-1]:
            cursor = cursor[key]
        del cursor[path[-1]]
        with self.subTest(path=path):
            with self.assertRaises(ConfigError):
                validate(broken)

def test_native_github_binding_rejects_repository_mismatch_and_bad_shapes(self):
    config = self.native_github_config()
    for owner, repository in (
        ("acme", "other/payments"),
        ("acme", "acme"),
        ("acme", "https://github.com/acme/payments"),
        ("acme/team", "acme/team/payments"),
        ("acme", "acme/../payments"),
        ("acme", "acme/payments\ninjected"),
    ):
        broken = copy.deepcopy(config)
        broken["github"]["owner"] = owner
        broken["github"]["repository"] = repository
        with self.subTest(owner=owner, repository=repository):
            with self.assertRaises(ConfigError):
                validate(broken)

def test_non_github_tracker_does_not_require_native_github_block(self):
    config = json.loads(
        (ROOT / "profiles/generic.json").read_text(encoding="utf-8")
    )
    self.assertEqual("linear", config["providers"]["tracker"])
    self.assertNotIn("github", config)
    validate(config)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_config.ConfigTest.test_native_github_tracker_requires_explicit_complete_binding \
  tests.test_config.ConfigTest.test_native_github_binding_rejects_repository_mismatch_and_bad_shapes \
  tests.test_config.ConfigTest.test_non_github_tracker_does_not_require_native_github_block -v
```

Expected: FAIL because `validate` does not inspect a `github` block.

- [ ] **Step 3: Implement the conditional GitHub validator**

Add this helper above `validate` in `scripts/pitcrew_config.py`:

```python
def _non_empty_string(mapping: Mapping[str, Any], key: str, field: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _validate_native_github(config: Mapping[str, Any]) -> None:
    github = _mapping(config, "github")
    host = _non_empty_string(github, "host", "github.host")
    _non_empty_string(github, "user", "github.user")
    owner = _non_empty_string(github, "owner", "github.owner")
    repository = _non_empty_string(
        github, "repository", "github.repository"
    )
    repository_parts = repository.split("/")
    github_slug = re.compile(
        r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
    )
    if (
        host.startswith(("http://", "https://"))
        or "/" in host
        or "/" in owner
        or len(repository_parts) != 2
        or repository_parts[0] != owner
        or not repository_parts[1]
        or github_slug.fullmatch(owner) is None
        or github_slug.fullmatch(repository_parts[1]) is None
        or "://" in repository
    ):
        raise ConfigError("github binding must use host plus owner/repository")

    tracker = github.get("tracker")
    if not isinstance(tracker, Mapping):
        raise ConfigError("github.tracker must be an object")
    _non_empty_string(
        tracker, "ticket_prefix", "github.tracker.ticket_prefix"
    )
    assignee = tracker.get("assignee_login")
    if assignee is not None and (not isinstance(assignee, str) or not assignee):
        raise ConfigError(
            "github.tracker.assignee_login must be a non-empty string when set"
        )
    labels = tracker.get("labels")
    states = tracker.get("states")
    if not isinstance(labels, Mapping):
        raise ConfigError("github.tracker.labels must be an object")
    if not isinstance(states, Mapping):
        raise ConfigError("github.tracker.states must be an object")
    for key in ("agent", "investigate", "quick_win", "bug", "improvement"):
        _non_empty_string(labels, key, f"github.tracker.labels.{key}")
    for key in ("todo", "processing", "review", "blocked", "done"):
        _non_empty_string(states, key, f"github.tracker.states.{key}")
```

Call it immediately after provider validation:

```python
if providers.get("tracker") == "github":
    if providers.get("forge") != "github":
        raise ConfigError(
            "native github tracker requires providers.forge=github"
        )
    _validate_native_github(config)
```

- [ ] **Step 4: Run the configuration suite**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: PASS.

- [ ] **Step 5: Commit the configuration contract**

```bash
git add scripts/pitcrew_config.py tests/test_config.py
git commit -m "feat: validate native GitHub issue bindings"
```

## Task 2: Add an explicit atomic GitHub binding command

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `bin/configure.sh`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing atomic-update tests**

Add to `tests/test_config.py`:

```python
def test_update_runtime_github_binding_is_explicit_and_atomic(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {"CODEX_HOME": temp}
        destination = write_project(
            ROOT / "profiles/generic.json", "example", env
        )
        binding = self.native_github_config()["github"]

        update_runtime_github_binding("example", binding, env)

        updated = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            {"forge": "github", "tracker": "github"},
            updated["providers"],
        )
        self.assertEqual(binding, updated["github"])
        self.assertEqual(0o600, destination.stat().st_mode & 0o777)

def test_update_runtime_github_binding_preserves_config_on_failure(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {"CODEX_HOME": temp}
        destination = write_project(
            ROOT / "profiles/generic.json", "example", env
        )
        before = destination.read_bytes()
        with self.assertRaises(ConfigError):
            update_runtime_github_binding(
                "example",
                {"host": "github.com", "owner": "acme"},
                env,
            )
        self.assertEqual(before, destination.read_bytes())
```

Import `update_runtime_github_binding` with the other configuration helpers.

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_config.ConfigTest.test_update_runtime_github_binding_is_explicit_and_atomic \
  tests.test_config.ConfigTest.test_update_runtime_github_binding_preserves_config_on_failure -v
```

Expected: import failure because the update function does not exist.

- [ ] **Step 3: Implement the secure runtime update**

Add to `scripts/pitcrew_config.py` beside `update_runtime_model`:

```python
def update_runtime_github_binding(
    project: str,
    binding: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> None:
    if not isinstance(binding, Mapping):
        raise ConfigError("github binding must be an object")
    values = os.environ if env is None else env
    project_fd = _open_runtime_project_for_read(project, values)
    lock_fd: int | None = None
    try:
        lock_fd = _lock_runtime_config(project_fd)
        config = _load_runtime_config_from_fd(project_fd)
        updated = dict(config)
        updated["providers"] = {"forge": "github", "tracker": "github"}
        updated["github"] = dict(binding)
        validate(updated)
        _replace_runtime_config(
            project_fd,
            json.dumps(updated, indent=2) + "\n",
        )
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(project_fd)
```

Add a CLI parser and dispatch:

```python
github_parser = subparsers.add_parser("bind-github")
github_parser.add_argument("--project", required=True)
github_parser.add_argument("--binding", type=Path, required=True)
```

```python
if args.command == "bind-github":
    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    update_runtime_github_binding(args.project, binding)
    print(f"Configured native GitHub binding for {args.project}")
    return 0
```

Keep the existing concise `ConfigError` handling at the CLI boundary; do not
print binding contents.

- [ ] **Step 4: Expose the command through `bin/configure.sh`**

Add this mode before the existing profile parsing:

```bash
if [[ "${1:-}" == "bind-github" ]]; then
  shift
  PROJECT="${1:?bind-github requires a project}"
  shift
  [[ "${1:-}" == "--binding" && -n "${2:-}" ]] || {
    echo "bind-github requires --binding <json-file>" >&2
    exit 2
  }
  exec python3 "$REPO_ROOT/scripts/pitcrew_config.py" \
    bind-github --project "$PROJECT" --binding "$2"
fi
```

Add a CLI test that initializes `generic`, writes a binding JSON file, runs:

```bash
bin/configure.sh bind-github example --binding /absolute/path/binding.json
```

and asserts the persisted providers are exactly GitHub/GitHub. Add invalid JSON
and incomplete-binding cases that return exit code 1 without echoing the file
contents.

- [ ] **Step 5: Run configuration and CLI tests**

Run:

```bash
python3 -m unittest tests.test_config tests.test_cli -v
```

Expected: PASS.

- [ ] **Step 6: Commit the explicit binding workflow**

```bash
git add scripts/pitcrew_config.py bin/configure.sh tests/test_config.py tests/test_cli.py
git commit -m "feat: configure native GitHub bindings explicitly"
```

## Task 3: Define the native GitHub Issues provider contract

**Files:**
- Create: `references/providers/github.md`
- Modify: `references/PROVIDERS.md`
- Modify: `references/DIRECTED-TARGET.md`
- Modify: `skills/manager-run/SKILL.md`
- Modify: `skills/implementer-run/SKILL.md`
- Modify: `skills/reviewer-run/SKILL.md`
- Modify: `skills/validator-run/SKILL.md`
- Modify: `skills/unblock/SKILL.md`
- Modify: `skills/investigate-run/SKILL.md`
- Modify: `skills/stale-sweep/SKILL.md`
- Modify: `skills/ops-run/SKILL.md`
- Modify: `skills/releaser-run/SKILL.md`
- Test: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing provider marker tests**

Add to `ReferenceContractTest`:

```python
def test_native_github_issue_contract(self):
    self.assert_markers(
        "references/providers/github.md",
        'gh auth status --hostname "$GITHUB_HOST"',
        "providers.tracker",
        "List eligible work",
        "Inspect work",
        "Claim or transition",
        "Create change",
        "Read checks",
        "Merge change",
        "Close lifecycle",
        "pull_request",
        "lookup-before-create",
        "Never fall back",
    )

def test_directed_targets_accept_native_github_issues(self):
    self.assert_markers(
        "references/DIRECTED-TARGET.md",
        "https://github.com/<owner>/<repo>/issues/<number>",
        "GitHub issue",
    )
```

Extend `test_provider_contract` to require
`references/providers/github.md` and the matched pair
`forge=github, tracker=github`.

Extend `test_acting_skills_route_providers_without_fallback` so every acting
skill contains both references and the exact pair dispatch.

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_skill_contracts.ReferenceContractTest.test_native_github_issue_contract \
  tests.test_skill_contracts.ReferenceContractTest.test_directed_targets_accept_native_github_issues -v
```

Expected: FAIL because `references/providers/github.md` is absent.

- [ ] **Step 3: Create the concrete GitHub provider reference**

Create `references/providers/github.md` with these normative sections and exact
operations:

```markdown
# Native GitHub provider contract

Use this contract only for the matched pair
`providers.forge=github` and `providers.tracker=github`.

Resolve `GITHUB_HOST`, `GITHUB_USER`, `GITHUB_OWNER`,
`GITHUB_REPOSITORY`, tracker labels, and tracker states only from the validated
configuration. Verify:

```sh
gh auth status --hostname "$GITHUB_HOST"
gh api --hostname "$GITHUB_HOST" user
gh api --hostname "$GITHUB_HOST" "repos/$GITHUB_REPOSITORY"
```

The authenticated login, returned owner, and full repository name must match
the configuration exactly. Never fall back to another host, owner, repository,
or tracker.

Before an acting role starts, list repository labels and verify every configured
tracker state/routing label exists exactly. A missing label returns a structured
no-op; runs never create or approximate labels.

## Tracker operations

- **List eligible work:** `GET /repos/:owner/:repo/issues` with explicit state
  and labels; discard entries containing a `pull_request` field.
- **Inspect work:** `GET /repos/:owner/:repo/issues/:number`, then its comments.
- **Claim or transition:** re-fetch first, preserve every non-state label,
  replace only the configured state label, then PATCH the complete label list.
- **Close lifecycle:** apply the configured done label, post the idempotency-
  marked audit comment, PATCH `state=closed`, and verify both conditions.

## Pull-request operations

- **Create change:** lookup-before-create by exact head repository and branch;
  reuse an open PR when it represents the same ticket operation.
- **Read checks:** inspect the current head SHA and required check runs.
- **Merge change:** re-fetch PR, reviews, checks, discussions, and expected head
  SHA immediately before squash merge; delete only the confirmed source branch.

Every comment, transition, PR, and closeout carries an operation marker. After
an uncertain response, re-read before retrying.
```

Keep actual `gh api` flag syntax in this reference. Acting skills must continue
to speak only in provider-neutral capabilities.

- [ ] **Step 4: Route providers and directed targets**

Update `references/PROVIDERS.md` so the table is explicit:

```markdown
| Forge | Tracker | Reference |
| --- | --- | --- |
| `github` | `github` | `providers/github.md` |
| `github` | `linear` | `providers/github-linear.md` |
| `gitlab` | `gitlab` | `providers/gitlab.md` |
```

Document that `bugfixer-run` supports only the matched native pairs in its first
version. Add the GitHub issue URL form to `references/DIRECTED-TARGET.md` and
require exact host/owner/repository/number validation.

In each acting skill's provider-dispatch block, replace forge-only selection
with pair selection:

```markdown
- `forge=github, tracker=github`: read
  `references/providers/github.md`.
- `forge=github, tracker=linear`: read
  `references/providers/github-linear.md`.
- `forge=gitlab, tracker=gitlab`: read
  `references/providers/gitlab.md`.
```

Any other pair required by a role returns the structured no-op. Do not alter
role-specific behavior in this task.

- [ ] **Step 5: Run reference contracts**

Run:

```bash
python3 -m unittest tests.test_skill_contracts -v
```

Expected: PASS.

- [ ] **Step 6: Commit the provider reference**

```bash
git add references/providers/github.md references/PROVIDERS.md \
  references/DIRECTED-TARGET.md skills/manager-run/SKILL.md \
  skills/implementer-run/SKILL.md skills/reviewer-run/SKILL.md \
  skills/validator-run/SKILL.md skills/unblock/SKILL.md \
  skills/investigate-run/SKILL.md skills/stale-sweep/SKILL.md \
  skills/ops-run/SKILL.md skills/releaser-run/SKILL.md \
  tests/test_skill_contracts.py
git commit -m "docs: define native GitHub issue provider"
```

## Task 4: Document and verify the provider runtime

**Files:**
- Modify: `README.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Add a failing documentation contract**

Add a test requiring the README to contain:

```python
for marker in (
    "Native GitHub Issues",
    "bin/configure.sh bind-github",
    "providers.forge",
    "providers.tracker",
    "does not infer",
    "bugfixer-run remains disabled",
):
    self.assertIn(marker, readme)
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: FAIL on the new README markers.

- [ ] **Step 3: Add the native GitHub setup section**

Document this exact operator sequence without real credentials:

```bash
./bin/configure.sh example --profile generic
$EDITOR /absolute/path/github-binding.json
./bin/configure.sh bind-github example --binding /absolute/path/github-binding.json
python3 scripts/pitcrew_config.py validate \
  "${CODEX_HOME:-$HOME/.codex}/pitcrew/example/config.json"
```

Explain that the binding file contains the reviewed `github` object from the
schema example, no value is inferred from `gh auth` or the current directory,
and `bugfixer-run` remains disabled until its later lifecycle plan supplies and
validates sensitivity policy.

- [ ] **Step 4: Run focused and full deterministic verification**

Run:

```bash
python3 -m unittest tests.test_config tests.test_cli tests.test_skill_contracts tests.test_docs -v
bash tests/run.sh
```

Expected: all tests PASS.

- [ ] **Step 5: Commit provider documentation**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: explain native GitHub issue setup"
```
