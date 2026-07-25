# Durable Ticket Run Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace role-wide ticket launch locks with a durable, idempotent SQLite queue that runs up to three tickets per role, keeps duplicate ticket launches disabled, and synchronizes dashboard and scheduled execution state.

**Architecture:** A focused SQLite repository owns run identity, FIFO admission, capacity, and terminal state. A dispatcher owns process spawning and recovery; the shell runner executes already-claimed run IDs without re-enqueuing. The dashboard overlays local run state on cached GitLab cards and polls the local run endpoint quickly while work is active.

**Tech Stack:** Python 3.13 standard library (`sqlite3`, `subprocess`, `fcntl`, `unittest`), Bash runner, vanilla JavaScript/CSS, GitLab through the existing `glab` adapter.

**Execution context:** Work in the current checkout. Preserve the existing uncommitted changes in `dashboard/app.js`, `dashboard/styles.css`, `scripts/pitcrew_dashboard.py`, `tests/test_dashboard.py`, `tests/test_skill_contracts.py`, and `references/DIRECTED-TARGET.md`; do not create a worktree solely for this implementation.

---

## File map

- Create `scripts/pitcrew_run_store.py`: SQLite schema, idempotent admission, binding, FIFO claims, heartbeats, terminal transitions, reconciliation, retention, and capacity snapshots.
- Create `scripts/pitcrew_run_dispatcher.py`: validate queued targets, spawn run-scoped workers, record PIDs, terminate active workers, and drain capacity.
- Create `tests/test_run_store.py`: transaction, concurrency, FIFO, capacity, migration, and stale-worker coverage.
- Create `tests/test_run_dispatcher.py`: spawning, validation, failure, stop, and automatic-drain coverage.
- Modify `scripts/pitcrew_config.py` and `tests/test_config.py`: optional execution-capacity validation and defaults.
- Modify `scripts/pitcrew_locked_exec.py` and `tests/test_cli.py`: run-scoped live status, heartbeat, terminal recording, and per-run lock behavior.
- Modify `bin/pitcrew-codex.sh`: enqueue scheduled runs, execute internal coordinated runs, and pass run identity to Codex.
- Modify `bin/pitcrew-schedule.py` and `tests/test_schedule.py`: stop/resume coordinated workers and queued work.
- Modify `scripts/pitcrew_dashboard.py`: inject the coordinator/dispatcher, validate GitLab lifecycle, expose run state, remove GitLab mutations from reads, and enqueue ticket runs.
- Modify `bin/pitcrew-dashboard`: add authenticated `GET /api/runs` and `POST /api/ticket-runs`.
- Modify `dashboard/app.js` and `dashboard/styles.css`: render persistent per-ticket states, adaptive polling, capacity, and retry behavior.
- Modify `tests/test_dashboard.py`: service, HTTP, cache, asset-contract, and concurrency coverage.
- Modify `skills/implementer-run/SKILL.md`, `skills/unblock/SKILL.md`, `skills/stale-sweep/SKILL.md`, `references/DIRECTED-TARGET.md`, and `tests/test_skill_contracts.py`: require run-target binding before ticket mutation.
- Modify `README.md` and `references/SCHEDULED-TASKS.md`: document capacity, queue, states, stop/resume, and compatibility.
- Modify `tests/run.sh`: include the new deterministic unit suites if it enumerates modules explicitly.

## Task 1: Validate configurable per-role capacity

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Add these cases to `ConfigTest`:

```python
def test_execution_capacity_is_optional_and_bounded(self):
    config = self.valid_config()
    CONFIG.validate(config)

    config["execution"] = {
        "default_max_concurrent_per_skill": 3,
        "max_concurrent_per_skill": {
            "implementer-run": 4,
            "unblock": 2,
        },
    }
    CONFIG.validate(config)

    for invalid in (0, 17, True, "3"):
        broken = copy.deepcopy(config)
        broken["execution"]["default_max_concurrent_per_skill"] = invalid
        with self.assertRaisesRegex(
            CONFIG.ConfigError,
            "execution.default_max_concurrent_per_skill",
        ):
            CONFIG.validate(broken)

    broken = copy.deepcopy(config)
    broken["execution"]["max_concurrent_per_skill"]["unknown-run"] = 2
    with self.assertRaisesRegex(CONFIG.ConfigError, "execution role is unsupported"):
        CONFIG.validate(broken)

def test_execution_capacity_rejects_unknown_fields(self):
    config = self.valid_config()
    config["execution"] = {
        "default_max_concurrent_per_skill": 3,
        "max_concurrent_per_skill": {},
        "burst": 10,
    }
    with self.assertRaisesRegex(CONFIG.ConfigError, "execution contains unsupported fields"):
        CONFIG.validate(config)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_config.ConfigTest.test_execution_capacity_is_optional_and_bounded \
  tests.test_config.ConfigTest.test_execution_capacity_rejects_unknown_fields
```

Expected: FAIL because `validate` currently ignores the `execution` section.

- [ ] **Step 3: Implement validation and the capacity resolver**

Add to `scripts/pitcrew_config.py`:

```python
DEFAULT_MAX_CONCURRENT_PER_SKILL = 3
MAX_CONCURRENT_PER_SKILL = 16


def _capacity(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be an integer from 1 to 16")
    if not 1 <= value <= MAX_CONCURRENT_PER_SKILL:
        raise ConfigError(f"{field} must be an integer from 1 to 16")
    return value


def max_concurrent_for(config: Mapping[str, Any], skill: str) -> int:
    execution = config.get("execution", {})
    if not isinstance(execution, Mapping):
        raise ConfigError("execution must be an object")
    overrides = execution.get("max_concurrent_per_skill", {})
    if not isinstance(overrides, Mapping):
        raise ConfigError("execution.max_concurrent_per_skill must be an object")
    if skill in overrides:
        return _capacity(
            overrides[skill],
            f"execution.max_concurrent_per_skill.{skill}",
        )
    return _capacity(
        execution.get(
            "default_max_concurrent_per_skill",
            DEFAULT_MAX_CONCURRENT_PER_SKILL,
        ),
        "execution.default_max_concurrent_per_skill",
    )
```

Inside `validate`, accept only the two documented execution keys, reject
unknown roles against `DEFAULT_MODELS`, and call `_capacity` for the default
and every override:

```python
execution = config.get("execution", {})
if not isinstance(execution, Mapping):
    raise ConfigError("execution must be an object")
if set(execution) - {
    "default_max_concurrent_per_skill",
    "max_concurrent_per_skill",
}:
    raise ConfigError("execution contains unsupported fields")
_capacity(
    execution.get(
        "default_max_concurrent_per_skill",
        DEFAULT_MAX_CONCURRENT_PER_SKILL,
    ),
    "execution.default_max_concurrent_per_skill",
)
overrides = execution.get("max_concurrent_per_skill", {})
if not isinstance(overrides, Mapping):
    raise ConfigError("execution.max_concurrent_per_skill must be an object")
for skill, capacity in overrides.items():
    if skill not in DEFAULT_MODELS:
        raise ConfigError("execution role is unsupported")
    _capacity(capacity, f"execution.max_concurrent_per_skill.{skill}")
```

- [ ] **Step 4: Run the configuration suite**

Run:

```bash
python3 -m unittest tests.test_config
```

Expected: PASS.

- [ ] **Step 5: Commit the capacity contract**

```bash
git add scripts/pitcrew_config.py tests/test_config.py
git commit -m "feat: configure ticket run capacity"
```

## Task 2: Build the transactional SQLite run store

**Files:**
- Create: `scripts/pitcrew_run_store.py`
- Create: `tests/test_run_store.py`

- [ ] **Step 1: Write failing store tests**

Create `tests/test_run_store.py` with a temporary database and deterministic
clock. Cover idempotency, three-slot capacity, FIFO, independent roles,
binding conflicts, terminal release, stale reconciliation, and seven-day
retention. The central concurrency tests are:

```python
class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "runs.sqlite3"
        self.now = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
        self.store = STORE.RunStore(self.db, now=lambda: self.now)

    def test_same_target_is_idempotent_under_concurrent_admission(self):
        target = "https://gitlab.com/getbill1/getbill/-/issues/42"
        barrier = threading.Barrier(2)

        def enqueue():
            barrier.wait()
            return self.store.enqueue(
                project="getbill",
                skill="implementer-run",
                source="dashboard",
                target=target,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: enqueue(), range(2)))

        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual({first["created"], second["created"]}, {True, False})
        self.assertEqual(1, len(self.store.list_runs("getbill", active_only=True)))

    def test_claim_ready_enforces_capacity_and_fifo(self):
        runs = [
            self.store.enqueue(
                project="getbill",
                skill="implementer-run",
                source="dashboard",
                target=f"https://gitlab.com/getbill1/getbill/-/issues/{iid}",
            )
            for iid in range(1, 5)
        ]
        claimed = self.store.claim_ready(
            project="getbill",
            capacities={"implementer-run": 3},
        )
        self.assertEqual(
            [run["run_id"] for run in runs[:3]],
            [run["run_id"] for run in claimed],
        )
        self.assertEqual("queued", self.store.get(runs[3]["run_id"])["state"])

        self.store.finish(runs[0]["run_id"], state="succeeded")
        next_claim = self.store.claim_ready(
            project="getbill",
            capacities={"implementer-run": 3},
        )
        self.assertEqual([runs[3]["run_id"]], [run["run_id"] for run in next_claim])

    def test_bind_target_refuses_an_active_ticket_owned_by_another_run(self):
        target = "https://gitlab.com/getbill1/getbill/-/issues/42"
        targeted = self.store.enqueue(
            project="getbill",
            skill="implementer-run",
            source="dashboard",
            target=target,
        )
        scheduled = self.store.enqueue(
            project="getbill",
            skill="implementer-run",
            source="scheduled",
        )
        with self.assertRaisesRegex(STORE.RunConflict, targeted["run_id"]):
            self.store.bind_target(scheduled["run_id"], target)
```

- [ ] **Step 2: Run the new suite and confirm import failure**

Run:

```bash
python3 -m unittest tests.test_run_store
```

Expected: ERROR because `scripts.pitcrew_run_store` does not exist.

- [ ] **Step 3: Create the schema and row normalization**

Create `scripts/pitcrew_run_store.py` with:

```python
ACTIVE_STATES = ("queued", "running")
TERMINAL_STATES = ("succeeded", "failed", "cancelled")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES
RETENTION = timedelta(days=7)
STALE_HEARTBEAT = timedelta(seconds=30)
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    skill TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('dashboard', 'scheduled', 'reconcile')),
    target TEXT,
    dedupe_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    queue_sequence INTEGER NOT NULL,
    pid INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    phase TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    predecessor_run_id TEXT
);
CREATE TABLE IF NOT EXISTS project_controls (
    project TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('running', 'stopped')),
    generation INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS runs_active_dedupe
ON runs(project, dedupe_key)
WHERE state IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS runs_queue
ON runs(project, skill, state, queue_sequence);
"""
```

`RunStore.__init__` must reject a symlinked database path, create the parent
with mode `0700`, connect with `timeout=5`, set `busy_timeout=5000`, enable WAL,
set `row_factory=sqlite3.Row`, apply `SCHEMA`, and set `PRAGMA user_version=1`.
Use a fresh connection per public operation so threads do not share
connections.

`enqueue` and `claim_ready` read `project_controls` inside their
`BEGIN IMMEDIATE` transaction. A missing row means `running`; a `stopped` row
raises `RunPaused` before an insert or claim. `set_project_state(project,
state)` upserts the row and increments `generation`, making the database the
linearizable admission gate while `execution-state.json` remains the
backward-compatible runner gate.

- [ ] **Step 4: Implement atomic admission and target binding**

Use `BEGIN IMMEDIATE` and catch only the active unique-index conflict:

```python
def enqueue(self, *, project, skill, source, target=None, predecessor_run_id=None):
    run_id = str(uuid.uuid4())
    dedupe_key = f"ticket:{target}" if target else f"scheduled:{skill}"
    created_at = self._timestamp()
    with self._transaction() as connection:
        existing = connection.execute(
            """
            SELECT * FROM runs
            WHERE project = ? AND dedupe_key = ?
              AND state IN ('queued', 'running')
            """,
            (project, dedupe_key),
        ).fetchone()
        if existing is not None:
            return {**self._row(existing), "created": False}
        sequence = connection.execute(
            "SELECT COALESCE(MAX(queue_sequence), 0) + 1 FROM runs"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO runs (
                run_id, project, skill, source, target, dedupe_key, state,
                queue_sequence, created_at, phase, predecessor_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, 'En attente', ?)
            """,
            (
                run_id, project, skill, source, target, dedupe_key,
                sequence, created_at, predecessor_run_id,
            ),
        )
        return {**self._row_by_id(connection, run_id), "created": True}

def bind_target(self, run_id, target):
    with self._transaction() as connection:
        run = self._required_active(connection, run_id)
        if run["target"] == target:
            return self._row(run)
        try:
            connection.execute(
                """
                UPDATE runs
                SET target = ?, dedupe_key = ?
                WHERE run_id = ? AND target IS NULL
                  AND state IN ('queued', 'running')
                """,
                (target, f"ticket:{target}", run_id),
            )
        except sqlite3.IntegrityError as error:
            owner = connection.execute(
                """
                SELECT run_id FROM runs
                WHERE project = ? AND dedupe_key = ?
                  AND state IN ('queued', 'running')
                """,
                (run["project"], f"ticket:{target}"),
            ).fetchone()
            raise RunConflict(
                f"ticket is already active in run {owner['run_id']}"
            ) from error
        return self._row_by_id(connection, run_id)
```

- [ ] **Step 5: Implement claiming, status, finish, reconcile, snapshots, and purge**

`claim_ready` must count `running` rows per role in the same transaction, select
queued rows by `queue_sequence`, and update only the available number. Add:

```python
def finish(self, run_id, *, state, error_code=None, error_message=None):
    if state not in TERMINAL_STATES:
        raise ValueError("terminal state is required")
    with self._transaction() as connection:
        cursor = connection.execute(
            """
            UPDATE runs
            SET state = ?, finished_at = ?, heartbeat_at = ?,
                phase = ?, error_code = ?, error_message = ?
            WHERE run_id = ? AND state IN ('queued', 'running')
            """,
            (
                state,
                self._timestamp(),
                self._timestamp(),
                self._terminal_phase(state),
                error_code,
                self._public_error(error_message),
                run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RunStateError("run is already terminal or missing")
        return self._row_by_id(connection, run_id)
```

`reconcile` must use an injected `pid_alive(pid)` callback, mark dead or
heartbeat-expired running rows `failed/interrupted`, leave queued rows intact,
and purge only terminal rows older than seven days. `snapshot` returns:

```python
{
    "runs": [
        {
            "run_id": "a6fd2d17-4d33-4a86-b847-c0b50ea1b028",
            "project": "getbill",
            "skill": "implementer-run",
            "target": "https://gitlab.com/getbill1/getbill/-/issues/42",
            "state": "running",
            "queue_position": 0,
        }
    ],
    "capacity": {
        "implementer-run": {
            "running": 2,
            "queued": 1,
            "max_concurrent": 3,
        }
    },
    "has_active": True,
}
```

Queue positions count only earlier queued rows of the same project and skill.

- [ ] **Step 6: Run the store suite**

Run:

```bash
python3 -m unittest tests.test_run_store
```

Expected: PASS.

- [ ] **Step 7: Commit the durable store**

```bash
git add scripts/pitcrew_run_store.py tests/test_run_store.py
git commit -m "feat: add durable ticket run store"
```

## Task 3: Add the dispatcher and coordinated worker lifecycle

**Files:**
- Create: `scripts/pitcrew_run_dispatcher.py`
- Create: `tests/test_run_dispatcher.py`
- Modify: `scripts/pitcrew_locked_exec.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing dispatcher tests**

Use a fake store and fake `Popen` to prove that valid claimed rows spawn exact
argument arrays, stale targets become cancelled before spawn, a spawn error
becomes failed, and three slots are drained. Include:

```python
def test_drain_spawns_claimed_target_with_opaque_run_id(self):
    run = self.store.enqueue(
        project="getbill",
        skill="implementer-run",
        source="dashboard",
        target="https://gitlab.com/getbill1/getbill/-/issues/42",
    )
    spawned = []
    dispatcher = DISPATCH.RunDispatcher(
        store=self.store,
        runner=ROOT / "bin/pitcrew-codex.sh",
        process_factory=lambda args, **kwargs: spawned.append((args, kwargs))
        or SimpleNamespace(pid=4321),
        target_validator=lambda claimed: True,
    )

    dispatcher.drain(
        project="getbill",
        capacities={"implementer-run": 3},
    )

    self.assertEqual(
        [
            str(ROOT / "bin/pitcrew-codex.sh"),
            "implementer-run",
            "getbill",
            "--target",
            "https://gitlab.com/getbill1/getbill/-/issues/42",
            "--scheduled",
            "--coordinated-run",
            run["run_id"],
        ],
        spawned[0][0],
    )
    self.assertEqual(4321, self.store.get(run["run_id"])["pid"])
```

- [ ] **Step 2: Write failing locked-exec lifecycle tests**

Extend `tests/test_cli.py` to call the helper with `--run-db` and `--run-id`.
Assert the row becomes `succeeded` on exit 0, `failed` on exit 17, its live
file is run-scoped, and the per-run lock rejects only a second execution of the
same `run_id`. Two distinct run IDs for the same skill must overlap.

```python
self.assertEqual("succeeded", store.get(first_run["run_id"])["state"])
self.assertEqual("failed", store.get(failed_run["run_id"])["state"])
self.assertFalse((runtime / "live/runs" / f"{first_run['run_id']}.json").exists())
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_run_dispatcher \
  tests.test_cli.CliTest.test_coordinated_helper_records_terminal_run_state \
  tests.test_cli.CliTest.test_distinct_run_ids_of_same_skill_can_overlap
```

Expected: ERROR/FAIL because the dispatcher and coordinated helper flags do not
exist.

- [ ] **Step 4: Implement `RunDispatcher`**

Create a focused class with `drain`, `reconcile_and_drain`, and
`cancel_running`. `drain` calls `store.claim_ready`, validates each targeted
row immediately before spawn, and uses:

```python
args = [
    str(self.runner),
    run["skill"],
    run["project"],
]
if run["target"]:
    args.extend(["--target", run["target"]])
args.extend(["--scheduled", "--coordinated-run", run["run_id"]])
process = self.process_factory(
    args,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
self.store.mark_pid(run["run_id"], process.pid)
```

If validation returns false, finish with
`cancelled/stale_target`. If `Popen` raises `OSError`, finish with
`failed/spawn_failed`. After either terminal transition, continue draining so
one bad row does not block the FIFO.

`cancel_running` first records `cancel_requested`, then sends `SIGTERM` to the
worker process group through injected `killpg`; after the grace period, rows
still running become `cancelled/global_stop`.

- [ ] **Step 5: Add the dispatcher CLI**

The parser exposes `enqueue`, `bind-target`, and `drain`. It resolves the
runtime through `pitcrew_config.runtime_root`, loads the validated project
configuration, and constructs the capacity map with `max_concurrent_for`.
`enqueue` prints exactly one compact JSON result:

```python
run = store.enqueue(
    project=args.project,
    skill=args.skill,
    source="scheduled",
    target=args.target,
)
dispatcher.reconcile_and_drain(
    project=args.project,
    capacities={
        role: max_concurrent_for(config, role)
        for role in config.get("agents", {})
    },
)
print(json.dumps(
    {
        "run_id": run["run_id"],
        "state": store.get(run["run_id"])["state"],
        "queue_position": store.get(run["run_id"])["queue_position"],
        "created": run["created"],
    },
    separators=(",", ":"),
))
```

`bind-target` requires `--project`, `--run-id`, and `--target`, verifies the
row belongs to the project, calls `store.bind_target`, and prints the bound
row. `drain` performs reconciliation and capacity draining without enqueuing.
All validation errors print one safe line to stderr and exit 2.

- [ ] **Step 6: Extend `pitcrew_locked_exec.py` for coordinated rows**

Add optional parser flags:

```python
result.add_argument("--run-db", type=Path)
result.add_argument("--run-id")
result.add_argument("--heartbeat-seconds", type=float, default=2.0)
```

Require both run flags together. Use `<run-id>.lock`, publish `run_id` in live
status, heartbeat inside the child-output loop, and finalize the store:

```python
terminal = "succeeded" if child.returncode == 0 else "failed"
error_code = None if child.returncode == 0 else "command_failed"
store.finish(
    args.run_id,
    state=terminal,
    error_code=error_code,
    error_message=None if child.returncode == 0 else f"command exited {child.returncode}",
)
```

Preserve the legacy behavior when both run flags are absent so an in-flight
old runner remains compatible during rollout. Move heartbeat emission into a
callback accepted by `drain_child_output` rather than creating a second thread.

- [ ] **Step 7: Run dispatcher and CLI suites**

Run:

```bash
python3 -m unittest tests.test_run_dispatcher tests.test_cli
```

Expected: PASS.

- [ ] **Step 8: Commit process coordination**

```bash
git add \
  scripts/pitcrew_run_dispatcher.py \
  scripts/pitcrew_locked_exec.py \
  tests/test_run_dispatcher.py \
  tests/test_cli.py
git commit -m "feat: dispatch concurrent ticket workers"
```

## Task 4: Route scheduled and targeted runner invocations through the queue

**Files:**
- Modify: `bin/pitcrew-codex.sh`
- Modify: `tests/test_cli.py`
- Modify: `references/DIRECTED-TARGET.md`
- Modify: `skills/implementer-run/SKILL.md`
- Modify: `skills/unblock/SKILL.md`
- Modify: `skills/stale-sweep/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Replace the old overlap test with queue behavior tests**

Change the test that expects the second same-role run to noop. The replacement
must start four directed runs, assert three fake Codex processes start, assert
the fourth remains queued, release one, and assert the fourth starts. Add a
same-target test:

```python
first = self.run_cli(
    "bin/pitcrew-codex.sh",
    "implementer-run",
    "getbill",
    "--target",
    target,
    "--scheduled",
    env=env,
)
second = self.run_cli(
    "bin/pitcrew-codex.sh",
    "implementer-run",
    "getbill",
    "--target",
    target,
    "--scheduled",
    env=env,
)
self.assertEqual(
    json.loads(first.stdout)["run_id"],
    json.loads(second.stdout)["run_id"],
)
self.assertTrue(json.loads(first.stdout)["created"])
self.assertFalse(json.loads(second.stdout)["created"])
```

- [ ] **Step 2: Write failing skill-contract tests**

Assert the three ticket-mutating skills and the directed-target reference
contain `bind-target`, `PITCREW_RUN_ID`, and the rule that binding happens
before tracker mutation or checkout writes.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_cli.CliTest.test_scheduled_runner_queues_distinct_targets_up_to_capacity \
  tests.test_cli.CliTest.test_scheduled_runner_is_idempotent_for_same_target \
  tests.test_skill_contracts
```

Expected: FAIL because scheduled invocations still acquire `<skill>.lock`.

- [ ] **Step 4: Parse and protect the internal run ID**

Add `--coordinated-run <uuid>` to `bin/pitcrew-codex.sh`. Reject it unless
`--scheduled` is present and the store row matches project, skill, and target.
Before building Codex arguments, external scheduled calls must execute:

```bash
exec python3 "$REPO_ROOT/scripts/pitcrew_run_dispatcher.py" enqueue \
  --project "$PROJECT" \
  --skill "$SKILL" \
  ${TARGET:+--target "$TARGET"}
```

Implement this without expansion tricks in the real Bash code: build an array,
append `--target "$TARGET"` only when non-empty, then `exec` the array.

The internal path sets:

```bash
RUN_DB="$RUNTIME_ROOT/$PROJECT/runs.sqlite3"
LOCK_FILE="$LOCK_ROOT/runs/$COORDINATED_RUN.lock"
SUMMARY_FILE="$SUMMARY_DIR/runs/$COORDINATED_RUN.last.txt"
LIVE_FILE="$LIVE_DIR/runs/$COORDINATED_RUN.json"
```

and passes `--run-db` plus `--run-id` to `pitcrew_locked_exec.py`.

- [ ] **Step 5: Add the binding protocol to prompts and skills**

The coordinated prompt must expose only the run ID and exact local claim
command:

```text
This execution is coordinated as PITCREW_RUN_ID=<uuid>. Before the first
tracker mutation or checkout write, bind the selected canonical ticket with:
python3 <pitcrew-root>/scripts/pitcrew_run_dispatcher.py bind-target
--project <project> --run-id <uuid> --target <canonical-url>.
If binding reports a conflict, select another eligible ticket or return a
structured no-op without mutating the provider.
```

Add the same invariant immediately after `DIRECTED TARGET` in
`implementer-run` and `unblock`. In `stale-sweep`, select at most one
ticket-lifecycle reconciliation per coordinated run, bind it before STEP 3,
and leave deploy-change cleanup unchanged only when the run has no directed
ticket.

- [ ] **Step 6: Run CLI and contract suites**

Run:

```bash
python3 -m unittest tests.test_cli tests.test_skill_contracts
```

Expected: PASS.

- [ ] **Step 7: Commit the unified runner path**

```bash
git add \
  bin/pitcrew-codex.sh \
  references/DIRECTED-TARGET.md \
  skills/implementer-run/SKILL.md \
  skills/unblock/SKILL.md \
  skills/stale-sweep/SKILL.md \
  tests/test_cli.py \
  tests/test_skill_contracts.py
git commit -m "feat: coordinate scheduled ticket runs"
```

## Task 5: Integrate stop, resume, and crash recovery

**Files:**
- Modify: `bin/pitcrew-schedule.py`
- Modify: `scripts/pitcrew_runtime_state.py`
- Modify: `tests/test_schedule.py`
- Modify: `tests/test_run_dispatcher.py`

- [ ] **Step 1: Write failing stop and resume tests**

Add tests proving this order:

```python
self.assertEqual(
    [
        ("set_project_state", "getbill", "stopped"),
        ("write_state", "getbill", "stopped"),
        ("cancel_running", "getbill", "global_stop"),
        ("bootout_enabled_jobs", "getbill"),
    ],
    calls,
)
```

Queued rows must remain `queued`. Running rows become `cancelled` after worker
termination. `resume-all` writes `running`, reinstalls schedules, and calls
`set_project_state("running")` followed by `reconcile_and_drain` once;
cancelled rows stay terminal. Add a barrier test where enqueue races
`stop-all`: either the run is admitted before the stop transaction and then
cancelled/suspended, or admission raises `RunPaused`; it must never start after
the project control row becomes `stopped`.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_schedule tests.test_run_dispatcher
```

Expected: FAIL because scheduler control does not know coordinated runs.

- [ ] **Step 3: Wire scheduler global control to the dispatcher**

Create the store from `runtime_root() / project / "runs.sqlite3"`; schema
creation is safe and idempotent. On stop:

```python
store.set_project_state(project, "stopped")
write_state(project, "stopped")
dispatcher.cancel_running(project=project, error_code="global_stop")
```

then unload launchd jobs. On resume, write the compatibility file, install
jobs, reopen SQLite admission, then drain:

```python
write_state(project, "running")
install_enabled_jobs(project)
store.set_project_state(project, "running")
dispatcher.reconcile_and_drain(
    project=project,
    capacities=capacity_map(config),
)
```

Keep `scripts/pitcrew_runtime_state.py` responsible only for the durable
running/stopped flag. Add a directory `fsync` after replacing
`execution-state.json` so stop survives a power loss.

- [ ] **Step 4: Add startup recovery**

The dispatcher CLI commands `enqueue`, `bind-target`, `drain`, and `resume`
must call `reconcile` before claiming capacity. Dead PIDs or stale heartbeats
become `failed/interrupted`; queued rows remain eligible.

- [ ] **Step 5: Run schedule, state, and dispatcher suites**

Run:

```bash
python3 -m unittest \
  tests.test_schedule \
  tests.test_run_dispatcher \
  tests.test_cli.CliTest.test_runner_refuses_to_start_when_global_stop_is_active
```

Expected: PASS.

- [ ] **Step 6: Commit recovery controls**

```bash
git add \
  bin/pitcrew-schedule.py \
  scripts/pitcrew_runtime_state.py \
  tests/test_schedule.py \
  tests/test_run_dispatcher.py
git commit -m "feat: stop and recover coordinated runs"
```

## Task 6: Make the dashboard service authoritative per ticket

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service tests for idempotent admission**

Inject a temporary `RunStore` and fake dispatcher into `DashboardService`.
Use a thread barrier to call `launch_ticket_agent` twice for the same target
and assert one run ID and one spawn. Add two distinct targets and assert both
are active.

```python
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(
        pool.map(
            lambda _: service.launch_ticket_agent("implementer-run", target),
            range(2),
        )
    )
self.assertEqual(results[0]["run_id"], results[1]["run_id"])
self.assertEqual({result["created"] for result in results}, {True, False})
self.assertEqual(1, dispatcher.drain_calls)
```

- [ ] **Step 2: Write failing lifecycle and read-purity tests**

Cover:

- `todo` accepts only `implementer-run`;
- `blocked` accepts only `unblock`;
- `done` accepts only `stale-sweep`;
- closed or mismatched issues are rejected before enqueue;
- a queued/running ticket exposes `active_run` and a disabled CTA;
- `_gitlab_mutation` is never called by `gitlab_work`;
- `lastGitLabRefresh` advances only after a successful fetch;
- legacy `live/<skill>.json` contributes one temporary running slot.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardServiceTest.test_ticket_launch_is_idempotent_under_concurrency \
  tests.test_dashboard.DashboardServiceTest.test_ticket_launch_revalidates_lifecycle \
  tests.test_dashboard.DashboardServiceTest.test_gitlab_work_is_read_only_and_overlays_active_runs
```

Expected: FAIL because the service starts the runner directly and mutates
GitLab during reads.

- [ ] **Step 4: Inject store and dispatcher**

Extend the constructor with keyword-only defaults:

```python
self.run_store = run_store or RunStore(
    self.runtime_dir / "runs.sqlite3",
    now=self.now,
)
self.run_dispatcher = run_dispatcher or RunDispatcher(
    store=self.run_store,
    runner=RUNNER,
)
```

Add keyword-only `run_store=None` and `run_dispatcher=None` to the existing
constructor signature, then add the exact assignments above after
`self._control_lock` is initialized. Add `runs_snapshot`, `_capacity_map`, `_canonical_ticket_target`,
`_ticket_lifecycle`, and `_validate_ticket_run`. The lifecycle validation reads
`projects/<encoded>/issues/<iid>` through `_gitlab_document`, requires
`state == "opened"` for every dashboard CTA and compares the configured label
against `TICKET_AGENT_ACTIONS`.

- [ ] **Step 5: Replace direct launch with enqueue and drain**

Implement:

```python
def launch_ticket_agent(self, skill: str, target: str) -> dict:
    with self._control_lock:
        self._enabled_entry(skill)
        if self._global_state() == "stopped":
            raise DashboardError("global stop is active")
        canonical, issue = self._validate_ticket_run(skill, target)
        run = self.run_store.enqueue(
            project=self.project,
            skill=skill,
            source="dashboard",
            target=canonical,
        )
        self.run_dispatcher.reconcile_and_drain(
            project=self.project,
            capacities=self._capacity_map(),
        )
        current = self.run_store.get(run["run_id"])
        return {
            "run_id": current["run_id"],
            "state": current["state"],
            "queue_position": current["queue_position"],
            "created": run["created"],
        }
```

Remove `_trigger_target`. Retain `_trigger` for non-ticket dashboard controls
until those controls are separately migrated.

- [ ] **Step 6: Overlay runs and remove mutations from GitLab reads**

Delete calls to `_sync_merged_issue` from `gitlab_work`. Build
`active_by_target` once from the run store and include:

```python
"active_run": active_by_target.get(issue["web_url"]),
"agent_action": {
    "skill": skill,
    "label": label,
    "target": issue["web_url"],
    "available": issue["web_url"] not in active_by_target,
    "unavailable_reason": (
        "Ticket en attente ou en cours"
        if issue["web_url"] in active_by_target
        else None
    ),
},
```

Represent merged drift as a reconciliation candidate and enqueue
`stale-sweep` through a write-side reconciliation method invoked after the
GitLab payload has been assembled, never through `_gitlab_mutation` in the GET
path.

- [ ] **Step 7: Run the dashboard service suite**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest
```

Expected: PASS.

- [ ] **Step 8: Commit service integration**

```bash
git add scripts/pitcrew_dashboard.py tests/test_dashboard.py
git commit -m "feat: expose durable ticket run state"
```

## Task 7: Add authenticated run HTTP contracts

**Files:**
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing HTTP tests**

Extend `FakeDashboardService` with `runs_snapshot` and the new launch return.
Test `GET /api/runs`, exact `POST /api/ticket-runs`, repeated responses, missing
session, wrong content type, extra keys, blank values, oversized bodies, and
safe error mapping.

```python
status, payload, _ = self.request(
    "POST",
    "/api/ticket-runs",
    {"skill": "implementer-run", "target": target},
    {"X-Pitcrew-Session": self.token},
)
self.assertEqual(202, status)
self.assertEqual("queued", payload["state"])
self.assertEqual(
    [("launch_ticket_agent", "implementer-run", target)],
    self.service.calls,
)
```

- [ ] **Step 2: Run HTTP tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardHttpTest
```

Expected: FAIL because both routes return 404.

- [ ] **Step 3: Implement the GET route**

Add before asset routing:

```python
if parsed.path == "/api/runs":
    self._service_json(service.runs_snapshot)
    return
```

- [ ] **Step 4: Implement the dedicated POST route**

Authenticate `/api/ticket-runs` with the same host/origin/session/content
checks as `/api/actions`, then require the exact payload:

```python
if (
    set(request) != {"skill", "target"}
    or not isinstance(request["skill"], str)
    or not isinstance(request["target"], str)
    or not request["skill"]
    or not request["target"]
):
    self._send_json(400, {"error": "invalid request"})
    return
```

Call `service.launch_ticket_agent` and return 202. Remove the legacy
`launch-ticket-agent` branch from `/api/actions` after its replacement tests
pass. Keep 403 for a rejected action and 500 for an unavailable service.

- [ ] **Step 5: Run all dashboard HTTP tests**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardHttpTest \
  tests.test_dashboard.DashboardRealAssetsHttpTest
```

Expected: PASS.

- [ ] **Step 6: Commit HTTP contracts**

```bash
git add bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: add ticket run dashboard API"
```

## Task 8: Render persistent queue state and adaptive polling

**Files:**
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing asset-contract tests**

Replace assertions tied to `pendingTicketActions` as the durable state with
assertions for:

```python
self.assertIn('fetchJson("/api/runs")', self.javascript)
self.assertIn('fetchJson("/api/ticket-runs"', self.javascript)
self.assertIn("active_run", self.javascript)
self.assertIn("queue_position", self.javascript)
self.assertIn("running_count", self.javascript)
self.assertIn("max_concurrent", self.javascript)
self.assertIn("ACTIVE_POLL_INTERVAL_MS", self.javascript)
self.assertIn("window.setTimeout", self.javascript)
self.assertNotIn('action: "launch-ticket-agent"', self.javascript)
```

Also assert `.ticket-run-status`, `.ticket-run-queued`,
`.ticket-run-running`, `.ticket-run-failed`, and `[aria-busy="true"]` have
visible styles.

- [ ] **Step 2: Run asset tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest
```

Expected: FAIL because the browser still posts the legacy action and uses a
fixed interval.

- [ ] **Step 3: Add local run state and deterministic rendering**

Use:

```javascript
const ACTIVE_POLL_INTERVAL_MS = 2_000;
const IDLE_POLL_INTERVAL_MS = 10_000;
let latestGitLabWork = null;
let latestRuns = { runs: [], capacity: {}, has_active: false };
let refreshTimer = null;

function runsByTarget(snapshot) {
  return new Map(
    (Array.isArray(snapshot?.runs) ? snapshot.runs : [])
      .filter((run) => typeof run.target === "string" && run.target)
      .map((run) => [run.target, run]),
  );
}
```

`renderGitLab` receives the run snapshot, prefers `runsByTarget` over cached
`issue.active_run`, and renders:

```javascript
if (run?.state === "queued") {
  status.textContent = `En attente · position ${run.queue_position}`;
} else if (run?.state === "running") {
  const role = latestRuns.capacity?.[run.skill];
  status.textContent =
    `En cours · ${role?.running ?? 1}/${role?.max_concurrent ?? 3} places utilisées`;
} else if (run?.state === "failed") {
  status.textContent = "Échec · Relancer";
}
button.disabled = pendingTicketActions.has(target)
  || run?.state === "queued"
  || run?.state === "running"
  || !agentAction.available;
```

Keep `pendingTicketActions` only for the sub-HTTP-request window.

- [ ] **Step 4: Post the new contract and preserve server identity**

`launchTicketAgent` posts:

```javascript
const run = await fetchJson("/api/ticket-runs", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Pitcrew-Session": sessionToken,
  },
  body: JSON.stringify({ skill, target }),
});
upsertRun(latestRuns, { ...run, skill, target });
renderGitLab(latestGitLabWork, latestRuns);
```

Do not delete the active run after 202. Clear only the local request-pending
set in `finally`.

- [ ] **Step 5: Make refresh adaptive and GitLab failure-safe**

Fetch status, history, proposals, decisions, and `/api/runs`; render cached
GitLab work with the new runs every cycle. Use one recursive timer:

```javascript
function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  const delay = latestRuns.has_active
    ? ACTIVE_POLL_INTERVAL_MS
    : IDLE_POLL_INTERVAL_MS;
  refreshTimer = window.setTimeout(async () => {
    await refresh();
    scheduleRefresh();
  }, delay);
}
```

Remove `window.setInterval`. Update `lastGitLabRefresh` only after
`fetchJson(gitlabPath)` succeeds. Detect a transition from active to terminal,
invalidate `lastGitLabRefresh`, and issue one forced GitLab refresh.

- [ ] **Step 6: Add capacity and state styles**

Reuse existing tokens:

```css
.ticket-run-status {
  flex-basis: 100%;
  font-size: 0.78rem;
  font-weight: 700;
}

.ticket-run-queued { color: var(--warning); }
.ticket-run-running { color: var(--accent); }
.ticket-run-failed { color: var(--danger); }

.ticket-agent-actions button[aria-busy="true"] {
  cursor: wait;
  opacity: 0.72;
}
```

Keep the existing reduced-motion and mobile rules.

- [ ] **Step 7: Run dashboard tests**

Run:

```bash
python3 -m unittest tests.test_dashboard
```

Expected: PASS.

- [ ] **Step 8: Commit UI synchronization**

```bash
git add dashboard/app.js dashboard/styles.css tests/test_dashboard.py
git commit -m "feat: show durable ticket queue state"
```

## Task 9: Document, verify, review, and preserve the dirty checkout

**Files:**
- Modify: `README.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `tests/run.sh` only if new suites are not automatically discovered
- Test: all suites

- [ ] **Step 1: Update operator documentation**

Document these exact behaviors:

- default capacity three per role and optional overrides;
- fourth run queued FIFO;
- duplicate ticket admission returns the active run;
- dashboard states survive reload;
- failure requires manual retry;
- `stop-all` cancels running work, suspends queued work, and `resume-all`
  restarts only queued work;
- runtime database path and seven-day terminal retention.

- [ ] **Step 2: Run syntax and deterministic test verification**

Run:

```bash
python3 -m py_compile \
  scripts/pitcrew_config.py \
  scripts/pitcrew_run_store.py \
  scripts/pitcrew_run_dispatcher.py \
  scripts/pitcrew_locked_exec.py \
  scripts/pitcrew_dashboard.py \
  scripts/pitcrew_runtime_state.py \
  bin/pitcrew-schedule.py \
  bin/pitcrew-dashboard
bash -n bin/pitcrew-codex.sh
bash tests/run.sh
```

Expected: all commands exit 0.

- [ ] **Step 3: Exercise the real browser flow**

Start the local dashboard against a temporary configured runtime and deterministic
fake worker. In a real browser:

1. launch three distinct `todo` cards and confirm all show `En cours`;
2. launch a fourth and confirm `En attente · position 1`;
3. double-click one card and confirm no duplicate run appears;
4. reload the page and confirm all four buttons remain disabled;
5. finish one fake worker and confirm the queued card starts within two seconds;
6. fail one worker and confirm only its ticket shows `Échec · Relancer`.

Capture one desktop and one narrow-width screenshot for inspection. Do not add
temporary runtime data or screenshots to git.

- [ ] **Step 4: Inspect the complete diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Confirm the pre-existing modifications were preserved and integrated. Confirm
no runtime database, live marker, log, screenshot, or temporary file is
tracked.

- [ ] **Step 5: Run a read-only review**

Use a `lean-reviewer` on the non-trivial diff. Ask it to focus on:

- SQLite transaction correctness and partial unique-index behavior;
- process/PID/heartbeat races;
- duplicate admission across HTTP threads and processes;
- stop/resume semantics;
- target/lifecycle validation;
- browser state after refresh and failure;
- preservation of existing dirty-worktree changes.

Resolve every actionable finding in the main thread, then rerun the focused
suite for the touched area and `bash tests/run.sh`.

- [ ] **Step 6: Commit documentation and final fixes**

```bash
git add \
  README.md \
  references/SCHEDULED-TASKS.md \
  tests/run.sh
git commit -m "docs: document concurrent ticket runs"
```

If final review fixes touch implementation files, stage only those owned files
and create a separate `fix: harden ticket run coordination` commit.
