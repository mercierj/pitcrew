# Remove No-op Role Cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each scheduled Pitcrew role evaluate new work at every configured interval after a normal no-op.

**Architecture:** Preserve the project-wide provider-authentication circuit breaker. Remove per-skill cooldown persistence and checks because normal no-ops do not identify a provider failure or an unchanged queue.

**Tech Stack:** Python standard library, Bash, unittest.

---

### Task 1: Remove the per-skill cooldown

**Files:**
- Modify: `scripts/pitcrew_preflight.py:45-143`
- Modify: `bin/pitcrew-codex.sh:562-574`
- Modify: `tests/test_preflight.py:65-78`
- Modify: `tests/test_cli.py:1519-1554`

- [ ] **Step 1: Write the failing regression test**

Replace `test_noop_cooldown_is_scoped_to_one_skill` with a test that records a
normal no-op and asserts that the next `check` for the same skill returns
`decision == "run"`.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python3 -m unittest tests.test_preflight.PreflightTest.test_noop_does_not_block_the_next_scheduled_pass`

Expected: FAIL because `record-noop` currently writes `skills[skill]` and
`check` returns `decision == "noop"`.

- [ ] **Step 3: Implement the minimal change**

Remove the per-skill branch from `check` and stop invoking `record-noop` for
structured no-op or blocked summaries in the scheduled runner. Keep
`record-provider-failure` and its project-wide cooldown unchanged.

- [ ] **Step 4: Run focused verification**

Run: `python3 -m unittest tests.test_preflight tests.test_cli.CliTest.test_authentication_failure_opens_provider_cooldown tests.test_cli.CliTest.test_structured_noop_without_legacy_phrase_opens_skill_cooldown tests.test_cli.CliTest.test_structured_blocked_without_legacy_phrase_opens_skill_cooldown`

Expected: PASS after adapting the two CLI expectations to assert that no
per-skill cooldown state is created and the next pass can start.

- [ ] **Step 5: Run the full relevant suite**

Run: `python3 -m unittest tests.test_preflight tests.test_cli`

Expected: PASS.
