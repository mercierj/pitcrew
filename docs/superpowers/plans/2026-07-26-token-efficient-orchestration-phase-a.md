# Token-Efficient Orchestration Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop provably empty scheduled runs before Codex starts, pin reasoning effort per role, and record exact structured outcomes without changing the model used for substantive work.

**Architecture:** A new read-only eligibility module returns `eligible`, `empty`, or `unavailable`; only `empty` may suppress Codex. The shell runner keeps its existing provider cooldown first, invokes the eligibility probe only for untargeted scheduled runs, then passes explicit model settings and a JSON schema to Codex. Both pre-model gates and invoked runs write backward-compatible history records.

**Tech Stack:** Python 3 standard library, Bash, JSON Schema consumed by `codex exec`, `unittest`, GitLab REST through `glab api`.

---

## Scope boundary

This plan implements Phase A of
`docs/superpowers/specs/2026-07-26-token-efficient-agent-orchestration-design.md`.
It deliberately keeps every configured execution model unchanged.

The first eligibility implementation covers the frequent queue-backed roles:

- `implementer-run`
- `reviewer-run`
- `validator-run`
- `investigate-run`
- `unblock`
- `manager-run`

For fingerprint-, browser-, deployment-, and lifecycle-driven roles, the engine
returns `unavailable`; the runner therefore preserves the current Codex launch.
Phase B will add repository fingerprints, adaptive backoff, and pending-decision
cadence. Phase C will add measured model experiments and progressive skill
loading.

## File map

- Create `scripts/pitcrew_eligibility.py`: pure decision logic, local-source
  probes, and a bounded read-only GitLab client.
- Create `references/run-result.schema.json`: strict final response contract for
  scheduled Codex runs.
- Create `tests/test_eligibility.py`: isolated tri-state probe tests using an
  injected provider runner; no live network.
- Modify `scripts/pitcrew_models.py`: defaults and resolvers for reasoning effort
  and routing mode.
- Modify `scripts/pitcrew_config.py`: validate the extended agent contract,
  preserve optional settings on model updates, and expose CLI resolvers.
- Modify `scripts/pitcrew_history.py`: validate optional orchestration metadata
  and provide a locked helper for no-model records.
- Modify `scripts/pitcrew_locked_exec.py`: accept orchestration metadata and
  normalize structured final status into history.
- Modify `scripts/pitcrew_preflight.py`: record an already-computed gate result
  through the shared history helper.
- Modify `bin/pitcrew-codex.sh`: order the gates, pass explicit reasoning and
  output schema, and forward eligibility metadata.
- Modify `profiles/getbill.json`, `profiles/generic.json`, and
  `references/config.example.json`: pin every shipped role's effort and
  `observe` routing mode while retaining its current model.
- Modify `tests/test_models.py`, `tests/test_config.py`,
  `tests/test_history.py`, `tests/test_preflight.py`, and `tests/test_cli.py`:
  contract and end-to-end regression coverage.
- Modify `README.md`: document the new settings and fail-open-on-uncertainty
  behavior.

### Task 1: Pin per-role reasoning without changing execution models

**Files:**

- Modify: `tests/test_models.py`
- Modify: `tests/test_config.py`
- Modify: `scripts/pitcrew_models.py`
- Modify: `scripts/pitcrew_config.py`

- [ ] **Step 1: Write failing resolver tests**

Add imports for `DEFAULT_REASONING_EFFORTS`, `resolve_reasoning_effort`, and
`resolve_routing_mode` to `tests/test_models.py`, then add:

```python
def test_reasoning_effort_uses_role_default_and_runtime_override(self):
    self.assertEqual(
        "high",
        resolve_reasoning_effort({}, "reviewer-run"),
    )
    self.assertEqual(
        "medium",
        resolve_reasoning_effort(
            {"agents": {"reviewer-run": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
            }}},
            "reviewer-run",
        ),
    )

def test_routing_mode_defaults_to_observe(self):
    self.assertEqual("observe", resolve_routing_mode({}, "manager-run"))
    self.assertEqual(
        "fixed",
        resolve_routing_mode(
            {"agents": {"manager-run": {
                "model": "gpt-5.6-luna",
                "routing_mode": "fixed",
            }}},
            "manager-run",
        ),
    )

def test_every_role_has_a_reasoning_default(self):
    self.assertEqual(set(DEFAULT_MODELS), set(DEFAULT_REASONING_EFFORTS))
```

- [ ] **Step 2: Run the focused model tests and observe the import failure**

Run:

```bash
python3 -m unittest tests.test_models -v
```

Expected: `ImportError` for the new reasoning symbols.

- [ ] **Step 3: Add reasoning and routing resolvers**

Add to `scripts/pitcrew_models.py` immediately after `DEFAULT_MODELS`:

```python
REASONING_EFFORTS = {"low", "medium", "high"}
ROUTING_MODES = {"fixed", "observe"}

DEFAULT_REASONING_EFFORTS = {
    "security-run": "high",
    "product-discovery-run": "medium",
    "research-run": "medium",
    "manager-run": "low",
    "implementer-run": "high",
    "reviewer-run": "high",
    "validator-run": "medium",
    "investigate-run": "high",
    "stale-sweep": "low",
    "qa-run": "medium",
    "coverage-run": "medium",
    "dev-verify-run": "medium",
    "ops-run": "low",
    "unblock": "medium",
    "releaser-run": "medium",
}


def _agent_entry(config: Mapping, skill: str) -> Mapping:
    if skill not in DEFAULT_MODELS:
        raise ValueError(f"unknown role: {skill}")
    agents = config.get("agents", {})
    entry = agents.get(skill, {}) if isinstance(agents, Mapping) else {}
    return entry if isinstance(entry, Mapping) else {}


def resolve_reasoning_effort(config: Mapping, skill: str) -> str:
    entry = _agent_entry(config, skill)
    return entry.get("reasoning_effort", DEFAULT_REASONING_EFFORTS[skill])


def resolve_routing_mode(config: Mapping, skill: str) -> str:
    entry = _agent_entry(config, skill)
    return entry.get("routing_mode", "observe")
```

Refactor `resolve_model` to reuse `_agent_entry`:

```python
def resolve_model(config: Mapping, skill: str) -> str:
    return _agent_entry(config, skill).get("model", DEFAULT_MODELS[skill])
```

- [ ] **Step 4: Run the model tests**

Run:

```bash
python3 -m unittest tests.test_models -v
```

Expected: all tests pass.

- [ ] **Step 5: Write failing configuration-contract tests**

Extend the model import in `tests/test_config.py`:

```python
from scripts.pitcrew_models import DEFAULT_MODELS, DEFAULT_REASONING_EFFORTS
```

Then add:

```python
def test_agents_accept_reasoning_effort_and_routing_mode(self):
    profile = self.getbill_profile()
    profile["agents"]["reviewer-run"] = {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "routing_mode": "observe",
    }
    validate(profile)

def test_agents_reject_unknown_settings(self):
    profile = self.getbill_profile()
    profile["agents"]["reviewer-run"]["surprise"] = True
    with self.assertRaisesRegex(ConfigError, "unsupported setting"):
        validate(profile)

def test_runtime_model_update_preserves_agent_settings(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {"CODEX_HOME": str(Path(temp).resolve())}
        destination = write_project(
            ROOT / "profiles/getbill.json", "getbill", env
        )
        config = json.loads(destination.read_text(encoding="utf-8"))
        config["agents"]["research-run"] = {
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "routing_mode": "observe",
        }
        destination.write_text(json.dumps(config), encoding="utf-8")

        update_runtime_model(
            "getbill", "research-run", "gpt-5.6-luna", env
        )

        updated = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
                "routing_mode": "observe",
            },
            updated["agents"]["research-run"],
        )
```

For the first two tests, load a mutable profile with:

```python
profile = json.loads(
    (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
)
```

- [ ] **Step 6: Run configuration tests and observe validation failures**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: the extended entry is rejected and the update drops optional keys.

- [ ] **Step 7: Extend configuration validation and CLI resolution**

Import the new constants/resolvers in both import branches of
`scripts/pitcrew_config.py`:

```python
from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    MODEL_CATALOG,
    REASONING_EFFORTS,
    ROUTING_MODES,
    resolve_model,
    resolve_reasoning_effort,
    resolve_routing_mode,
)
```

Replace the exact-key agent validation with:

```python
allowed_agent_keys = {"model", "reasoning_effort", "routing_mode"}
for skill, entry in agents.items():
    if skill not in DEFAULT_MODELS:
        raise ConfigError(f"agents.{skill} is unsupported")
    if not isinstance(entry, Mapping):
        raise ConfigError(f"agents.{skill} must be an object")
    unknown = set(entry) - allowed_agent_keys
    if unknown:
        raise ConfigError(
            f"agents.{skill} has unsupported setting: {sorted(unknown)[0]}"
        )
    if entry.get("model") not in MODEL_CATALOG:
        raise ConfigError(f"agents.{skill}.model is unsupported")
    if (
        "reasoning_effort" in entry
        and entry["reasoning_effort"] not in REASONING_EFFORTS
    ):
        raise ConfigError(f"agents.{skill}.reasoning_effort is unsupported")
    if "routing_mode" in entry and entry["routing_mode"] not in ROUTING_MODES:
        raise ConfigError(f"agents.{skill}.routing_mode is unsupported")
```

In the locked model update, preserve the existing entry:

```python
agents = dict(config.get("agents", {}))
entry = dict(agents.get(skill, {}))
entry["model"] = model
agents[skill] = entry
updated["agents"] = agents
```

Add `reasoning` and `routing-mode` subcommands beside `model`, each requiring
`--project` and `--skill`. Their handlers load the selected project config and
print `resolve_reasoning_effort(config, skill)` or
`resolve_routing_mode(config, skill)`.

- [ ] **Step 8: Run model and configuration tests**

Run:

```bash
python3 -m unittest tests.test_models tests.test_config -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit the settings contract**

```bash
git add scripts/pitcrew_models.py scripts/pitcrew_config.py tests/test_models.py tests/test_config.py
git commit -m "feat: pin reasoning effort per pitcrew role"
```

### Task 2: Add a strict scheduled-run result contract

**Files:**

- Create: `references/run-result.schema.json`
- Modify: `tests/test_cli.py`
- Modify: `bin/pitcrew-codex.sh`

- [ ] **Step 1: Write a failing fake-Codex argument test**

Add to `tests/test_cli.py`:

```python
def test_scheduled_runner_passes_output_schema(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        env = {
            **os.environ,
            "HOME": str(root),
            "CODEX_HOME": str(root / ".codex"),
            "FAKE_CODEX_ARGS": str(root / "codex-args.json"),
        }
        configured = self.run_cli(
            "bin/configure.sh", "getbill", "--profile", "getbill", env=env
        )
        self.assertEqual(0, configured.returncode, configured.stderr)
        fake_codex = root / "fake-codex"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['FAKE_CODEX_ARGS']).write_text("
            "json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "for index, value in enumerate(sys.argv):\n"
            "    if value == '--output-last-message':\n"
            "        pathlib.Path(sys.argv[index + 1]).write_text("
            "'{\"status\":\"noop\",\"reason\":\"no eligible item\","
            "\"project\":\"getbill\",\"skill\":\"research-run\","
            "\"target_id\":null,\"did_work\":false,\"work_kind\":\"none\","
            "\"quality_outcome\":\"not-applicable\","
            "\"next_action\":\"wait\"}', encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        env["CODEX_BIN"] = str(fake_codex)

        result = self.run_cli(
            "bin/pitcrew-codex.sh",
            "research-run",
            "getbill",
            "--scheduled",
            env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        arguments = json.loads(Path(env["FAKE_CODEX_ARGS"]).read_text())
        schema_index = arguments.index("--output-schema")
        self.assertEqual(
            str(ROOT / "references/run-result.schema.json"),
            arguments[schema_index + 1],
        )
```

- [ ] **Step 2: Run the focused CLI test and observe the missing argument**

Run:

```bash
python3 -m unittest tests.test_cli.CliTest.test_scheduled_runner_passes_output_schema -v
```

Expected: failure because `--output-schema` is absent.

- [ ] **Step 3: Create the JSON Schema**

Create `references/run-result.schema.json` with:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "status",
    "reason",
    "project",
    "skill",
    "target_id",
    "did_work",
    "work_kind",
    "quality_outcome",
    "next_action"
  ],
  "properties": {
    "status": {
      "type": "string",
      "enum": ["success", "noop", "blocked", "failed"]
    },
    "reason": {"type": "string", "minLength": 1},
    "project": {"type": "string", "minLength": 1},
    "skill": {"type": "string", "minLength": 1},
    "target_id": {"type": ["string", "null"]},
    "did_work": {"type": "boolean"},
    "work_kind": {
      "type": "string",
      "enum": [
        "none",
        "implementation",
        "review",
        "validation",
        "investigation",
        "triage",
        "research",
        "security",
        "product",
        "operations",
        "release",
        "cleanup"
      ]
    },
    "quality_outcome": {"type": "string", "minLength": 1},
    "next_action": {"type": "string", "minLength": 1}
  }
}
```

- [ ] **Step 4: Pass the schema to scheduled Codex**

In the scheduled `CODEX_ARGS` block in `bin/pitcrew-codex.sh`, add:

```bash
--output-schema "$REPO_ROOT/references/run-result.schema.json"
```

Do not add it to unscheduled interactive runs.

- [ ] **Step 5: Run the focused CLI test**

Run:

```bash
python3 -m unittest tests.test_cli.CliTest.test_scheduled_runner_passes_output_schema -v
```

Expected: pass.

- [ ] **Step 6: Commit the result schema**

```bash
git add references/run-result.schema.json bin/pitcrew-codex.sh tests/test_cli.py
git commit -m "feat: require structured scheduled run results"
```

### Task 3: Normalize invoked-run history from the structured result

**Files:**

- Modify: `tests/test_history.py`
- Modify: `tests/test_cli.py`
- Modify: `scripts/pitcrew_history.py`
- Modify: `scripts/pitcrew_locked_exec.py`

- [ ] **Step 1: Write failing history metadata tests**

Add to `tests/test_history.py`:

```python
def test_history_round_trips_valid_orchestration_metadata(self):
    with tempfile.TemporaryDirectory() as temp:
        now = "2026-07-26T12:00:00Z"
        store = HistoryStore(Path(temp) / "history.jsonl")
        record = _record(
            "reviewer-run",
            now,
            model_invoked=True,
            reasoning_effort="high",
            routing_mode="observe",
            candidate_model="gpt-5.6-terra",
            routing_reason="observation only",
            target_id="getbill1/getbill!123",
            work_kind="review",
            did_work=True,
            quality_outcome="signed-off",
            gate_decision="eligible",
            gate_reason="open authored merge request",
        )
        store.append(record, now=now)
        self.assertEqual(record, store.read(now=now)[0])

def test_history_drops_invalid_optional_metadata(self):
    with tempfile.TemporaryDirectory() as temp:
        now = "2026-07-26T12:00:00Z"
        store = HistoryStore(Path(temp) / "history.jsonl")
        store.append(
            _record(
                "reviewer-run",
                now,
                model_invoked="yes",
                reasoning_effort="extreme",
                did_work=1,
            ),
            now=now,
        )
        result = store.read(now=now)[0]
        self.assertNotIn("model_invoked", result)
        self.assertNotIn("reasoning_effort", result)
        self.assertNotIn("did_work", result)
```

- [ ] **Step 2: Run history tests and observe invalid metadata being retained**

Run:

```bash
python3 -m unittest tests.test_history -v
```

Expected: `test_history_drops_invalid_optional_metadata` fails.

- [ ] **Step 3: Validate optional history metadata**

Add to `_validate_record` in `scripts/pitcrew_history.py`, after usage
normalization:

```python
for field in (
    "routing_reason",
    "target_id",
    "work_kind",
    "quality_outcome",
    "gate_decision",
    "gate_reason",
):
    value = normalized.get(field)
    if field in normalized and (
        not isinstance(value, str) or not value
    ):
        normalized.pop(field, None)
for field in ("model_invoked", "did_work"):
    if field in normalized and not isinstance(normalized[field], bool):
        normalized.pop(field, None)
if normalized.get("reasoning_effort") not in {"low", "medium", "high"}:
    normalized.pop("reasoning_effort", None)
if normalized.get("routing_mode") not in {"fixed", "observe"}:
    normalized.pop("routing_mode", None)
candidate_model = normalized.get("candidate_model")
if candidate_model not in MODEL_CATALOG:
    normalized.pop("candidate_model", None)
```

Absence remains valid for legacy records.

- [ ] **Step 4: Run history tests**

Run:

```bash
python3 -m unittest tests.test_history -v
```

Expected: all pass.

- [ ] **Step 5: Extend the scheduled schema test with failing history assertions**

The fake in
`CliTest.test_scheduled_runner_passes_output_schema` already writes this exact
final summary:

```json
{"status":"noop","reason":"no eligible item","project":"getbill","skill":"research-run","target_id":null,"did_work":false,"work_kind":"none","quality_outcome":"not-applicable","next_action":"wait"}
```

After its schema-argument assertions, add:

```python
self.assertTrue((root / ".codex/pitcrew/getbill/history.jsonl").is_file())
record = json.loads(
    (root / ".codex/pitcrew/getbill/history.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()[-1]
)
self.assertEqual("noop", record["outcome"])
self.assertTrue(record["model_invoked"])
self.assertEqual("medium", record["reasoning_effort"])
self.assertFalse(record["did_work"])
self.assertEqual("none", record["work_kind"])
self.assertEqual("not-applicable", record["quality_outcome"])
```

- [ ] **Step 6: Run the extended test and observe `outcome=success`**

Run:

```bash
python3 -m unittest \
  tests.test_cli.CliTest.test_scheduled_runner_passes_output_schema \
  -v
```

Expected: failure because the process exit code currently wins over the
structured `status`.

- [ ] **Step 7: Add structured summary normalization**

In `scripts/pitcrew_locked_exec.py`, add CLI arguments:

```python
result.add_argument(
    "--reasoning-effort",
    choices=("low", "medium", "high"),
    required=True,
)
result.add_argument("--routing-mode", choices=("fixed", "observe"), required=True)
result.add_argument("--candidate-model")
result.add_argument("--routing-reason")
result.add_argument("--target-id")
result.add_argument("--gate-decision")
result.add_argument("--gate-reason")
```

Add:

```python
def parse_structured_summary(summary: str) -> dict | None:
    try:
        value = json.loads(summary)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status not in {"success", "noop", "blocked", "failed"}:
        return None
    return value


def normalized_outcome(return_code: int, summary: dict | None) -> str:
    if return_code < 0:
        return "interrupted"
    if return_code != 0:
        return "failed"
    if summary is None:
        return "failed"
    if summary["status"] in {"noop", "blocked"}:
        return "noop"
    return "failed" if summary["status"] == "failed" else "success"
```

After `write_fallback_summary`, read and decode the summary once:

```python
summary_text = read_summary(args.summary_file)
structured = parse_structured_summary(summary_text)
outcome = normalized_outcome(return_code, structured)
record = {
    "project": args.project,
    "skill": args.skill,
    "model": args.model,
    "model_invoked": True,
    "reasoning_effort": args.reasoning_effort,
    "routing_mode": args.routing_mode,
    "started_at": started_at,
    "finished_at": utc_now(),
    "duration_ms": (time.monotonic_ns() - started_monotonic) // 1_000_000,
    "outcome": outcome,
    "exit_code": return_code,
    "summary": summary_text,
    **({"usage": usage} if usage is not None else {}),
}
```

Copy non-empty `target_id`, `work_kind`, `quality_outcome`, and boolean
`did_work` from `structured` into `record`. Copy non-empty optional runner
arguments into their corresponding history fields. Use the same base metadata
for lock-overlap and launch-failure records, with `model_invoked=False` for a
lock overlap and `model_invoked=True` after a launch attempt begins.

Update existing scheduled fake-Codex fixtures in `tests/test_cli.py` so every
successful fake writes the nine required schema fields. Fakes intentionally
testing absent output keep their current behavior and must now produce a failed
history outcome.

- [ ] **Step 8: Forward metadata from the shell runner**

Resolve `REASONING_EFFORT` and `ROUTING_MODE` through the new config CLI.
For Phase A, set `CANDIDATE_MODEL="$MODEL"` and
`ROUTING_REASON="baseline retained during observation"`. Pass all values to
`pitcrew_locked_exec.py`:

```bash
--reasoning-effort "$REASONING_EFFORT" \
--routing-mode "$ROUTING_MODE" \
--candidate-model "$CANDIDATE_MODEL" \
--routing-reason "$ROUTING_REASON" \
--target-id "$TARGET" \
--gate-decision "$GATE_DECISION" \
--gate-reason "$GATE_REASON" \
```

Also add the explicit Codex override:

```bash
-c "model_reasoning_effort=\"$REASONING_EFFORT\""
```

The dry-run output must print `reasoning_effort` and `routing_mode`.

- [ ] **Step 9: Run history and CLI tests**

Run:

```bash
python3 -m unittest tests.test_history tests.test_cli -v
```

Expected: all pass.

- [ ] **Step 10: Commit normalized history**

```bash
git add scripts/pitcrew_history.py scripts/pitcrew_locked_exec.py bin/pitcrew-codex.sh tests/test_history.py tests/test_cli.py
git commit -m "feat: record exact scheduled run outcomes"
```

### Task 4: Implement conservative tri-state eligibility probes

**Files:**

- Create: `scripts/pitcrew_eligibility.py`
- Create: `tests/test_eligibility.py`

- [ ] **Step 1: Write failing decision-shape and provider tests**

Create `tests/test_eligibility.py` with:

```python
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_eligibility import decide


ROOT = Path(__file__).resolve().parents[1]


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self):
        return json.loads(
            (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def provider_json(value):
        def run(command):
            return subprocess.CompletedProcess(
                command, 0, json.dumps(value), ""
            )
        return run

def test_provider_error_is_unavailable(self):
    decision = decide(
        self.config(),
        "implementer-run",
        runtime_dir=self.runtime,
        provider_run=lambda command: subprocess.CompletedProcess(
            command, 1, "", "authentication failed"
        ),
    )
    self.assertEqual("unavailable", decision["decision"])
    self.assertIsNone(decision["target_id"])

def test_no_agent_issue_is_empty(self):
    decision = decide(
        self.config(),
        "implementer-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("empty", decision["decision"])

def test_implementer_selects_first_todo_issue(self):
    issues = [
        {"iid": 9, "labels": ["pitcrew-agent", "pitcrew-state::review"]},
        {"iid": 4, "labels": ["pitcrew-agent", "pitcrew-state::todo"]},
    ]
    decision = decide(
        self.config(),
        "implementer-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json(issues),
    )
    self.assertEqual("eligible", decision["decision"])
    self.assertEqual("getbill1/getbill#4", decision["target_id"])

def test_investigate_requires_route_and_todo_labels(self):
    issues = [
        {"iid": 2, "labels": ["pitcrew-agent", "pitcrew-state::todo"]},
        {
            "iid": 3,
            "labels": [
                "pitcrew-agent",
                "pitcrew-route::investigate",
                "pitcrew-state::todo",
            ],
        },
    ]
    decision = decide(
        self.config(),
        "investigate-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json(issues),
    )
    self.assertEqual("getbill1/getbill#3", decision["target_id"])

def test_reviewer_empty_requires_successful_empty_mr_response(self):
    decision = decide(
        self.config(),
        "reviewer-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("empty", decision["decision"])

def test_pending_unblock_question_is_empty_without_provider_call(self):
    (self.runtime / "unblock-state.json").write_text(
        json.dumps({
            "asked": {},
            "pending_question": {
                "ticket_id": "getbill1/getbill#5",
                "status": "blocked",
                "question": "Choose a scope",
                "choices": ["A", "B"],
            },
            "history": [],
        }),
        encoding="utf-8",
    )
    decision = decide(
        self.config(),
        "unblock",
        runtime_dir=self.runtime,
        provider_run=lambda command: self.fail("provider must not run"),
    )
    self.assertEqual("empty", decision["decision"])
    self.assertIn("human decision", decision["reason"])

def test_unsupported_role_is_unavailable(self):
    decision = decide(
        self.config(),
        "research-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("unavailable", decision["decision"])

def test_validator_selects_review_issue(self):
    decision = decide(
        self.config(),
        "validator-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([
            {"iid": 8, "labels": [
                "pitcrew-agent", "pitcrew-state::review"
            ]},
        ]),
    )
    self.assertEqual("getbill1/getbill#8", decision["target_id"])

def test_unblock_selects_blocked_issue_without_pending_question(self):
    decision = decide(
        self.config(),
        "unblock",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([
            {"iid": 6, "labels": [
                "pitcrew-agent", "pitcrew-state::blocked"
            ]},
        ]),
    )
    self.assertEqual("eligible", decision["decision"])
    self.assertEqual("getbill1/getbill#6", decision["target_id"])

def test_malformed_provider_json_is_unavailable(self):
    decision = decide(
        self.config(),
        "reviewer-run",
        runtime_dir=self.runtime,
        provider_run=lambda command: subprocess.CompletedProcess(
            command, 0, "{not-json", ""
        ),
    )
    self.assertEqual("unavailable", decision["decision"])

def test_issue_candidates_are_sorted_by_iid(self):
    decision = decide(
        self.config(),
        "validator-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([
            {"iid": 12, "labels": [
                "pitcrew-agent", "pitcrew-state::review"
            ]},
            {"iid": 5, "labels": [
                "pitcrew-agent", "pitcrew-state::review"
            ]},
        ]),
    )
    self.assertEqual("getbill1/getbill#5", decision["target_id"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test module and observe the import failure**

Run:

```bash
python3 -m unittest tests.test_eligibility -v
```

Expected: import failure for `scripts.pitcrew_eligibility`.

- [ ] **Step 3: Implement the decision contract and GitLab issue probes**

Create `scripts/pitcrew_eligibility.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlencode

if __package__:
    from scripts.pitcrew_config import load_runtime_config, runtime_root
else:
    from pitcrew_config import load_runtime_config, runtime_root


ProviderRun = Callable[[list[str]], subprocess.CompletedProcess[str]]
QUEUE_ROLES = {
    "implementer-run",
    "validator-run",
    "investigate-run",
    "unblock",
}


def result(
    decision: str,
    config: Mapping,
    skill: str,
    *,
    target_id: str | None,
    reason: str,
    fingerprint_source: object | None = None,
) -> dict:
    fingerprint = None
    if fingerprint_source is not None:
        serialized = json.dumps(
            fingerprint_source, sort_keys=True, separators=(",", ":")
        )
        fingerprint = "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()
    return {
        "decision": decision,
        "project": config["project_name"],
        "skill": skill,
        "target_id": target_id,
        "fingerprint": fingerprint,
        "reason": reason,
    }


def default_provider_run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def gitlab_items(
    config: Mapping,
    endpoint: str,
    provider_run: ProviderRun,
) -> list[dict] | None:
    host = config["gitlab"]["host"]
    binary = os.environ.get("GLAB_BIN", "glab")
    try:
        completed = provider_run([binary, "api", "--hostname", host, endpoint])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return value


def issue_decision(
    config: Mapping,
    skill: str,
    provider_run: ProviderRun,
) -> dict:
    gitlab = config["gitlab"]
    tracker = gitlab["tracker"]
    labels = tracker["labels"]
    states = tracker["states"]
    endpoint = (
        f"projects/{quote(str(gitlab['project_id']), safe='')}/issues?"
        + urlencode({
            "state": "opened",
            "labels": labels["agent"],
            "per_page": 100,
            "order_by": "iid",
            "sort": "asc",
        })
    )
    issues = gitlab_items(config, endpoint, provider_run)
    if issues is None:
        return result(
            "unavailable",
            config,
            skill,
            target_id=None,
            reason="configured GitLab issue probe failed",
        )
    required = {
        "implementer-run": ({states["todo"]}, {states["review"]}),
        "validator-run": ({states["review"]},),
        "investigate-run": ({states["todo"], labels["investigate"]},),
        "unblock": ({states["blocked"]},),
    }[skill]
    candidates = []
    for issue in issues:
        iid = issue.get("iid")
        item_labels = issue.get("labels")
        if (
            isinstance(iid, int)
            and isinstance(item_labels, list)
            and all(isinstance(label, str) for label in item_labels)
            and any(expected.issubset(set(item_labels)) for expected in required)
        ):
            candidates.append(issue)
    candidates.sort(key=lambda issue: issue["iid"])
    if not candidates:
        return result(
            "empty",
            config,
            skill,
            target_id=None,
            reason="no eligible configured GitLab issue",
            fingerprint_source=issues,
        )
    target = (
        f"{tracker['ticket_prefix']}{candidates[0]['iid']}"
    )
    return result(
        "eligible",
        config,
        skill,
        target_id=target,
        reason="first provider-ordered issue is eligible",
        fingerprint_source=candidates,
    )
```

- [ ] **Step 4: Implement reviewer and pending-question probes**

Add:

```python
def pending_unblock(runtime_dir: Path) -> bool | None:
    try:
        value = json.loads(
            (runtime_dir / "unblock-state.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError):
        return None
    pending = value.get("pending_question") if isinstance(value, dict) else None
    return isinstance(pending, dict) and pending.get("status") == "blocked"


def reviewer_decision(
    config: Mapping,
    skill: str,
    provider_run: ProviderRun,
) -> dict:
    gitlab = config["gitlab"]
    endpoint = (
        f"projects/{quote(str(gitlab['project_id']), safe='')}/merge_requests?"
        + urlencode({
            "state": "opened",
            "author_username": gitlab["user"],
            "per_page": 100,
            "order_by": "updated_at",
            "sort": "asc",
        })
    )
    merge_requests = gitlab_items(config, endpoint, provider_run)
    if merge_requests is None:
        return result(
            "unavailable",
            config,
            skill,
            target_id=None,
            reason="configured GitLab merge-request probe failed",
        )
    candidates = [
        item for item in merge_requests
        if isinstance(item.get("iid"), int)
    ]
    if not candidates:
        return result(
            "empty",
            config,
            skill,
            target_id=None,
            reason="no authored open merge request requires review",
            fingerprint_source=merge_requests,
        )
    target = f"{gitlab['project_path']}!{candidates[0]['iid']}"
    return result(
        "eligible",
        config,
        skill,
        target_id=target,
        reason="an authored open merge request requires review",
        fingerprint_source=candidates,
    )
```

Before the `unblock` issue probe, call `pending_unblock`. A `True` result returns
`empty` with reason `a human decision is pending`; `None` returns `unavailable`.

- [ ] **Step 5: Run provider probe tests**

Run:

```bash
python3 -m unittest tests.test_eligibility -v
```

Expected: provider, reviewer, and unblock tests pass; manager tests added next
remain absent.

- [ ] **Step 6: Write failing manager-source tests**

Add these cases to `tests/test_eligibility.py`:

```python
def test_manager_is_eligible_for_unfiled_research_finding(self):
    ledger = self.runtime / "research-findings.json"
    ledger.write_text(
        json.dumps({
            "source": "research-run",
            "updated_at": "2026-07-26T10:00:00Z",
            "findings": [{"key": "getbill::hygiene::dead-export"}],
        }),
        encoding="utf-8",
    )
    config = self.config()
    config["manager"] = {
        "sources": [{
            "name": "research",
            "format": "research-v1",
            "findings_json": str(ledger),
        }]
    }
    decision = decide(
        config,
        "manager-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("eligible", decision["decision"])
    self.assertEqual("getbill::hygiene::dead-export", decision["target_id"])

def test_manager_is_empty_when_every_finding_is_filed(self):
    ledger = self.runtime / "research-findings.json"
    ledger.write_text(
        json.dumps({"findings": [{"key": "known"}]}),
        encoding="utf-8",
    )
    (self.runtime / "manager-state.json").write_text(
        json.dumps({"filed": {"known": {"ticket": "#1"}}, "history": []}),
        encoding="utf-8",
    )
    config = self.config()
    config["manager"] = {
        "sources": [{
            "name": "research",
            "format": "research-v1",
            "findings_json": str(ledger),
        }]
    }
    decision = decide(
        config,
        "manager-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("empty", decision["decision"])

def test_manager_malformed_inputs_are_unavailable(self):
    cases = {
        "malformed-state": (
            "{not-json",
            {"findings": []},
            "research-v1",
        ),
        "malformed-ledger": (
            json.dumps({"filed": {}, "history": []}),
            "{not-json",
            "research-v1",
        ),
        "missing-key": (
            json.dumps({"filed": {}, "history": []}),
            {"findings": [{"title": "keyless"}]},
            "research-v1",
        ),
        "unsupported-format": (
            json.dumps({"filed": {}, "history": []}),
            {"findings": []},
            "audit-v1",
        ),
    }
    for name, (state_value, ledger_value, source_format) in cases.items():
        with self.subTest(name=name):
            ledger = self.runtime / f"{name}.json"
            ledger.write_text(
                ledger_value
                if isinstance(ledger_value, str)
                else json.dumps(ledger_value),
                encoding="utf-8",
            )
            (self.runtime / "manager-state.json").write_text(
                state_value,
                encoding="utf-8",
            )
            config = self.config()
            config["manager"] = {
                "sources": [{
                    "name": "research",
                    "format": source_format,
                    "findings_json": str(ledger),
                }]
            }
            decision = decide(
                config,
                "manager-run",
                runtime_dir=self.runtime,
                provider_run=self.provider_json([]),
            )
            self.assertEqual("unavailable", decision["decision"])

def test_manager_missing_source_is_empty(self):
    config = self.config()
    config["manager"] = {
        "sources": [{
            "name": "research",
            "format": "research-v1",
            "findings_json": str(self.runtime / "missing.json"),
        }]
    }
    decision = decide(
        config,
        "manager-run",
        runtime_dir=self.runtime,
        provider_run=self.provider_json([]),
    )
    self.assertEqual("empty", decision["decision"])
```

- [ ] **Step 7: Implement the manager probe**

Add:

```python
def load_json_object(path: Path, *, missing: dict | None) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return missing
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def manager_decision(config: Mapping, skill: str, runtime_dir: Path) -> dict:
    manager = config.get("manager")
    if not isinstance(manager, Mapping) or not isinstance(manager.get("sources"), list):
        return result(
            "unavailable", config, skill, target_id=None,
            reason="manager sources are not configured",
        )
    state = load_json_object(
        runtime_dir / "manager-state.json",
        missing={"filed": {}, "history": []},
    )
    if state is None or not isinstance(state.get("filed"), dict):
        return result(
            "unavailable", config, skill, target_id=None,
            reason="manager state is unavailable",
        )
    keys = []
    for source in manager["sources"]:
        if (
            not isinstance(source, Mapping)
            or source.get("format") != "research-v1"
            or not isinstance(source.get("findings_json"), str)
        ):
            return result(
                "unavailable", config, skill, target_id=None,
                reason="manager source format is unavailable to preflight",
            )
        ledger = load_json_object(Path(source["findings_json"]), missing={"findings": []})
        if ledger is None or not isinstance(ledger.get("findings"), list):
            return result(
                "unavailable", config, skill, target_id=None,
                reason="manager source ledger is unavailable",
            )
        for finding in ledger["findings"]:
            key = finding.get("key") if isinstance(finding, Mapping) else None
            if not isinstance(key, str) or not key:
                return result(
                    "unavailable", config, skill, target_id=None,
                    reason="manager source finding is malformed",
                )
            keys.append(key)
    unfiled = sorted(key for key in keys if key not in state["filed"])
    if unfiled:
        return result(
            "eligible", config, skill, target_id=unfiled[0],
            reason="at least one configured finding is unfiled",
            fingerprint_source=keys,
        )
    return result(
        "empty", config, skill, target_id=None,
        reason="all configured findings are already filed",
        fingerprint_source=keys,
    )
```

- [ ] **Step 8: Add the dispatcher and CLI**

Add:

```python
def decide(
    config: Mapping,
    skill: str,
    *,
    runtime_dir: Path,
    provider_run: ProviderRun = default_provider_run,
) -> dict:
    if skill == "manager-run":
        return manager_decision(config, skill, runtime_dir)
    if skill == "reviewer-run":
        return reviewer_decision(config, skill, provider_run)
    if skill == "unblock":
        pending = pending_unblock(runtime_dir)
        if pending is None:
            return result(
                "unavailable", config, skill, target_id=None,
                reason="unblock state is unavailable",
            )
        if pending:
            return result(
                "empty", config, skill, target_id=None,
                reason="a human decision is pending",
                fingerprint_source="pending-human-decision",
            )
    if skill in QUEUE_ROLES:
        return issue_decision(config, skill, provider_run)
    return result(
        "unavailable", config, skill, target_id=None,
        reason="this role has no Phase A deterministic eligibility probe",
    )
```

Add:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--project", required=True)
    check_parser.add_argument("--skill", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_runtime_config(args.project)
        decision = decide(
            config,
            args.skill,
            runtime_dir=runtime_root() / args.project,
        )
    except (KeyError, OSError, ValueError) as error:
        print(f"pitcrew eligibility: {error}", file=sys.stderr)
        return 2
    print(json.dumps(decision, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

A decision of `unavailable` returns exit 0 because the shell runner must
continue to Codex.

- [ ] **Step 9: Run all eligibility tests**

Run:

```bash
python3 -m unittest tests.test_eligibility -v
```

Expected: all pass without network access.

- [ ] **Step 10: Commit the eligibility engine**

```bash
git add scripts/pitcrew_eligibility.py tests/test_eligibility.py
git commit -m "feat: add conservative scheduled eligibility probes"
```

### Task 5: Record pre-model gates and integrate eligibility into the runner

**Files:**

- Modify: `tests/test_preflight.py`
- Modify: `tests/test_cli.py`
- Modify: `scripts/pitcrew_history.py`
- Modify: `scripts/pitcrew_preflight.py`
- Modify: `bin/pitcrew-codex.sh`

- [ ] **Step 1: Write a failing no-model history helper test**

Add to `tests/test_history.py`:

```python
def test_append_gate_record_marks_model_not_invoked(self):
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "history.jsonl"
        append_gate_record(
            path,
            project="getbill",
            skill="reviewer-run",
            decision="empty",
            reason="no authored open merge request requires review",
            outcome="noop",
            target_id=None,
            fingerprint="sha256:abc",
        )
        record = HistoryStore(path).read()[0]
        self.assertEqual("noop", record["outcome"])
        self.assertFalse(record["model_invoked"])
        self.assertEqual("empty", record["gate_decision"])
        self.assertNotIn("model", record)
        self.assertNotIn("usage", record)
```

- [ ] **Step 2: Run the focused history test and observe the missing helper**

Run:

```bash
python3 -m unittest \
  tests.test_history.HistoryStoreTest.test_append_gate_record_marks_model_not_invoked \
  -v
```

Expected: import failure for `append_gate_record`.

- [ ] **Step 3: Implement the locked no-model record helper**

Add to `scripts/pitcrew_history.py`:

```python
def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def append_gate_record(
    history_path: Path,
    *,
    project: str,
    skill: str,
    decision: str,
    reason: str,
    outcome: str,
    target_id: str | None,
    fingerprint: str | None,
) -> dict:
    timestamp = utc_now()
    if outcome not in {"noop", "failed"}:
        raise ValueError("pre-model outcome must be noop or failed")
    summary = {
        "status": outcome,
        "reason": reason,
        "project": project,
        "skill": skill,
        "target_id": target_id,
        "did_work": False,
        "work_kind": "none",
        "quality_outcome": "not-applicable",
        "next_action": "retry after the configured interval",
    }
    record = {
        "project": project,
        "skill": skill,
        "model_invoked": False,
        "started_at": timestamp,
        "finished_at": timestamp,
        "duration_ms": 0,
        "outcome": outcome,
        "exit_code": 0 if outcome == "noop" else 2,
        "summary": json.dumps(summary, separators=(",", ":")),
        "did_work": False,
        "work_kind": "none",
        "quality_outcome": "not-applicable",
        "gate_decision": decision,
        "gate_reason": reason,
    }
    if target_id:
        record["target_id"] = target_id
    if fingerprint:
        record["fingerprint"] = fingerprint
    HistoryStore(history_path).append(record)
    return summary
```

Add `fingerprint` to the validated optional non-empty string fields.

- [ ] **Step 4: Run history tests**

Run:

```bash
python3 -m unittest tests.test_history -v
```

Expected: all pass.

- [ ] **Step 5: Add a preflight record-gate CLI test**

Add to `tests/test_preflight.py`:

```python
def test_record_gate_writes_no_model_history(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, "CODEX_HOME": temp}
        result = self.run_helper(
            "record-gate",
            "--project", "getbill",
            "--skill", "reviewer-run",
            "--decision", "empty",
            "--reason", "no eligible item",
            env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("noop", json.loads(result.stdout)["status"])
        history = Path(temp) / "pitcrew/getbill/history.jsonl"
        record = json.loads(history.read_text(encoding="utf-8").splitlines()[-1])
        self.assertFalse(record["model_invoked"])
        self.assertEqual("empty", record["gate_decision"])
```

Run:

```bash
python3 -m unittest \
  tests.test_preflight.PreflightTest.test_record_gate_writes_no_model_history \
  -v
```

Expected: failure because `record-gate` is not a known subcommand.

- [ ] **Step 6: Expose `record-gate` in preflight**

Import `append_gate_record` and add:

```python
gate_parser = subparsers.add_parser("record-gate")
gate_parser.add_argument("--project", required=True)
gate_parser.add_argument("--skill", required=True)
gate_parser.add_argument("--decision", required=True)
gate_parser.add_argument("--reason", required=True)
gate_parser.add_argument(
    "--outcome",
    choices=("noop", "failed"),
    default="noop",
)
gate_parser.add_argument("--target-id")
gate_parser.add_argument("--fingerprint")
```

Its handler calls:

```python
result = append_gate_record(
    runtime_root() / args.project / "history.jsonl",
    project=args.project,
    skill=args.skill,
    decision=args.decision,
    reason=args.reason,
    outcome=args.outcome,
    target_id=args.target_id,
    fingerprint=args.fingerprint,
)
```

- [ ] **Step 7: Write runner integration tests**

Add these helpers inside `CliTest`:

```python
def configured_scheduled_env(self, root):
    env = {
        **os.environ,
        "HOME": str(root),
        "CODEX_HOME": str(root / ".codex"),
        "FAKE_CODEX_MARKER": str(root / "codex-started"),
    }
    configured = self.run_cli(
        "bin/configure.sh", "getbill", "--profile", "getbill", env=env
    )
    self.assertEqual(0, configured.returncode, configured.stderr)
    fake_codex = root / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['FAKE_CODEX_MARKER']).touch()\n"
        "summary = None\n"
        "for index, value in enumerate(sys.argv):\n"
        "    if value == '--output-last-message':\n"
        "        summary = pathlib.Path(sys.argv[index + 1])\n"
        "if summary is not None:\n"
        "    summary.write_text(json.dumps({"
        "'status':'noop','reason':'no eligible item',"
        "'project':'getbill','skill':'reviewer-run','target_id':None,"
        "'did_work':False,'work_kind':'none',"
        "'quality_outcome':'not-applicable','next_action':'wait'"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env["CODEX_BIN"] = str(fake_codex)
    return env

def write_fake_glab(self, root, *, stdout="", exit_code=0, marker=None):
    fake_glab = root / "fake-glab"
    lines = ["#!/usr/bin/env bash"]
    if marker is not None:
        lines.append(f"touch {marker}")
    if stdout:
        lines.append(f"printf '%s' '{stdout}'")
    lines.append(f"exit {exit_code}")
    fake_glab.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake_glab.chmod(0o755)
    return fake_glab
```

Add:

```python
def test_scheduled_empty_gate_skips_codex_and_records_history(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        env = self.configured_scheduled_env(root)
        env["GLAB_BIN"] = str(self.write_fake_glab(root, stdout="[]"))

        result = self.run_cli(
            "bin/pitcrew-codex.sh",
            "reviewer-run",
            "getbill",
            "--scheduled",
            env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("noop", json.loads(result.stdout)["status"])
        self.assertFalse(Path(env["FAKE_CODEX_MARKER"]).exists())
        history = root / ".codex/pitcrew/getbill/history.jsonl"
        record = json.loads(history.read_text(encoding="utf-8").splitlines()[-1])
        self.assertFalse(record["model_invoked"])
        self.assertEqual("empty", record["gate_decision"])

def test_scheduled_unavailable_gate_runs_codex(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        env = self.configured_scheduled_env(root)
        env["GLAB_BIN"] = str(self.write_fake_glab(root, exit_code=1))

        result = self.run_cli(
            "bin/pitcrew-codex.sh",
            "reviewer-run",
            "getbill",
            "--scheduled",
            env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(Path(env["FAKE_CODEX_MARKER"]).exists())
        history = root / ".codex/pitcrew/getbill/history.jsonl"
        record = json.loads(history.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(record["model_invoked"])
        self.assertEqual("unavailable", record["gate_decision"])

def test_scheduled_directed_target_skips_eligibility_probe(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        env = self.configured_scheduled_env(root)
        glab_marker = root / "glab-called"
        env["GLAB_BIN"] = str(
            self.write_fake_glab(root, exit_code=1, marker=glab_marker)
        )

        result = self.run_cli(
            "bin/pitcrew-codex.sh",
            "reviewer-run",
            "getbill",
            "--target",
            "getbill1/getbill#123",
            "--scheduled",
            env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(Path(env["FAKE_CODEX_MARKER"]).exists())
        self.assertFalse(glab_marker.exists())
```

- [ ] **Step 8: Run runner integration tests and observe Codex still starts**

Run:

```bash
python3 -m unittest \
  tests.test_cli.CliTest.test_scheduled_empty_gate_skips_codex_and_records_history \
  tests.test_cli.CliTest.test_scheduled_unavailable_gate_runs_codex \
  tests.test_cli.CliTest.test_scheduled_directed_target_skips_eligibility_probe \
  -v
```

Expected: the empty-gate test fails because eligibility is not yet called.

- [ ] **Step 9: Integrate the tri-state gate**

In `bin/pitcrew-codex.sh`, initialize:

```bash
TARGET_SOURCE="none"
if [[ -n "$TARGET" ]]; then
  TARGET_SOURCE="directed"
fi
GATE_DECISION="not-checked"
GATE_REASON="scheduled eligibility was not checked"
GATE_FINGERPRINT=""
```

For scheduled runs, keep provider cooldown first. Before model resolution, and
only when `TARGET` is empty:

```bash
ELIGIBILITY="$(python3 "$REPO_ROOT/scripts/pitcrew_eligibility.py" \
  check --project "$PROJECT" --skill "$SKILL")" || {
  echo "pitcrew-codex: eligibility probe is unavailable" >&2
  exit 2
}
GATE_DECISION="$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["decision"])' <<<"$ELIGIBILITY")"
GATE_REASON="$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["reason"])' <<<"$ELIGIBILITY")"
GATE_FINGERPRINT="$(python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("fingerprint") or "")' \
  <<<"$ELIGIBILITY")"
if [[ "$GATE_DECISION" == "empty" ]]; then
  GATE_ARGS=(
    record-gate
    --project "$PROJECT"
    --skill "$SKILL"
    --decision empty
    --reason "$GATE_REASON"
  )
  if [[ -n "$GATE_FINGERPRINT" ]]; then
    GATE_ARGS+=(--fingerprint "$GATE_FINGERPRINT")
  fi
  python3 "$REPO_ROOT/scripts/pitcrew_preflight.py" "${GATE_ARGS[@]}"
  exit 0
fi
if [[ "$GATE_DECISION" == "eligible" ]]; then
  TARGET="$(python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("target_id") or "")' \
    <<<"$ELIGIBILITY")"
  TARGET_SOURCE="eligibility"
fi
```

For provider cooldown, call `record-gate --decision cooldown` before printing
the existing response and exiting. For `unavailable`, retain
`GATE_DECISION=unavailable`, record its reason on the invoked run, and continue.

Split prompt target handling by source:

```bash
if [[ -n "$TARGET" && "$TARGET_SOURCE" == "directed" ]]; then
  PROMPT+=" Operate on exactly this directed target: $TARGET. Validate it with references/DIRECTED-TARGET.md before any provider lookup."
elif [[ -n "$TARGET" && "$TARGET_SOURCE" == "eligibility" ]]; then
  PROMPT+=" The read-only eligibility probe preselected this target: $TARGET. Revalidate that exact target against the skill's authoritative source before any mutation; if it is stale, return a structured noop."
fi
```

This distinction is required because `manager-run` receives a finding key, not
a human-directed tracker identifier.

Move `HISTORY_FILE="$RUNTIME_ROOT/$PROJECT/history.jsonl"` above the global
execution-state check. If the global stop is active, call `record-gate` with
`decision=stopped` before returning. If the runtime-state, provider-preflight,
or eligibility command itself exits non-zero, call `record-gate` with
`decision=error --outcome failed` before returning exit 2. This makes every
scheduled attempt observable while still treating an eligibility decision of
`unavailable` as permission to continue through the existing workflow.

- [ ] **Step 10: Run preflight, eligibility, history, and CLI tests**

Run:

```bash
python3 -m unittest \
  tests.test_preflight \
  tests.test_eligibility \
  tests.test_history \
  tests.test_cli -v
```

Expected: all pass.

- [ ] **Step 11: Commit runner gate integration**

```bash
git add bin/pitcrew-codex.sh scripts/pitcrew_history.py scripts/pitcrew_preflight.py tests/test_history.py tests/test_preflight.py tests/test_cli.py
git commit -m "feat: skip provably empty scheduled runs"
```

### Task 6: Ship explicit role settings and operator documentation

**Files:**

- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `tests/test_config.py`
- Modify: `README.md`

- [ ] **Step 1: Update the shipped-profile contract test**

Replace the exact `{role: {"model": model}}` expectation in
`tests/test_config.py` with:

```python
self.assertEqual(set(DEFAULT_MODELS), set(profile["agents"]))
for role, model in DEFAULT_MODELS.items():
    self.assertEqual(model, profile["agents"][role]["model"])
    self.assertEqual(
        DEFAULT_REASONING_EFFORTS[role],
        profile["agents"][role]["reasoning_effort"],
    )
    self.assertEqual("observe", profile["agents"][role]["routing_mode"])
```

- [ ] **Step 2: Run the profile test and observe missing settings**

Run:

```bash
python3 -m unittest \
  tests.test_config.ConfigTest.test_profiles_and_example_pin_the_complete_default_agent_mapping \
  -v
```

Expected: failure on missing `reasoning_effort`.

- [ ] **Step 3: Extend both profiles**

For every role in `profiles/getbill.json`, `profiles/generic.json`, and
`references/config.example.json`, retain the current `model`, add the matching
value from `DEFAULT_REASONING_EFFORTS`, and add:

```json
"routing_mode": "observe"
```

Examples:

```json
"reviewer-run": {
  "model": "gpt-5.6-sol",
  "reasoning_effort": "high",
  "routing_mode": "observe"
}
```

```json
"manager-run": {
  "model": "gpt-5.6-luna",
  "reasoning_effort": "low",
  "routing_mode": "observe"
}
```

- [ ] **Step 4: Document runtime behavior**

Add a concise `README.md` subsection stating:

```markdown
### Scheduled token controls

Each `agents.<role>` entry pins `model`, `reasoning_effort`, and
`routing_mode`. Scheduled runs therefore do not inherit the operator's global
reasoning setting.

Before Codex starts, queue-backed roles run a read-only eligibility probe.
Only a confirmed `empty` decision suppresses Codex. Provider errors, malformed
responses, and roles without a deterministic Phase A probe return
`unavailable` and preserve the existing workflow launch.

`routing_mode: observe` records routing evidence but does not change the
configured execution model.
```

- [ ] **Step 5: Run profile and documentation tests**

Run:

```bash
python3 -m unittest tests.test_config tests.test_docs -v
```

Expected: all pass.

- [ ] **Step 6: Commit profiles and docs**

```bash
git add profiles/getbill.json profiles/generic.json references/config.example.json tests/test_config.py README.md
git commit -m "docs: expose scheduled token controls"
```

### Task 7: Full verification and quality review

**Files:**

- Verify only; fix only files owned by Tasks 1–6 if a regression is found.

- [ ] **Step 1: Run shell syntax checks**

Run:

```bash
bash -n bin/pitcrew-codex.sh bin/configure.sh tests/run.sh
```

Expected: exit 0 with no output.

- [ ] **Step 2: Run the entire test suite**

Run:

```bash
rtk bash tests/run.sh
```

Expected: all tests and profile validations pass. If RTK reports a failure,
rerun the exact failing unittest without RTK to retain its full assertion
output.

- [ ] **Step 3: Run a deterministic empty smoke test**

In a temporary `CODEX_HOME`, configure `getbill`, set
`PITCREW_ELIGIBILITY_OVERRIDE=empty`, and use a fake `CODEX_BIN` that creates a
marker if called. Run:

```bash
bin/pitcrew-codex.sh reviewer-run getbill --scheduled
```

Expected: schema-valid `status=noop`, marker absent, one history row with
`model_invoked=false`, no `model`, and no `usage`.

- [ ] **Step 4: Run an unavailable smoke test**

Repeat with `PITCREW_ELIGIBILITY_OVERRIDE=unavailable` and a fake Codex that
writes a schema-valid noop result.

Expected: fake Codex marker present, history row has `model_invoked=true`,
`gate_decision=unavailable`, the configured model, explicit reasoning effort,
and normalized `outcome=noop`.

- [ ] **Step 5: Review the final diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only Phase A files plus pre-existing user
changes are present. Do not stage or revert unrelated dashboard work.

- [ ] **Step 6: Request a read-only review**

Use a `lean-reviewer` agent with ownership limited to the Phase A diff. Ask it
to check:

- only `empty` suppresses Codex;
- provider and parsing failures continue to Codex;
- directed targets bypass automatic selection;
- model assignments are unchanged;
- reasoning effort is explicit;
- no-model and invoked records are distinguishable;
- existing user changes are untouched.

- [ ] **Step 7: Apply review fixes test-first**

For each accepted issue, add or tighten a focused regression test, observe it
fail, make the minimal correction, and rerun the focused test plus
`rtk bash tests/run.sh`.

- [ ] **Step 8: Commit verification fixes, if any**

If review produced fixes:

```bash
git add bin/pitcrew-codex.sh scripts/pitcrew_config.py scripts/pitcrew_eligibility.py scripts/pitcrew_history.py scripts/pitcrew_locked_exec.py scripts/pitcrew_models.py scripts/pitcrew_preflight.py references/run-result.schema.json profiles/getbill.json profiles/generic.json references/config.example.json README.md tests/test_cli.py tests/test_config.py tests/test_eligibility.py tests/test_history.py tests/test_models.py tests/test_preflight.py
git commit -m "fix: harden token-efficient orchestration"
```

If no fixes were needed, do not create an empty commit.

## Phase A exit gate

Phase A is complete only when:

- the full suite passes;
- the empty and unavailable smoke tests pass;
- a read-only review finds no unresolved correctness or safety issue;
- shipped role models match their pre-change values;
- no unrelated dirty file is staged or modified by this work.

After this gate, write the separate Phase B plan for empty backoff and
fingerprint cadence. Do not enable a Terra execution cohort until the separate
Phase C observation data and rollback thresholds are accepted.
