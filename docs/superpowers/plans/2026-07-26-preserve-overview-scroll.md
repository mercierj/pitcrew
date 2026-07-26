# Preserve Overview Scroll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the operator's current document scroll position when dashboard polling refreshes the overview.

**Architecture:** `dashboard/app.js` will capture the viewport coordinates only for non-manual refreshes. The refresh coordinator will restore those coordinates in its `finally` block, after any render work and regardless of refresh success; rendering modules remain unchanged.

**Tech Stack:** Browser JavaScript modules, Node.js built-in test runner, Python `unittest` source-contract coverage.

---

### Task 1: Specify and implement automatic viewport preservation

**Files:**
- Modify: `tests/dashboard_pilotage.test.mjs:748-763`
- Modify: `dashboard/app.js:1602-1654`

- [ ] **Step 1: Write the failing source-contract test**

  Add this test after `refresh keeps human actions, authenticated preprod, and GitLab independent` in `tests/dashboard_pilotage.test.mjs`:

  ```js
  test("automatic refresh preserves the viewport without changing manual refresh", async () => {
    const appSource = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
    const refreshSource = appSource
      .split("async function refresh({ manual = false, skipGitLab = false } = {}) {")[1]
      .split("elements.refreshButton.addEventListener")[0];

    assert.match(
      refreshSource,
      /const scrollPosition = manual \? null : \{x: window\.scrollX, y: window\.scrollY\};/,
    );
    assert.match(
      refreshSource,
      /if \(scrollPosition\) window\.scrollTo\(scrollPosition\.x, scrollPosition\.y\);/,
    );
  });
  ```

- [ ] **Step 2: Run the test and confirm it fails for the intended reason**

  Run:

  ```bash
  node --test tests/dashboard_pilotage.test.mjs
  ```

  Expected: FAIL in `automatic refresh preserves the viewport without changing manual refresh`, because `refresh` does not yet capture or restore viewport coordinates.

- [ ] **Step 3: Add the minimal refresh-coordinator change**

  In `dashboard/app.js`, immediately after the `refreshPromise` guard in `refresh`, capture the viewport only for automatic refreshes:

  ```js
  const scrollPosition = manual ? null : {x: window.scrollX, y: window.scrollY};
  ```

  In the existing `finally` block that clears `aria-busy` and re-enables the refresh button, add restoration after the two existing statements:

  ```js
  if (scrollPosition) window.scrollTo(scrollPosition.x, scrollPosition.y);
  ```

  This lets the browser clamp coordinates if rendered content became shorter, and preserves the manual-refresh path by leaving `scrollPosition` as `null`.

- [ ] **Step 4: Run the focused test and confirm it passes**

  Run:

  ```bash
  node --test tests/dashboard_pilotage.test.mjs
  ```

  Expected: PASS with the new viewport-preservation test and all existing pilotage tests.

- [ ] **Step 5: Run the dashboard source-contract suite**

  Run:

  ```bash
  python3 -m unittest tests.test_dashboard
  ```

  Expected: PASS, confirming the served dashboard assets retain their polling and accessibility contracts.

- [ ] **Step 6: Commit the focused change**

  Run:

  ```bash
  git add dashboard/app.js tests/dashboard_pilotage.test.mjs
  git commit -m "fix: preserve overview scroll during polling"
  ```

  Expected: a commit containing only the implementation and focused test, without including existing unrelated working-tree changes.
