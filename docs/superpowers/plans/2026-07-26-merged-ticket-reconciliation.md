# Merged Ticket Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `stale-sweep` reconcile any open agent-managed ticket that GitLab proves was merged, without weakening the merge-evidence gate.

**Architecture:** The dispatcher remains the launch-time eligibility gate. Its `stale-sweep` rule will accept `pitcrew-agent` tickets in every configured lifecycle state, while the existing stale-sweep workflow remains the only component permitted to close the ticket after re-checking GitLab’s `merged` state and `merged_at` timestamp.

**Tech Stack:** Python 3; `unittest`; GitLab CLI workflow contracts.

---

### Task 1: Cover reconciliation eligibility for every lifecycle label

**Files:**

- Modify: `tests/test_run_dispatcher.py`
- Modify: `scripts/pitcrew_run_dispatcher.py`

- [ ] **Step 1: Write the failing regression test**

Add this method to `RunDispatcherTest`, adjacent to `test_bind_target_validates_gitlab_identity_and_lifecycle_before_mutating`:

```python
def test_stale_sweep_binds_every_open_agent_lifecycle_state(self):
    canonical = "https://gitlab.example/crew/demo/-/issues/7"
    for lifecycle in (
        "todo-label", "processing-label", "review-label", "blocked-label", "done-label"
    ):
        with self.subTest(lifecycle=lifecycle):
            run = self.store.enqueue(project="demo", skill="stale-sweep", source="reconcile")
            provider = mock.Mock(return_value=self.provider_result(labels=["agent-label", lifecycle]))
            dispatcher = RunDispatcher(self.store, "/runner", provider_runner=provider)
            with mock.patch.object(dispatcher_module, "load_runtime_config", return_value=self.gitlab_config()):
                bound = dispatcher.bind_target("demo", run["run_id"], canonical)
            self.assertEqual(canonical, bound["target"])
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```sh
python3 -m unittest tests.test_run_dispatcher.RunDispatcherTest.test_stale_sweep_binds_every_open_agent_lifecycle_state -v
```

Expected: FAIL for `todo-label`, `processing-label`, `review-label`, and `blocked-label`; the current rule only admits `done-label`.

- [ ] **Step 3: Implement the smallest eligibility change**

In `_required_labels` in `scripts/pitcrew_run_dispatcher.py`, replace the single stale-sweep requirement with one requirement per configured lifecycle:

```python
"stale-sweep": (
    frozenset((agent, todo)),
    frozenset((agent, processing)),
    frozenset((agent, review)),
    frozenset((agent, blocked)),
    frozenset((agent, done)),
),
```

Do not change `validate_queued_target`, the rules for other skills, or the merge-evidence checks in `skills/stale-sweep/SKILL.md`.

- [ ] **Step 4: Run the focused test and existing lifecycle test**

Run:

```sh
python3 -m unittest tests.test_run_dispatcher.RunDispatcherTest.test_stale_sweep_binds_every_open_agent_lifecycle_state tests.test_run_dispatcher.RunDispatcherTest.test_bind_target_validates_gitlab_identity_and_lifecycle_before_mutating -v
```

Expected: PASS. The second test confirms closed issues and incompatible labels remain rejected.

- [ ] **Step 5: Commit the code and regression test**

```sh
git add scripts/pitcrew_run_dispatcher.py tests/test_run_dispatcher.py
git commit -m "fix: reconcile merged tickets from any lifecycle state"
```

### Task 2: Verify the workflow contract and repair confirmed drift

**Files:**

- Verify: `skills/stale-sweep/SKILL.md`
- External state: GitLab issues `#11`, `#13`, and `#14`

- [ ] **Step 1: Run the relevant dispatcher and skill-contract tests**

Run:

```sh
python3 -m unittest tests.test_run_dispatcher tests.test_skill_contracts -v
```

Expected: PASS. The contract suite confirms `stale-sweep` still requires an explicit GitLab merged state and non-null `merged_at` before moving a ticket to `done`.

- [ ] **Step 2: Reconcile one verified ticket at a time**

For each ticket `#11`, `#13`, and `#14`, run the configured `stale-sweep` directed at its GitLab issue URL. Before each mutation, confirm the related MR is `state=merged` with a non-null `merged_at`; then apply `pitcrew-state::done`, post the idempotent audit marker, and close the issue.

- [ ] **Step 3: Verify GitLab’s resulting state**

Read each issue back. Expected for `#11`, `#13`, and `#14`:

```text
state=closed
labels include pitcrew-agent and pitcrew-state::done
```

Read `#12` back. Expected:

```text
state=opened
labels include pitcrew-agent and pitcrew-state::blocked
```

- [ ] **Step 4: Run the full local verification suite**

Run:

```sh
tests/run.sh
```

Expected: `Pitcrew verification passed`.

