# Dashboard Agent Models and Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators select a Codex model per Pitcrew agent and inspect per-agent token usage plus a seven-day API-equivalent cost estimate.

**Architecture:** Add one model/pricing catalog shared by configuration, execution, aggregation, and the dashboard. Pass the resolved model explicitly to `codex exec`, consume its JSONL events in the locked runner without retaining transcripts, enrich the existing history records with optional usage, and expose server-calculated metrics plus a guarded stop-configure-reinstall-trigger action to the dependency-free browser UI.

**Tech Stack:** Python 3 standard library, Bash, JSON/JSONL, launchd scheduler controls, vanilla JavaScript/CSS, `unittest`.

---

## File Structure

- Create `scripts/pitcrew_models.py`: model catalog, role defaults, model resolution, pricing metadata, usage normalization, aggregation, and decimal cost calculation.
- Modify `scripts/pitcrew_config.py`: validate optional agent overrides and atomically update one runtime model while preserving the rest of the configuration.
- Modify `bin/pitcrew-codex.sh`: resolve the role model, pass `--model` and `--json`, and tell the locked runner which model was requested.
- Modify `scripts/pitcrew_locked_exec.py`: consume Codex JSONL stdout, retain only final usage metadata, and write model/usage into history records.
- Modify `scripts/pitcrew_history.py`: validate optional model and usage fields without rejecting legacy records.
- Modify `scripts/pitcrew_dashboard.py`: add model/catalog/usage fields to snapshots, aggregate seven-day usage, and orchestrate model changes.
- Modify `bin/pitcrew-dashboard`: expose a strictly validated model-change request through the existing authenticated local action endpoint.
- Modify `dashboard/index.html`: add global seven-day usage metrics.
- Modify `dashboard/app.js`: render model selectors, usage and costs, confirm disruptive changes, and submit the new action.
- Modify `dashboard/styles.css`: style selectors and compact usage blocks responsively.
- Modify `profiles/getbill.json`, `profiles/generic.json`, and `references/config.example.json`: document explicit defaults in shipped configuration.
- Modify `references/SCHEDULED-TASKS.md` and `README.md`: document configuration, history, interruption semantics, and cost-estimate limitations.
- Modify `tests/test_models.py`, `tests/test_config.py`, `tests/test_cli.py`, `tests/test_history.py`, and `tests/test_dashboard.py`: cover the new contracts end to end.

### Task 1: Model catalog, defaults, and configuration validation

**Files:**
- Create: `scripts/pitcrew_models.py`
- Create: `tests/test_models.py`
- Modify: `scripts/pitcrew_config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing catalog and validation tests**

Create `tests/test_models.py` with exact default coverage and decimal pricing:

```python
import unittest
from decimal import Decimal

from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    MODEL_CATALOG,
    estimate_cost,
    resolve_model,
)


class ModelCatalogTest(unittest.TestCase):
    def test_every_role_has_a_supported_default(self):
        self.assertEqual(
            {
                "research-run", "manager-run", "implementer-run", "reviewer-run",
                "validator-run", "investigate-run", "stale-sweep", "qa-run",
                "coverage-run", "dev-verify-run", "ops-run", "unblock",
                "releaser-run",
            },
            set(DEFAULT_MODELS),
        )
        self.assertTrue(set(DEFAULT_MODELS.values()) <= set(MODEL_CATALOG))

    def test_override_wins_and_missing_override_uses_default(self):
        self.assertEqual(
            "gpt-5.6-luna",
            resolve_model({"agents": {"research-run": {"model": "gpt-5.6-luna"}}}, "research-run"),
        )
        self.assertEqual(
            "gpt-5.6-sol",
            resolve_model({}, "implementer-run"),
        )

    def test_cost_uses_all_four_token_categories(self):
        usage = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "total_tokens": 4_000_000,
        }
        self.assertEqual(Decimal("20.875"), estimate_cost("gpt-5.6-terra", usage))
```

Add `tests/test_config.py` cases:

```python
def test_agents_are_optional_but_overrides_must_be_known(self):
    profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
    validate(profile)
    profile["agents"] = {"research-run": {"model": "gpt-5.6-terra"}}
    validate(profile)

    for agents, message in (
        ([], "agents must be an object"),
        ({"unknown-run": {"model": "gpt-5.6-terra"}}, "agents.unknown-run"),
        ({"research-run": []}, "agents.research-run"),
        ({"research-run": {"model": "invented"}}, "agents.research-run.model"),
    ):
        with self.subTest(agents=agents):
            invalid = {**profile, "agents": agents}
            with self.assertRaisesRegex(ConfigError, message):
                validate(invalid)
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_models tests.test_config -v
```

Expected: `tests.test_models` fails to import `scripts.pitcrew_models`.

- [ ] **Step 3: Implement the catalog and validation**

Create `scripts/pitcrew_models.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


MODEL_CATALOG = {
    "gpt-5.6-sol": {
        "profile": "quality",
        "label": "Qualité",
        "pricing": {
            "input_tokens": Decimal("5"),
            "cached_input_tokens": Decimal("0.5"),
            "cache_write_tokens": Decimal("6.25"),
            "output_tokens": Decimal("30"),
        },
    },
    "gpt-5.6-terra": {
        "profile": "balance",
        "label": "Équilibre",
        "pricing": {
            "input_tokens": Decimal("2.5"),
            "cached_input_tokens": Decimal("0.25"),
            "cache_write_tokens": Decimal("3.125"),
            "output_tokens": Decimal("15"),
        },
    },
    "gpt-5.6-luna": {
        "profile": "speed_cost",
        "label": "Rapidité/coût",
        "pricing": {
            "input_tokens": Decimal("1"),
            "cached_input_tokens": Decimal("0.1"),
            "cache_write_tokens": Decimal("1.25"),
            "output_tokens": Decimal("6"),
        },
    },
}

DEFAULT_MODELS = {
    "research-run": "gpt-5.6-terra",
    "manager-run": "gpt-5.6-luna",
    "implementer-run": "gpt-5.6-sol",
    "reviewer-run": "gpt-5.6-sol",
    "validator-run": "gpt-5.6-terra",
    "investigate-run": "gpt-5.6-sol",
    "stale-sweep": "gpt-5.6-luna",
    "qa-run": "gpt-5.6-terra",
    "coverage-run": "gpt-5.6-terra",
    "dev-verify-run": "gpt-5.6-terra",
    "ops-run": "gpt-5.6-luna",
    "unblock": "gpt-5.6-sol",
    "releaser-run": "gpt-5.6-terra",
}

PRICING_EFFECTIVE_DATE = "2026-07-24"
PRICING_CURRENCY = "USD"
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)


def resolve_model(config: Mapping, skill: str) -> str:
    if skill not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {skill}")
    agents = config.get("agents", {})
    entry = agents.get(skill, {}) if isinstance(agents, Mapping) else {}
    return entry.get("model", DEFAULT_MODELS[skill])


def estimate_cost(model: str, usage: Mapping[str, int]) -> Decimal:
    prices = MODEL_CATALOG[model]["pricing"]
    return sum(
        Decimal(usage.get(field, 0)) * prices[field] / Decimal(1_000_000)
        for field in prices
    )
```

In `scripts/pitcrew_config.py`, import `DEFAULT_MODELS` and `MODEL_CATALOG`, then add this block near the end of `validate()`:

```python
agents = config.get("agents", {})
if not isinstance(agents, Mapping):
    raise ConfigError("agents must be an object")
for skill, entry in agents.items():
    if skill not in DEFAULT_MODELS:
        raise ConfigError(f"agents.{skill} is unsupported")
    if not isinstance(entry, Mapping):
        raise ConfigError(f"agents.{skill} must be an object")
    if set(entry) != {"model"} or entry.get("model") not in MODEL_CATALOG:
        raise ConfigError(f"agents.{skill}.model is unsupported")
```

- [ ] **Step 4: Run focused tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_models tests.test_config -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit the catalog**

```bash
git add scripts/pitcrew_models.py scripts/pitcrew_config.py tests/test_models.py tests/test_config.py
git commit -m "feat: define agent model catalog"
```

### Task 2: Atomic model persistence and explicit Codex selection

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing persistence and CLI tests**

Add to `tests/test_config.py`:

```python
def test_update_runtime_model_preserves_config_and_permissions(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {"CODEX_HOME": temp}
        destination = write_project(ROOT / "profiles/generic.json", "example", env)
        before = json.loads(destination.read_text(encoding="utf-8"))

        update_runtime_model("example", "research-run", "gpt-5.6-luna", env)

        after = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(before["repos"], after["repos"])
        self.assertEqual("gpt-5.6-luna", after["agents"]["research-run"]["model"])
        self.assertEqual(0o600, destination.stat().st_mode & 0o777)
```

Import `update_runtime_model` in the test module. Add to the existing successful
scheduled-run test in `tests/test_cli.py`:

```python
self.assertIn("--model", args)
self.assertIn("gpt-5.6-terra", args)
self.assertIn("--json", args)
```

Add a second CLI test that edits runtime config to select Luna for
`research-run`, invokes `pitcrew-codex.sh --dry-run`, and asserts that the
rendered command metadata includes `model=gpt-5.6-luna`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_config tests.test_cli -v
```

Expected: import failure for `update_runtime_model` and missing model arguments.

- [ ] **Step 3: Implement safe persistence**

Add `update_runtime_model()` to `scripts/pitcrew_config.py`. Reuse
`_open_runtime_project_for_read()` so symlink protections remain identical:

```python
def update_runtime_model(
    project: str,
    skill: str,
    model: str,
    env: Mapping[str, str] | None = None,
) -> None:
    if skill not in DEFAULT_MODELS:
        raise ConfigError(f"agents.{skill} is unsupported")
    if model not in MODEL_CATALOG:
        raise ConfigError(f"agents.{skill}.model is unsupported")
    values = os.environ if env is None else env
    config = load_runtime_config(project, values)
    agents = dict(config.get("agents", {}))
    agents[skill] = {"model": model}
    config["agents"] = agents
    validate(config)
    serialized = json.dumps(config, indent=2) + "\n"
    project_fd = _open_runtime_project_for_read(project, values)
    temporary_name = f".config-{os.getpid()}.tmp"
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=project_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, "config.json", src_dir_fd=project_fd, dst_dir_fd=project_fd)
        os.chmod("config.json", 0o600, dir_fd=project_fd, follow_symlinks=False)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=project_fd)
        except FileNotFoundError:
            pass
        os.close(project_fd)
```

Add focused cleanup tests for validation failure, a symlinked config, and a
simulated `os.replace` failure; each must preserve the original file.

- [ ] **Step 4: Pass the selected model to Codex**

In `bin/pitcrew-codex.sh`, resolve the model through a new
`pitcrew_config.py model --project ... --skill ...` subcommand. Add that
subcommand to the Python CLI and print `resolve_model(load_runtime_config(...))`.
Then add:

```bash
MODEL="$(python3 "$REPO_ROOT/scripts/pitcrew_config.py" model \
  --project "$PROJECT" --skill "$SKILL")"
```

For dry runs, print `model=$MODEL`. Add these arguments before the prompt:

```bash
CODEX_ARGS+=(
  --model "$MODEL"
  --json
)
```

- [ ] **Step 5: Run focused tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_config tests.test_cli -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit persistence and execution selection**

```bash
git add scripts/pitcrew_config.py bin/pitcrew-codex.sh tests/test_config.py tests/test_cli.py
git commit -m "feat: run agents with configured models"
```

### Task 3: Capture Codex usage without retaining transcripts

**Files:**
- Modify: `scripts/pitcrew_locked_exec.py`
- Modify: `scripts/pitcrew_history.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_history.py`

- [ ] **Step 1: Write failing usage parser and history tests**

In `tests/test_history.py`, assert that a valid optional usage block survives a
round trip, a legacy record remains valid, and negative/non-integer optional
usage causes only the optional usage block to be dropped.

In `tests/test_cli.py`, update the fake Codex executable to emit:

```json
{"type":"thread.started","thread_id":"thread_1"}
{"type":"turn.completed","usage":{"input_tokens":120,"cached_input_tokens":40,"cache_write_tokens":10,"output_tokens":30,"total_tokens":200}}
```

Then assert:

```python
self.assertEqual("gpt-5.6-terra", latest["model"])
self.assertEqual(
    {
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "cache_write_tokens": 10,
        "output_tokens": 30,
        "total_tokens": 200,
    },
    latest["usage"],
)
self.assertNotIn("thread_1", history_path.read_text(encoding="utf-8"))
```

Add cases for malformed JSON between valid events, no usage event, and a
terminated fake process that emitted a final usage event first.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_history tests.test_cli -v
```

Expected: history lacks `model` and `usage`.

- [ ] **Step 3: Normalize optional history metadata**

In `scripts/pitcrew_history.py`, import the catalog and add:

```python
def _optional_usage(record: dict) -> None:
    model = record.get("model")
    if model is not None and model not in MODEL_CATALOG:
        record.pop("model", None)
        record.pop("usage", None)
        return
    usage = record.get("usage")
    if usage is None:
        return
    if not isinstance(usage, dict) or any(
        not isinstance(usage.get(field), int)
        or isinstance(usage.get(field), bool)
        or usage[field] < 0
        for field in USAGE_FIELDS
    ):
        record.pop("usage", None)
```

Call `_optional_usage(record)` at the end of `_validate_record()`. Copy decoded
records before normalizing so reading malformed optional metadata never mutates
caller-owned data.

- [ ] **Step 4: Capture only the final usage event**

Add `--model` to `pitcrew_locked_exec.py` arguments. Start the child with
`stdout=subprocess.PIPE`, `text=True`, `encoding="utf-8"`, and
`errors="replace"`. Consume lines while the child runs:

```python
def usage_from_event(line: str) -> dict[str, int] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    usage = event.get("usage") if isinstance(event, dict) else None
    if not isinstance(usage, dict):
        return None
    normalized = {
        field: usage.get(field, 0)
        for field in USAGE_FIELDS
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in normalized.values()
    ):
        return None
    if not normalized["total_tokens"]:
        normalized["total_tokens"] = sum(
            normalized[field] for field in USAGE_FIELDS[:-1]
        )
    return normalized
```

Keep only the latest valid result in memory. Add `"model": args.model` to every
record created after the model has been resolved and add `"usage": usage` only
when present. Do not print or persist JSONL lines. Pass `--model "$MODEL"` from
`bin/pitcrew-codex.sh` to the locked runner before `--`.

- [ ] **Step 5: Run focused tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_history tests.test_cli -v
```

Expected: all tests pass and no raw event identifier appears in history.

- [ ] **Step 6: Commit usage capture**

```bash
git add scripts/pitcrew_locked_exec.py scripts/pitcrew_history.py bin/pitcrew-codex.sh tests/test_cli.py tests/test_history.py
git commit -m "feat: record agent token usage"
```

### Task 4: Aggregate usage and expose model metadata in dashboard snapshots

**Files:**
- Modify: `scripts/pitcrew_models.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing aggregation tests**

Add to `tests/test_models.py`:

```python
def test_aggregate_usage_tracks_measured_and_unmeasured_records(self):
    records = [
        {"model": "gpt-5.6-luna", "usage": {
            "input_tokens": 100, "cached_input_tokens": 20,
            "cache_write_tokens": 0, "output_tokens": 10, "total_tokens": 130,
        }},
        {"outcome": "success"},
    ]
    result = aggregate_usage(records)
    self.assertEqual(1, result["measured_runs"])
    self.assertEqual(1, result["unmeasured_runs"])
    self.assertEqual(130, result["tokens"]["total_tokens"])
    self.assertEqual("0.000162", result["estimated_cost_usd"])
```

Extend `DashboardServiceTest.write_history()` with two measured records using
different models and one legacy record. Assert that `snapshot()` includes:

```python
self.assertEqual("gpt-5.6-terra", research["configured_model"])
self.assertEqual("gpt-5.6-luna", research["latest_model"])
self.assertEqual(1, research["usage_7d"]["measured_runs"])
self.assertIn("model_catalog", snapshot)
self.assertEqual("USD", snapshot["pricing"]["currency"])
self.assertEqual(3, snapshot["usage_7d"]["measured_runs"])
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_models tests.test_dashboard.DashboardServiceTest -v
```

Expected: missing `aggregate_usage` and snapshot fields.

- [ ] **Step 3: Implement deterministic aggregation**

Add to `scripts/pitcrew_models.py`:

```python
def empty_usage() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def aggregate_usage(records: list[Mapping]) -> dict:
    tokens = empty_usage()
    cost = Decimal("0")
    measured = 0
    unmeasured = 0
    for record in records:
        model = record.get("model")
        usage = record.get("usage")
        if model not in MODEL_CATALOG or not isinstance(usage, Mapping):
            unmeasured += 1
            continue
        measured += 1
        for field in USAGE_FIELDS:
            tokens[field] += usage[field]
        cost += estimate_cost(model, usage)
    return {
        "tokens": tokens,
        "measured_runs": measured,
        "unmeasured_runs": unmeasured,
        "estimated_cost_usd": format(cost.quantize(Decimal("0.000001")), "f"),
    }


def public_catalog() -> dict:
    return {
        model: {"profile": data["profile"], "label": data["label"]}
        for model, data in MODEL_CATALOG.items()
    }
```

- [ ] **Step 4: Enrich the dashboard snapshot**

In `DashboardService.snapshot()`, group retained records by skill once. Add to
each normalized agent:

```python
"configured_model": resolve_model(self.config, skill),
"latest_model": latest.get("model") if latest else None,
"latest_usage": aggregate_usage([latest]) if latest else aggregate_usage([]),
"usage_7d": aggregate_usage(records_by_skill.get(skill, [])),
```

Add top-level fields:

```python
"model_catalog": public_catalog(),
"pricing": {
    "currency": PRICING_CURRENCY,
    "effective_date": PRICING_EFFECTIVE_DATE,
    "basis": "API standard token pricing estimate",
},
"usage_7d": aggregate_usage(records),
```

Apply configured/default model display to disabled roles too.

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_models tests.test_dashboard.DashboardServiceTest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit aggregation**

```bash
git add scripts/pitcrew_models.py scripts/pitcrew_dashboard.py tests/test_models.py tests/test_dashboard.py
git commit -m "feat: aggregate dashboard agent usage"
```

### Task 5: Guarded stop-configure-reinstall-trigger action

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service orchestration tests**

Add a `DashboardServiceTest` using a recording fake runner and patched
`subprocess.Popen`. Call:

```python
result = service.change_model("research-run", "gpt-5.6-luna")
```

Assert this order:

```python
self.assertEqual(
    [
        ("scheduler", "stop", "research-run"),
        ("persist", "research-run", "gpt-5.6-luna"),
        ("scheduler", "install", "research-run"),
        ("trigger", "research-run"),
    ],
    operations,
)
self.assertTrue(result["accepted"])
```

Add one test for each failure boundary:

- stop failure: no persistence, install, or trigger;
- persistence failure: no install or trigger;
- install failure: configured model remains Luna and no trigger;
- trigger failure: configured model remains Luna and the error is bounded;
- disabled/unknown role and unsupported model: rejected before any operation.

- [ ] **Step 2: Write failing HTTP contract tests**

Extend `FakeDashboardService` with `change_model(skill, model)`. Test a valid
request:

```json
{"action":"change-model","skill":"research-run","model":"gpt-5.6-luna"}
```

Expected: HTTP `202` and the exact fake call. Test extra keys, missing model,
non-string values, unsupported model, invalid session token, and foreign Origin.
Expected: `400` for malformed shapes and `403` for rejected actions/auth.

- [ ] **Step 3: Run service and HTTP tests to verify failure**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest tests.test_dashboard.DashboardHttpTest -v
```

Expected: missing `change_model` and request-shape rejection.

- [ ] **Step 4: Implement orchestration**

Refactor `DashboardService.control()` so private helpers perform scheduler and
trigger operations:

```python
def _scheduler_control(self, action: str, skill: str) -> None:
    result = self._run([
        "python3", str(SCHEDULER), action,
        "--project", self.project, "--skill", skill,
    ])
    if result.returncode:
        raise DashboardError(_redacted_error(result.stderr, f"failed to {action} agent"))


def _trigger(self, skill: str) -> int:
    try:
        process = subprocess.Popen(
            [str(RUNNER), skill, self.project, "--scheduled"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise DashboardError(_redacted_error(str(error), "failed to trigger agent")) from error
    return process.pid
```

Add:

```python
def change_model(self, skill: str, model: str) -> dict:
    self._enabled_entry(skill)
    if model not in MODEL_CATALOG:
        raise DashboardError(f"unsupported model: {model}")
    self._scheduler_control("stop", skill)
    try:
        update_runtime_model(self.project, skill, model)
        self.config = self._load_config()
    except (OSError, ConfigError) as error:
        raise DashboardError(_redacted_error(str(error), "failed to update model")) from error
    self._scheduler_control("install", skill)
    pid = self._trigger(skill)
    self._schedule_cache = None
    return {"accepted": True, "pid": pid, "model": model}
```

- [ ] **Step 5: Implement strict HTTP dispatch**

Keep `/api/actions` and branch on the exact key set. Existing actions require
`{"action", "skill"}`. Model changes require exactly
`{"action", "skill", "model"}` with `action == "change-model"`. Dispatch only
to `service.change_model()`. Preserve current body-size, content-type, token,
Host, Origin, and exception handling.

- [ ] **Step 6: Run tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest tests.test_dashboard.DashboardHttpTest -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit the model-change API**

```bash
git add scripts/pitcrew_dashboard.py bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: change agent models from dashboard"
```

### Task 6: Render selectors, per-agent usage, and global estimates

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing static UI contract tests**

Extend existing asset tests in `tests/test_dashboard.py` to assert:

```python
self.assertIn('id="metric-tokens-7d"', html)
self.assertIn('id="metric-cost-7d"', html)
self.assertIn("changeModel", javascript)
self.assertIn("document.createElement(\"select\")", javascript)
self.assertIn("textContent", javascript)
self.assertNotIn("innerHTML", javascript)
self.assertIn(".agent-usage", stylesheet)
```

Add focused JavaScript-source assertions that the confirmation text contains
both `interrompu` and `relancé`, the request body uses `change-model`, and model
options come from `snapshot.model_catalog`, never from unsanitized HTML.

- [ ] **Step 2: Run UI contract tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetTest -v
```

Expected: missing metric IDs, selector, and usage styles.

- [ ] **Step 3: Add global metrics**

In `dashboard/index.html`, append to `#overview-metrics`:

```html
<article class="metric">
  <span>Tokens · 7 jours</span>
  <strong id="metric-tokens-7d">—</strong>
</article>
<article class="metric">
  <span>Équiv. API · 7 jours</span>
  <strong id="metric-cost-7d">—</strong>
</article>
```

Add the corresponding element references and render them from
`snapshot.usage_7d`. Show `—` when `measured_runs` is zero and suffix a discreet
`mesuré` label when `unmeasured_runs` is non-zero.

- [ ] **Step 4: Add selector and usage rendering**

Add helpers to `dashboard/app.js`:

```javascript
function formatTokens(value) {
  return Number.isFinite(Number(value))
    ? new Intl.NumberFormat("fr-FR").format(Number(value))
    : "Indisponible";
}

function formatCost(value) {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? `${amount.toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 6 })} $`
    : "Indisponible";
}

function appendUsage(container, title, usage) {
  const block = document.createElement("section");
  block.className = "agent-usage";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const text = document.createElement("p");
  const tokens = usage?.tokens;
  text.textContent = usage?.measured_runs
    ? `Entrée ${formatTokens(tokens?.input_tokens)} · Cache lu ${formatTokens(tokens?.cached_input_tokens)} · Cache écrit ${formatTokens(tokens?.cache_write_tokens)} · Sortie ${formatTokens(tokens?.output_tokens)} · Total ${formatTokens(tokens?.total_tokens)} · ${formatCost(usage?.estimated_cost_usd)}`
    : "Données indisponibles";
  block.append(heading, text);
  container.append(block);
}
```

For enabled agents, create a labelled `<select>` from
`snapshot.model_catalog`. Set its value to `agent.configured_model`, add the
catalog label to option text, disable it while the role is pending, and call
`changeModel(agent.skill, select.value, previousValue)` on change. Render
`latest_model`, `latest_usage`, and `usage_7d`. For disabled roles, render the
resolved model as text only.

- [ ] **Step 5: Implement confirmation and request behavior**

Add:

```javascript
async function changeModel(skill, model, previousModel) {
  if (pendingSkills.has(skill) || model === previousModel) return;
  const confirmed = window.confirm(
    `Changer le modèle de ${skill} ? Le passage courant sera interrompu puis relancé immédiatement.`
  );
  if (!confirmed) {
    await refresh();
    return;
  }
  pendingSkills.add(skill);
  syncSkillButtons(skill);
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify({ action: "change-model", skill, model }),
    });
    setText(elements.operationalStatus, `Modèle ${model} appliqué à ${skill}.`);
  } catch {
    setText(elements.operationalStatus, `Le changement de modèle a échoué pour ${skill}.`);
  } finally {
    pendingSkills.delete(skill);
    await refresh({ manual: true });
  }
}
```

Update `syncSkillButtons()` to disable both buttons and selects carrying the
same `data-skill`.

- [ ] **Step 6: Add responsive styling**

Add focused styles:

```css
.agent-model {
  display: grid;
  gap: 0.4rem;
  margin: 1rem 0;
}

.agent-model select {
  width: 100%;
  min-height: 2.75rem;
}

.agent-usage {
  padding-top: 0.8rem;
  border-top: 1px solid var(--line);
}

.agent-usage h4 {
  margin: 0 0 0.35rem;
  font-size: 0.82rem;
}

.agent-usage p {
  margin: 0;
  color: var(--muted);
  line-height: 1.55;
}
```

- [ ] **Step 7: Run dashboard tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: all dashboard service, HTTP, asset, and safety tests pass.

- [ ] **Step 8: Commit the UI**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py
git commit -m "feat: show agent models and token costs"
```

### Task 7: Ship defaults and document the runtime contract

**Files:**
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `README.md`
- Modify: `tests/test_docs.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing documentation and profile tests**

Add assertions that both profiles contain all 13 agent roles with catalog
models, the example includes `agents`, and documentation contains these exact
terms:

```python
for term in (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "cache_write_tokens",
    "API-equivalent",
    "seven-day",
):
    self.assertIn(term, scheduled_tasks)
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_docs tests.test_config -v
```

Expected: profile and documentation assertions fail.

- [ ] **Step 3: Add explicit shipped defaults**

Add the complete `agents` map from `DEFAULT_MODELS` to both profiles and the
configuration example. Keep role names and model slugs byte-for-byte identical
to the catalog.

- [ ] **Step 4: Document behavior and limitations**

Update `references/SCHEDULED-TASKS.md` with:

- `agents.<role>.model` and fallback defaults;
- `--model` and JSONL usage capture;
- optional `model` and `usage` history fields;
- stop, atomic persist, reinstall, and immediate-trigger ordering;
- last-run and seven-day aggregation;
- pricing effective date, USD standard-tier token rates, and measured-subtotal
  behavior;
- explicit exclusion of subscription billing, tool charges, containers,
  regional uplifts, and priority service.

Add a concise README section linking to the detailed reference and explaining
the dashboard selector and estimate.

- [ ] **Step 5: Run focused tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_docs tests.test_config -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit profiles and docs**

```bash
git add profiles/getbill.json profiles/generic.json references/config.example.json references/SCHEDULED-TASKS.md README.md tests/test_docs.py tests/test_config.py
git commit -m "docs: describe agent model usage controls"
```

### Task 8: Full verification and review

**Files:**
- Review all files changed in Tasks 1–7.
- Modify only files required to fix verified regressions.

- [ ] **Step 1: Run formatting and syntax checks**

Run:

```bash
python3 -m compileall -q scripts bin tests
bash -n bin/pitcrew-codex.sh bin/configure.sh bin/install.sh
git diff --check e133ab5..HEAD
```

Expected: exit code `0` for every command and no diff-check output.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Exercise dry-run model resolution**

Using a temporary `CODEX_HOME`, initialize the generic profile and run:

```bash
bin/pitcrew-codex.sh research-run example --dry-run
```

Expected output includes:

```text
model=gpt-5.6-terra
```

Change the runtime override to Luna through the tested configuration helper and
repeat. Expected output includes `model=gpt-5.6-luna`.

- [ ] **Step 4: Review the final diff against the spec**

Check each design requirement explicitly:

- configured and latest models are distinct;
- all 13 defaults match the spec;
- changing a model interrupts and immediately relaunches;
- no JSONL transcript is persisted;
- legacy history remains readable;
- mixed measured/unmeasured history is labelled;
- costs use each run's recorded model and all four price categories;
- browser input cannot inject role, model, command, path, or price;
- estimates are labelled as API equivalents, not subscription billing.

Expected: every item is backed by implementation and a named automated test.

- [ ] **Step 5: Request a read-only code review**

Use `superpowers:requesting-code-review`. Under the repository's delegation
policy, use a `lean-reviewer` for the non-trivial final diff. Ask it to review
spec compliance, process-control failure semantics, JSONL parsing, atomic
configuration writes, pricing correctness, and DOM safety without editing files.

Expected: no unresolved high- or medium-severity findings.

- [ ] **Step 6: Apply review fixes with focused regression tests**

For each confirmed finding, add or adjust the smallest failing test, run it to
observe the failure, implement the focused fix, then rerun that test and the
full suite.

- [ ] **Step 7: Commit verified review fixes if needed**

If review produced confirmed fixes:

```bash
git add \
  scripts/pitcrew_models.py scripts/pitcrew_config.py \
  scripts/pitcrew_locked_exec.py scripts/pitcrew_history.py \
  scripts/pitcrew_dashboard.py bin/pitcrew-codex.sh bin/pitcrew-dashboard \
  dashboard/index.html dashboard/app.js dashboard/styles.css \
  tests/test_models.py tests/test_config.py tests/test_cli.py \
  tests/test_history.py tests/test_dashboard.py
git commit -m "fix: address agent usage review findings"
```

If no fixes were needed, do not create an empty commit.

- [ ] **Step 8: Run final verification**

Run:

```bash
python3 -m unittest discover -s tests -v
git status --short
```

Expected: all tests pass. Status contains no uncommitted feature files; any
pre-existing unrelated user changes remain untouched.
