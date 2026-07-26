# Remove Duplicate Forge Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep GitHub/GitLab data feeding the Workflow kanban while removing its duplicate dashboard section and merge-request cards.

**Architecture:** `refreshForgeWork()` continues to fetch `/api/forge-work` into `sources.work`; `renderPilotageView()` remains the only visual consumer of that data. Remove the separate forge DOM and dedicated renderer, while preserving the manual merge action available from a Workflow ticket's details.

**Tech Stack:** Static HTML, vanilla JavaScript, Node.js built-in test runner.

---

### Task 1: Replace the duplicate-forge asset contract

**Files:**
- Modify: `tests/dashboard_forge_ui.test.mjs:10-55`

- [ ] **Step 1: Replace the forge-region test with the desired failing assertion**

Replace the existing `dashboard exposes provider-neutral forge work regions` test with:

```js
test("dashboard keeps forge data in the Workflow kanban only", () => {
  for (const id of ["forge-work", "forge-groups", "change-list", "changes-title"]) {
    assert.doesNotMatch(html, new RegExp(`id="${id}"`));
  }
  assert.doesNotMatch(html, /Travail GitHub · GitLab/);
  assert.match(javascript, /const forgePath = "\/api\/forge-work"/);
  assert.match(javascript, /sources\.work = state\.data/);
  assert.match(javascript, /renderPilotageView\(\);/);
});
```

Replace the manual-merge test with:

```js
test("dashboard has no duplicate forge renderer or change cards", () => {
  for (const token of [
    "renderForgeWork",
    "renderChanges",
    "changes-panel",
    "change-list",
    "change-card",
  ]) {
    assert.doesNotMatch(`${javascript}\n${styles}`, new RegExp(token));
  }
});
```

- [ ] **Step 2: Run the focused asset test and verify it fails**

Run: `node --test tests/dashboard_forge_ui.test.mjs`

Expected: FAIL because the duplicate DOM and renderer still exist.

### Task 2: Remove the duplicate dashboard output

**Files:**
- Modify: `dashboard/index.html:15,154-174`
- Modify: `dashboard/app.js:73-77,881-1150,1659`
- Modify: `dashboard/styles.css` (the `.changes-*` and `.change-*` rules)

- [ ] **Step 1: Remove the second section from the static markup**

In `dashboard/index.html`, change the header copy to:

```html
<p class="header-copy">Surveillez les agents et leurs passages depuis cette machine.</p>
```

Delete the complete `<section id="forge-work" ...>…</section>` block. Do not modify `#workflow-board` or its filters.

- [ ] **Step 2: Remove only the duplicate-renderer JavaScript**

Delete these entries from `elements`:

```js
forgeWork: document.querySelector("#forge-work"),
forgeGroups: document.querySelector("#forge-groups"),
forgeState: document.querySelector("#forge-state"),
changeList: document.querySelector("#change-list"),
changesState: document.querySelector("#changes-state"),
```

Delete the complete functions `renderForgeWork` and `renderChanges`. Replace calls that refreshed the duplicate renderer after a ticket launch with `renderPilotageView()` and `syncDetailTicketAction()`. Remove the call below from `refresh()`:

```js
renderForgeWork(latestForgeWork, latestRuns);
```

Keep `renderTicketActionState`, `mergeMergeRequest`, `refreshForgeWork()`, `sources.work = state.data`, `renderPilotageView()`, and `refreshForgeWork({force: true})`: they support the Workflow kanban and its ticket details.

- [ ] **Step 3: Remove the now-unused change-card CSS**

Delete the contiguous `.changes-panel`, `.change-list`, `.change-card`, `.change-branches`, `.change-meta`, and `.change-actions` style rules. Leave `.work-grid` and `.workflow-columns` unchanged.

- [ ] **Step 4: Run the focused asset test and verify it passes**

Run: `node --test tests/dashboard_forge_ui.test.mjs`

Expected: PASS with the forge endpoint still asserted and all duplicate-view tokens absent.

### Task 3: Verify the surviving workflow

**Files:**
- Test: `tests/dashboard_pilotage.test.mjs`
- Test: `tests/dashboard_view_model.test.mjs`

- [ ] **Step 1: Run the workflow-focused tests**

Run: `node --test tests/dashboard_pilotage.test.mjs tests/dashboard_view_model.test.mjs`

Expected: PASS, confirming active GitHub/GitLab tickets still render in Workflow and its lifecycle lanes remain unchanged.

- [ ] **Step 2: Run the complete dashboard JavaScript suite**

Run: `node --test tests/dashboard_*.test.mjs`

Expected: PASS with no references to the removed duplicate renderer.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check && git diff -- dashboard/index.html dashboard/app.js dashboard/styles.css tests/dashboard_forge_ui.test.mjs`

Expected: no whitespace errors; the diff removes only the redundant display and its direct code.

- [ ] **Step 4: Commit the implementation**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/dashboard_forge_ui.test.mjs
git commit -m "fix: remove duplicate forge dashboard"
```
