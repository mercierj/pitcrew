# GetBill Autonomous Pitcrew Loops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a safe, persistent set of autonomous Pitcrew loops for GetBill.

**Architecture:** Keep every Pitcrew role as a bounded Codex invocation and add a
macOS launchd adapter around the existing runner. Configure GitLab as the shared
board, protect each role with a non-blocking lock, and leave human/release gates
disabled.

**Tech Stack:** Bash, Python 3, launchd property lists, Codex CLI, GitLab CLI.

---

### Task 1: Specify the scheduler contract

**Files:**
- Modify: `tests/test_cli.py`
- Create: `tests/test_schedule.py`

- [x] Add failing tests for the GetBill schedule, disabled gated roles,
  deterministic launchd labels, and per-role overlap prevention.
- [x] Run `python3 -m unittest tests.test_cli tests.test_schedule -v` and confirm
  the new assertions fail because scheduling support does not exist.

### Task 2: Implement bounded scheduled execution

**Files:**
- Modify: `bin/pitcrew-codex.sh`
- Create: `bin/pitcrew-schedule.py`

- [x] Add a non-blocking, per-project/per-skill lock to the headless runner.
- [x] Pass explicit unattended-safe Codex options without bypassing sandboxing.
- [x] Render, install, and inspect per-role launchd jobs.
- [x] Re-run the focused tests and confirm they pass.

### Task 3: Complete the GetBill runtime contract

**Files:**
- Modify: `profiles/getbill.json`
- Modify: `references/providers/gitlab.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `README.md`

- [x] Add the explicit GitLab project, identity, lifecycle-label mapping, and
  manager source ledger paths.
- [x] Document GitLab label-backed lifecycle operations and the scheduler
  commands.
- [x] Validate the profile with `scripts/pitcrew_config.py`.

### Task 4: Install the GetBill board and jobs

**Files:**
- Modify outside repository: `~/.codex/pitcrew/getbill/config.json`
- Create outside repository: `~/Library/LaunchAgents/io.getbill.pitcrew.*.plist`

- [x] Create only the missing GitLab lifecycle and routing labels.
- [x] Refresh the installed plugin through the plugin cachebuster flow.
- [x] Update the runtime config atomically without storing credentials.
- [x] Install the safe core launchd jobs and verify them with `launchctl print`.

### Task 5: Verify end to end

**Files:**
- No additional production files.

- [x] Run the focused unit tests.
- [x] Run `bash tests/run.sh`.
- [x] Run the Codex plugin validators.
- [x] Run all role dry-runs and one read-only research pass.
- [x] Inspect the research log and registered jobs, then report exact enabled and
  gated roles.
