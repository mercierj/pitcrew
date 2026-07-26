# Unblock UI Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh dashboard lists before closing a successfully answered unblock decision, and prevent stale detail-panel success feedback from appearing on another ticket.

**Architecture:** `dashboard/app.js` owns the decision lifecycle. It will clear the shared detail live region whenever detail content changes, await the existing manual refresh after a submitted decision, then close the detail panel only after that refresh resolves. Tests stay in the existing Node dashboard suite and assert the required source-level sequencing.

**Tech Stack:** Vanilla JavaScript modules, Node.js built-in test runner, existing dashboard test doubles.

**Execution context:** Work in the current checkout. Preserve unrelated uncommitted changes, especially in `dashboard/app.js` and `tests/dashboard_pilotage.test.mjs`.

---

## File map

- Modify `dashboard/app.js`: reset shared detail action feedback on each detail transition; close the detail after the accepted decision’s manual refresh completes.
- Modify `tests/dashboard_pilotage.test.mjs`: lock in reset and refresh-before-close sequencing using the app source contract.

### Task 1: Specify the stale-feedback and sequencing regressions

**Files:**

- Modify `tests/dashboard_pilotage.test.mjs`
- Test: `tests/dashboard_pilotage.test.mjs`

- [ ] **Step 1: Add a failing source-contract test**

Append this test:

```js
test("answered decisions refresh before close and opening another detail clears feedback", async () => {
  const appSource = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  const submitDecision = appSource
    .split("async function submitDecision(pending, answer) {")[1]
    .split("function runsByTarget(")[0];
  const openItem = appSource
    .split("function openItem(entry) {")[1]
    .split("function openAllActions(")[0];

  assert.match(openItem, /clearDetailActionStatus\(\);/);
  assert.match(
    submitDecision,
    /await refresh\(\{ manual: true \}\);\s*clearDetailActionStatus\(\);\s*detailController\.close\(\);/,
  );
});
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
node --test tests/dashboard_pilotage.test.mjs
```

Expected: FAIL because `clearDetailActionStatus` does not exist and `submitDecision` does not close the panel.

### Task 2: Make the minimal UI-state correction

**Files:**

- Modify `dashboard/app.js:160-184`
- Modify `dashboard/app.js:782-810`
- Modify `dashboard/app.js:1206-1241`

- [ ] **Step 1: Add a focused live-region reset helper**

After `setText`, add:

```js
function clearDetailActionStatus() {
  renderActionFeedback(elements.detailActionStatus, null);
}
```

- [ ] **Step 2: Clear feedback on every detail transition**

Make `openItem` start with:

```js
function openItem(entry) {
  if (!entry) return;
  clearDetailActionStatus();
```

Make `openAllActions` start with:

```js
function openAllActions(queue = currentActionQueue, returnFocusTo = null) {
  clearDetailActionStatus();
  detailTicketView = null;
```

- [ ] **Step 3: Close only after the manual refresh has completed**

In the accepted path of `submitDecision`, place these lines immediately after
the existing awaited refresh:

```js
await refresh({ manual: true });
clearDetailActionStatus();
detailController.close();
```

This preserves the success announcement while the refresh is running, rerenders
the dashboard before dismissal, and leaves no stale status for the next ticket.

- [ ] **Step 4: Run the focused test and verify green**

Run:

```bash
node --test tests/dashboard_pilotage.test.mjs
```

Expected: PASS with the new regression test included.

### Task 3: Verify dashboard behavior remains covered

**Files:**

- Verify `dashboard/app.js`
- Verify `tests/dashboard_pilotage.test.mjs`

- [ ] **Step 1: Run the dashboard JavaScript suite**

Run:

```bash
npm test -- --test-name-pattern='dashboard|pilotage'
```

Expected: PASS. If the project script does not pass Node test-name filters,
run the focused `node --test tests/dashboard_pilotage.test.mjs` command and
the repository’s documented JavaScript suite instead.

- [ ] **Step 2: Inspect the scoped diff**

Run:

```bash
git diff --check -- dashboard/app.js tests/dashboard_pilotage.test.mjs
git diff -- dashboard/app.js tests/dashboard_pilotage.test.mjs
```

Expected: no whitespace errors; only the helper, two clear calls, post-refresh
close, and the regression test.

- [ ] **Step 3: Commit the focused correction**

```bash
git add dashboard/app.js tests/dashboard_pilotage.test.mjs
git commit -m "fix: synchronize unblock dashboard feedback"
```
