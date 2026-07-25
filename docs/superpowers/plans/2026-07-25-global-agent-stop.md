# Global Agent Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a confirmed dashboard control that stops all scheduled agents and blocks every future scheduled or manual run until the user explicitly resumes them.

**Architecture:** Store a per-project running/stopped runtime state, expose scheduler commands that update the state and unload or reinstall all enabled launchd jobs, and make the runner fail closed before invoking Codex. Extend the authenticated dashboard action API and render a global emergency control without changing the existing per-agent action contract.

**Tech Stack:** Python 3 standard library, Bash, launchd, authenticated localhost HTTP API, vanilla ES modules, CSS, unittest.

---

## File map

- Create `scripts/pitcrew_runtime_state.py`: read/write the per-project stop state and provide a CLI for the Bash runner.
- Modify `bin/pitcrew-schedule.py`: add global stop/resume commands, report global state, and refuse install while stopped.
- Modify `bin/pitcrew-codex.sh`: check the state before resolving or invoking Codex.
- Modify `scripts/pitcrew_dashboard.py` and `bin/pitcrew-dashboard`: expose and validate global API actions.
- Modify `dashboard/index.html`, `dashboard/app.js`, and `dashboard/styles.css`: add confirmation, blocked status, stop, and resume controls.
- Modify `README.md` and `references/SCHEDULED-TASKS.md`: document the behavior.
- Test in `tests/test_schedule.py`, `tests/test_cli.py`, and `tests/test_dashboard.py`.

### Task 1: Add persistent per-project execution state

**Files:**
- Create: `scripts/pitcrew_runtime_state.py`
- Test: `tests/test_schedule.py`

- [ ] **Step 1: Write failing tests** for CLI status/stop/resume using an isolated CODEX_HOME. Missing state must report running; stop must report stopped; resume must report running; unsafe project names must exit 2.
- [ ] **Step 2: Run the focused tests** with `python3 -m unittest tests.test_schedule.ScheduleTest.test_runtime_state_defaults_and_transitions tests.test_schedule.ScheduleTest.test_runtime_state_rejects_unsafe_projects`; expect failure because the helper does not exist.
- [ ] **Step 3: Implement the helper** with `state_path`, `read_state`, and `write_state`. Use existing `runtime_root()` and project validation, store `execution-state.json` beside config/history, write mode 0600 through a same-directory temporary file plus `os.replace`, and reject symlinked runtime/project directories. CLI: `status --project`, `stop --project`, `resume --project`.
- [ ] **Step 4: Run the focused tests again**; expect PASS.
- [ ] **Step 5: Commit** with `git add scripts/pitcrew_runtime_state.py tests/test_schedule.py && git commit -m "feat: add persistent project execution state"`.

### Task 2: Make scheduler and runner fail closed

**Files:**
- Modify: `bin/pitcrew-schedule.py`
- Modify: `bin/pitcrew-codex.sh`
- Test: `tests/test_schedule.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing scheduler tests** asserting stop-all writes stopped, attempts bootout for every enabled skill, reports global_state in status, and install exits 2 without bootstrapping while stopped. Add resume-all coverage for running state and bootstrap.
- [ ] **Step 2: Write the failing runner test** with a stopped state file and fake CODEX_BIN; assert exit 0, a JSON noop reason containing global stop, and no Codex marker.
- [ ] **Step 3: Implement scheduler commands.** Add stop-all and resume-all. stop-all writes stopped before attempting every enabled launchd label, returns the first failure after the loop, and never reopens execution. resume-all writes running then reuses installation. Add global_state to status and reject install with scheduler is globally stopped before rendering.
- [ ] **Step 4: Add the runner guard** immediately after PROJECT resolution and before repository/model lookup. Invoke the state helper; when stopped print `{"status":"noop","reason":"global stop is active"}` and exit 0. On malformed state/helper failure, print a redacted error and exit 2 without invoking Codex.
- [ ] **Step 5: Run `python3 -m unittest tests.test_schedule tests.test_cli`; expect PASS.**
- [ ] **Step 6: Commit** with `git add bin/pitcrew-schedule.py bin/pitcrew-codex.sh tests/test_schedule.py tests/test_cli.py && git commit -m "feat: block scheduled runs during global stop"`.

### Task 3: Extend the dashboard service and authenticated API

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `bin/pitcrew-dashboard`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service tests** asserting snapshot includes global_state; global_control("stop-all") invokes scheduler stop-all; global_control("resume-all") invokes resume-all; and per-agent actions still require an enabled skill.
- [ ] **Step 2: Implement service actions** with `GLOBAL_CONTROL_ACTIONS = {"stop-all", "resume-all"}`, a global scheduler-control helper, and `global_control(action)` returning accepted plus the resulting state. Preserve the existing skill-scoped control method.
- [ ] **Step 3: Implement API validation.** Accept exactly `{"action":"stop-all"}` or `{"action":"resume-all"}`; dispatch to `service.global_control`. Keep exact skill/model payload validation for existing actions and reject mixed global/skill payloads with 400.
- [ ] **Step 4: Run `python3 -m unittest tests.test_dashboard`; expect PASS.**
- [ ] **Step 5: Commit** with `git add scripts/pitcrew_dashboard.py bin/pitcrew-dashboard tests/test_dashboard.py && git commit -m "feat: expose global stop through dashboard API"`.

### Task 4: Add the confirmed dashboard control

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write asset contract tests** for global-action, global-stop-button, global-resume-button, the exact French confirmation text, both global action names, the session header, and danger/blocked CSS selectors.
- [ ] **Step 2: Add HTML controls** in the header: a global-actions wrapper, global-state text initialized to “État global : en fonctionnement”, a red “Tout arrêter” button, and a hidden “Réactiver les agents” button.
- [ ] **Step 3: Implement rendering and actions.** Render snapshot.global_state after every refresh. Stop confirmation must be exactly “Arrêter tous les agents et bloquer les futures exécutions ?” and POST exactly action stop-all. Resume must confirm “Réactiver les agents et les exécutions planifiées ?” and POST resume-all. While stopped, show “Exécutions bloquées”, disable per-agent buttons/model selects, show resume, refresh after success, and report failures through operationalStatus.
- [ ] **Step 4: Add CSS** for global-actions, button-danger, global-state, and global-state-blocked using existing variables; preserve keyboard focus and responsive header behavior.
- [ ] **Step 5: Run `python3 -m unittest tests.test_dashboard.DashboardAssetContractTest tests.test_dashboard.DashboardHTTPTest`; expect PASS.**
- [ ] **Step 6: Commit** with `git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py && git commit -m "feat: add confirmed global stop control"`.

### Task 5: Document and verify the feature

**Files:**
- Modify: `README.md`
- Modify: `references/SCHEDULED-TASKS.md`

- [ ] **Step 1: Document** stop-all as the emergency token-saving command, resume-all as explicit recovery, and the fail-closed behavior for manual and launchd-triggered runs.
- [ ] **Step 2: Run the complete suite** with `bash tests/run.sh`; expect exit code 0.
- [ ] **Step 3: Run static checks** with `git diff --check` and `python3 -m py_compile scripts/pitcrew_runtime_state.py scripts/pitcrew_dashboard.py bin/pitcrew-schedule.py`; expect no output and exit code 0.
- [ ] **Step 4: Commit documentation** with `git add README.md references/SCHEDULED-TASKS.md && git commit -m "docs: document global agent stop control"`.
- [ ] **Step 5: Manual smoke test:** open the local dashboard, click “Tout arrêter”, confirm, verify “Exécutions bloquées”, then click “Réactiver les agents”.

## Self-review

- Spec coverage: persistence and commands are Task 1; scheduler/runner fail-closed behavior is Task 2; API is Task 3; confirmation/UI is Task 4; documentation and verification are Task 5.
- Placeholder scan: no TODO, TBD, or unspecified implementation step remains.
- Type/contract consistency: the state values are running/stopped throughout; global actions are stop-all/resume-all throughout; skill actions remain trigger/stop/restart/change-model.

