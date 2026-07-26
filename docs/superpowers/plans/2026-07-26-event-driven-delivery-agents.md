# Event-Driven Delivery Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace timer-based delivery-role polling with durable Python-triggered chaining while preserving periodic discovery, observation, reconciliation, safety gates, and run history.

**Architecture:** Keep LaunchAgents only for scan/observation/reconciliation roles. Extend the durable run coordinator so a completed coordinated worker can enqueue and drain one idempotent successor based on its authoritative lifecycle result. Keep role-level eligibility and target binding as fail-closed guards; represent chain admission explicitly in run metadata.

**Tech Stack:** Python 3, SQLite-backed `RunStore`, macOS LaunchAgent generation, shell runner, unittest, dashboard JSON snapshots.

---

### Task 1: Define the event-driven role policy

**Files:**
- Modify: `scripts/pitcrew_config.py` (role schedule defaults and validation)
- Modify: `bin/pitcrew-schedule.py` (LaunchAgent listing/install/status filtering)
- Test: `tests/test_schedule.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing policy tests**

  Add assertions that `manager-run`, `implementer-run`, `reviewer-run`,
  `validator-run`, `investigate-run`, and `unblock` have no interval and are
  omitted from generated LaunchAgents, while scan roles retain their configured
  intervals. Keep `stale-sweep` periodic.

- [ ] **Step 2: Run focused tests to verify the policy tests fail**

  Run: `python3 -m unittest tests.test_schedule tests.test_config -v`

  Expected: failures showing delivery roles are still treated as scheduled.

- [ ] **Step 3: Implement the minimal schedule policy**

  Introduce one authoritative event-driven-role set used by configuration and
  scheduler code. Treat an event-driven role as enabled only through the
  coordinator and reject attempts to install a LaunchAgent for it. Preserve
  existing disabled-role reasons and manual-only release/preprod behaviour.

- [ ] **Step 4: Run focused tests to verify they pass**

  Run: `python3 -m unittest tests.test_schedule tests.test_config -v`

- [ ] **Step 5: Commit the policy change**

  Run: `git add scripts/pitcrew_config.py bin/pitcrew-schedule.py tests/test_schedule.py tests/test_config.py && git commit -m "feat: classify delivery roles as event driven"`

### Task 2: Add durable successor admission to the coordinator

**Files:**
- Modify: `scripts/pitcrew_run_store.py` (source metadata and idempotent keys)
- Modify: `scripts/pitcrew_run_dispatcher.py` (successor mapping and drain)
- Test: `tests/test_run_store.py`
- Test: `tests/test_run_dispatcher.py`

- [ ] **Step 1: Write failing coordinator tests**

  Cover one successor per terminal source run, duplicate completion returning
  the existing queued/running successor, normal no-op with no successor, and
  fail-closed outcomes that do not enqueue guessed work. Assert the new source
  value is `chain` and that existing `dashboard`, `scheduled`, and `reconcile`
  sources remain valid.

- [ ] **Step 2: Run focused tests to verify they fail**

  Run: `python3 -m unittest tests.test_run_store tests.test_run_dispatcher -v`

- [ ] **Step 3: Implement idempotent successor admission**

  Add a coordinator method that receives the completed run, its structured
  result, and the authoritative target/state evidence. Map only explicit
  transitions to the next role, derive a stable deduplication key from source
  run plus target, enqueue with `source="chain"`, and call the existing drain
  path. Do not infer transitions from free-form summaries.

- [ ] **Step 4: Run focused tests to verify they pass**

  Run: `python3 -m unittest tests.test_run_store tests.test_run_dispatcher -v`

- [ ] **Step 5: Commit the coordinator change**

  Run: `git add scripts/pitcrew_run_store.py scripts/pitcrew_run_dispatcher.py tests/test_run_store.py tests/test_run_dispatcher.py && git commit -m "feat: chain durable delivery runs"`

### Task 3: Connect worker completion to the successor chain

**Files:**
- Modify: `bin/pitcrew-codex.sh` (post-run structured-result handoff)
- Modify: `scripts/pitcrew_run_dispatcher.py` (completion command/validation)
- Test: `tests/test_cli.py`
- Test: `tests/test_run_dispatcher.py`

- [ ] **Step 1: Write failing runner tests**

  Assert a coordinated worker hands its validated structured result to the
  coordinator, drains the next role immediately, and does not chain on malformed
  output, provider failure, interruption, or a normal no-op without a successor.

- [ ] **Step 2: Run focused tests to verify they fail**

  Run: `python3 -m unittest tests.test_cli tests.test_run_dispatcher -v`

- [ ] **Step 3: Implement the completion handoff**

  Add a bounded completion call after the existing history/result write. Validate
  project, run id, target binding, and structured result schema before chaining.
  Keep the current exit code and final summary semantics even if successor
  admission is unavailable; record the admission failure in coordinator history.

- [ ] **Step 4: Run focused tests to verify they pass**

  Run: `python3 -m unittest tests.test_cli tests.test_run_dispatcher -v`

- [ ] **Step 5: Commit the worker handoff**

  Run: `git add bin/pitcrew-codex.sh scripts/pitcrew_run_dispatcher.py tests/test_cli.py tests/test_run_dispatcher.py && git commit -m "feat: trigger next role after worker completion"`

### Task 4: Update dashboard, schedule status, and documentation

**Files:**
- Modify: `scripts/pitcrew_dashboard.py` (agent status snapshot)
- Modify: `README.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing presentation tests**

  Assert event-driven roles report `trigger_mode="chain"`, no interval, and no
  restartable LaunchAgent, while periodic roles retain interval and schedule
  status. Assert queued runs expose their `source="chain"` metadata.

- [ ] **Step 2: Run focused tests to verify they fail**

  Run: `python3 -m unittest tests.test_dashboard -v`

- [ ] **Step 3: Implement the status and documentation changes**

  Update snapshots and labels to distinguish chain-triggered roles from periodic
  roles. Document that Pitcrew-created lifecycle transitions enqueue the next
  role immediately and that `stale-sweep` is the periodic recovery path.

- [ ] **Step 4: Run focused tests to verify they pass**

  Run: `python3 -m unittest tests.test_dashboard -v`

- [ ] **Step 5: Commit the operator-facing changes**

  Run: `git add README.md references/SCHEDULED-TASKS.md scripts/pitcrew_dashboard.py tests/test_dashboard.py && git commit -m "docs: expose event-driven delivery roles"`

### Task 5: Full verification and cleanup

**Files:**
- Test: `tests/test_schedule.py`
- Test: `tests/test_run_store.py`
- Test: `tests/test_run_dispatcher.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Run the focused regression suite**

  Run: `python3 -m unittest tests.test_schedule tests.test_config tests.test_run_store tests.test_run_dispatcher tests.test_cli tests.test_dashboard -v`

- [ ] **Step 2: Run the repository test entrypoint**

  Run: `bash tests/run.sh`

- [ ] **Step 3: Inspect the final diff and schedule output**

  Run: `git diff --check && python3 bin/pitcrew-schedule.py list --project getbill && python3 bin/pitcrew-schedule.py status --project getbill`

  Confirm no delivery-role LaunchAgents are listed and periodic roles retain
  their expected cadence.

- [ ] **Step 4: Commit only verified implementation changes**

  Stage only files owned by this plan and commit with a message describing the
  completed event-driven delivery chain.
