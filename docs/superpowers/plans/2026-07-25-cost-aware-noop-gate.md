# Cost-aware no-op gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip Codex for deterministic preflight no-ops and suppress repeated provider failures during a cooldown.

**Architecture:** Add a small Python preflight helper that reads project-scoped runtime state and returns structured decisions. The shell runner calls it before resolving the model or launching Codex; the locked history path records blocked runs without usage.

**Tech Stack:** Bash, Python 3, unittest, JSON state files.

---

### Task 1: Preflight decision helper

**Files:**
- Create: `scripts/pitcrew_preflight.py`
- Test: `tests/test_preflight.py`

- [ ] Write tests for healthy state, active cooldown, expired cooldown, and malformed state.
- [ ] Run `python3 -m unittest tests.test_preflight` and verify the new tests fail because the helper is absent.
- [ ] Implement JSON decision output and atomic project-scoped cooldown state.
- [ ] Run the focused tests and verify they pass.

### Task 2: Runner integration

**Files:**
- Modify: `bin/pitcrew-codex.sh`
- Modify: `tests/test_cli.py`

- [ ] Add a test with a fake Codex command and active preflight cooldown; assert structured no-op and no marker file.
- [ ] Run the focused test and verify it fails before integration.
- [ ] Invoke the helper before model resolution and Codex construction; return the complete no-op contract when blocked.
- [ ] Run the focused test and verify it passes.

### Task 3: Documentation and regression verification

**Files:**
- Modify: `references/CODEX-RUNTIME.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `tests/test_docs.py`

- [ ] Document the preflight/cooldown contract and its fail-closed behavior.
- [ ] Run the full repository test suite with `bash tests/run.sh`.
- [ ] Inspect the diff and verify unrelated working-tree changes remain untouched.
