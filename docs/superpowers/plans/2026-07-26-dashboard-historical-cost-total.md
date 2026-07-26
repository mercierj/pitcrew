# Dashboard Historical Cost Total Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an estimated cost total that survives the seven-day detail-history retention period.

**Architecture:** `HistoryStore` owns a `history.jsonl.usage-total.json` sidecar. On its first read it initializes the aggregate from retained lines; each later append atomically refreshes it from the retained lines plus the new record before the detail history can be pruned. The dashboard exposes the sidecar as `usage_total` and renders it alongside the existing seven-day metric.

**Tech Stack:** Python standard library backend, dependency-free browser JavaScript, Python `unittest`, Node test runner.

---

### Task 1: Persist a durable usage aggregate

**Files:**
- Modify: `tests/test_history.py:75-108`
- Modify: `scripts/pitcrew_history.py:148-252`

- [ ] **Step 1: Write the failing history-store test**

  Add a test that appends one measured record on July 24, initializes the
  aggregate, advances the read clock beyond retention, and still receives the
  same aggregate:

  ```python
  def test_usage_total_survives_detail_history_retention(self):
      with tempfile.TemporaryDirectory() as temp:
          store = HistoryStore(Path(temp) / "history.jsonl", retention_days=7)
          store.append(_record(
              "research-run", "2026-07-24T12:00:00Z", model="gpt-5.6-luna",
              usage={"input_tokens": 10, "cached_input_tokens": 0,
                     "cache_write_tokens": 0, "output_tokens": 0, "total_tokens": 10},
          ), now="2026-07-24T12:00:00Z")
          self.assertEqual(1, store.usage_total(now="2026-07-24T12:00:00Z")["measured_runs"])
          self.assertEqual([], store.read(now="2026-08-01T12:00:01Z"))
          total = store.usage_total(now="2026-08-01T12:00:01Z")
          self.assertEqual(1, total["measured_runs"])
          self.assertEqual("10", total["tokens"]["input_tokens"])
  ```

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `python3 -m unittest tests.test_history.HistoryStoreTest.test_usage_total_survives_detail_history_retention`

  Expected: FAIL with `AttributeError: 'HistoryStore' object has no attribute 'usage_total'`.

- [ ] **Step 3: Implement atomic sidecar initialization and refresh**

  Import `aggregate_usage` from `scripts.pitcrew_models`. Add
  `self.usage_total_path = self.path.with_name(f"{self.path.name}.usage-total.json")`
  in `HistoryStore.__init__`. Add helpers that validate a JSON object with the
  keys returned by `aggregate_usage`, write it through the existing atomic-file
  pattern with mode `0600`, and return a fresh aggregate when the sidecar is
  absent or invalid.

  Add `usage_total(self, now: str | None = None) -> dict`: lock, load retained
  records, initialize the sidecar from them only when absent/invalid, and return
  its aggregate. In `append`, under the existing lock, either load the valid
  sidecar or initialize from retained records before the append; then write the
  aggregate of all previously accounted records plus the normalized new record.
  Do not use the detail JSONL after it has been pruned to recalculate a valid
  sidecar.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `python3 -m unittest tests.test_history.HistoryStoreTest.test_usage_total_survives_detail_history_retention`

  Expected: PASS.

### Task 2: Expose and render the cumulative total

**Files:**
- Modify: `tests/test_dashboard.py:422-514,3436-3455`
- Modify: `scripts/pitcrew_dashboard.py:674-749`
- Modify: `dashboard/index.html:112-122`
- Modify: `dashboard/app.js:56-100,470-489`

- [ ] **Step 1: Write failing snapshot and UI contract assertions**

  In `DashboardServiceTest.test_snapshot_normalizes_schedule_history_health_and_next_pass`,
  assert `snapshot["usage_total"]` has the same five measured records as the
  initial retained history. In
  `DashboardAssetContractTest.test_model_controls_and_usage_metrics_are_rendered_from_safe_dom_apis`,
  assert the HTML contains `metric-cost-total` and the JavaScript contains
  `usage_total` and `passage(s) mesuré(s) depuis le début de l’historique`.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run: `python3 -m unittest tests.test_dashboard.DashboardServiceTest.test_snapshot_normalizes_schedule_history_health_and_next_pass tests.test_dashboard.DashboardAssetContractTest.test_model_controls_and_usage_metrics_are_rendered_from_safe_dom_apis`

  Expected: FAIL because the snapshot and markup are absent.

- [ ] **Step 3: Add the snapshot field and UI card**

  Add `"usage_total": _present_usage(self.history_store.usage_total(now=self._now().isoformat()))`
  beside `usage_7d`. Add the following card after the seven-day cost in the
  overview:

  ```html
  <article class="metric"><span>Coût estimé · total historique</span><strong id="metric-cost-total">—</strong></article>
  ```

  Bind the metric in `elements.metrics`. In `renderOverview`, render its cost
  when `usage_total.measured_runs` is nonzero and otherwise render `—`; extend
  the note with `${measured} passage(s) mesuré(s) depuis le début de l’historique`
  and `usageNote(totalUsage)`. Preserve the existing safe DOM APIs.

- [ ] **Step 4: Run the focused tests and verify they pass**

  Run: `python3 -m unittest tests.test_dashboard.DashboardServiceTest.test_snapshot_normalizes_schedule_history_health_and_next_pass tests.test_dashboard.DashboardAssetContractTest.test_model_controls_and_usage_metrics_are_rendered_from_safe_dom_apis`

  Expected: PASS.

### Task 3: Verify and commit

**Files:**
- Modify: `scripts/pitcrew_history.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `tests/test_history.py`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Run Python coverage for the changed modules**

  Run: `python3 -m unittest tests.test_history tests.test_dashboard`

  Expected: PASS.

- [ ] **Step 2: Run dashboard JavaScript tests**

  Run: `node --test tests/dashboard_*.test.mjs`

  Expected: PASS.

- [ ] **Step 3: Check the diff**

  Run: `git diff --check`

  Expected: no output and exit code 0.

- [ ] **Step 4: Commit the implementation**

  ```bash
  git add scripts/pitcrew_history.py scripts/pitcrew_dashboard.py dashboard/index.html dashboard/app.js tests/test_history.py tests/test_dashboard.py
  git commit -m "feat: retain dashboard historical cost total"
  ```
