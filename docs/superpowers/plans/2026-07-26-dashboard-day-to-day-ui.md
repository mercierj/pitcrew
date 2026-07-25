# Pitcrew Day-to-Day Dashboard UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the long monitoring page with a laptop-first operating dashboard that puts human interventions first, shows delivery work as a four-column flow, and moves agent and history detail into focused views.

**Architecture:** Keep the existing localhost-only Python service and dependency-free browser client. Add explicit resource identity to the existing API payloads, extract pure view-model and source-state modules, then render the app through focused navigation, pilotage, detail-panel, agent, and history modules while `app.js` remains the refresh and action orchestrator.

**Tech Stack:** Python 3.11+, `unittest`, vanilla HTML/CSS, browser ES modules, Node’s built-in `node:test`, Playwright CLI for the final browser pass.

---

## Execution constraints

- Work in the current checkout; do not create a worktree.
- Preserve the existing modifications in `dashboard/app.js`, `dashboard/styles.css`,
  `references/DIRECTED-TARGET.md`, `scripts/pitcrew_dashboard.py`,
  `tests/test_dashboard.py`, and `tests/test_skill_contracts.py`.
- Before each commit, inspect `git diff --cached --name-status` and stage only the
  files named by that task.
- Keep all DOM construction on `textContent`, `createElement`, `append`, and
  `replaceChildren`; never introduce `innerHTML`.
- Keep the fixed asset allowlist in `bin/pitcrew-dashboard`; do not add a generic
  filesystem route.

## File map

- Modify `scripts/pitcrew_dashboard.py`: expose canonical resource identity.
- Modify `bin/pitcrew-dashboard`: allowlist the new ES modules.
- Replace `dashboard/index.html`: application shell, three views, and detail dialog.
- Rewrite `dashboard/app.js`: bootstrap, refresh coordination, and action handlers.
- Create `dashboard/view-model.mjs`: pure queue and workflow normalization.
- Create `dashboard/api.mjs`: session-authenticated JSON reads and actions.
- Create `dashboard/source-store.mjs`: retain the latest successful source payload.
- Create `dashboard/format.mjs`: date, duration, token, and cost formatting.
- Create `dashboard/navigation.mjs`: view switching and navigation accessibility.
- Create `dashboard/detail-panel.mjs`: native dialog lifecycle and focus restoration.
- Create `dashboard/pilotage.mjs`: health summary, intervention queue, Kanban, and detail content.
- Create `dashboard/agents.mjs`: compact expandable agent rows and controls.
- Create `dashboard/history.mjs`: seven-day history view and filters.
- Create `tests/dashboard_view_model.test.mjs`: pure view-model tests.
- Create `tests/dashboard_source_store.test.mjs`: stale-source behavior tests.
- Modify `tests/test_dashboard.py`: API, asset, semantic HTML, and DOM contract tests.
- Modify `tests/run.sh`: run the Node tests before the Python suite.
- Modify `README.md`: document the new daily navigation.

### Task 1: Add canonical resource identity to dashboard payloads

**Files:**
- Modify: `tests/test_dashboard.py:466-849`
- Modify: `scripts/pitcrew_dashboard.py:305-875`

- [ ] **Step 1: Write failing service assertions**

Add these assertions to the existing decision, GitLab grouping, and merge-request
tests:

```python
# test_decisions_returns_pending_question_with_context
self.assertEqual("issue", pending["ticket"]["resource_type"])
self.assertEqual(
    "https://gitlab.com/getbill1/getbill/-/issues/1",
    pending["ticket"]["canonical_url"],
)

# test_gitlab_groups_work_and_extracts_related_merge_requests
todo = work["groups"]["todo"][0]
self.assertEqual("issue", todo["resource_type"])
self.assertEqual(todo["web_url"], todo["canonical_url"])

# test_gitlab_work_lists_only_open_merge_requests_with_display_fields
merge_request = work["merge_requests"][0]
self.assertEqual("merge_request", merge_request["resource_type"])
self.assertEqual(merge_request["web_url"], merge_request["canonical_url"])
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardServiceTest.test_decisions_returns_pending_question_with_context \
  tests.test_dashboard.DashboardServiceTest.test_gitlab_groups_work_and_extracts_related_merge_requests \
  tests.test_dashboard.DashboardServiceTest.test_gitlab_work_lists_only_open_merge_requests_with_display_fields -v
```

Expected: three failures reporting missing `resource_type` or `canonical_url`.

- [ ] **Step 3: Expose the identity fields**

In `decisions()`, make the returned ticket block:

```python
"ticket": {
    "resource_type": "issue",
    "canonical_url": ticket.get("web_url"),
    "title": ticket.get("title", "Ticket sans titre"),
    "description": str(ticket.get("description", ""))[:12000],
    "web_url": ticket.get("web_url"),
},
```

In the issue object appended to `groups[lifecycle]`, add:

```python
"resource_type": "issue",
"canonical_url": issue.get("web_url"),
```

In `_normalize_merge_request()`, add:

```python
normalized["resource_type"] = "merge_request"
normalized["canonical_url"] = (
    merge_request.get("web_url")
    if isinstance(merge_request.get("web_url"), str)
    else None
)
```

- [ ] **Step 4: Run the service tests**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest -v
```

Expected: all `DashboardServiceTest` tests pass.

- [ ] **Step 5: Commit the payload contract**

```bash
git add scripts/pitcrew_dashboard.py tests/test_dashboard.py
git commit -m "feat: identify dashboard work resources"
```

### Task 2: Build and test the pure daily view model

**Files:**
- Create: `dashboard/view-model.mjs`
- Create: `tests/dashboard_view_model.test.mjs`
- Modify: `tests/run.sh`

- [ ] **Step 1: Create failing view-model tests**

Create `tests/dashboard_view_model.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildActionQueue,
  buildWorkflow,
  resourceKey,
} from "../dashboard/view-model.mjs";

const issue = (lifecycle, iid, extra = {}) => ({
  resource_type: "issue",
  canonical_url: `https://gitlab.example/group/app/-/issues/${iid}`,
  web_url: `https://gitlab.example/group/app/-/issues/${iid}`,
  iid,
  lifecycle,
  title: `${lifecycle} ${iid}`,
  updated_at: `2026-07-2${iid}T10:00:00Z`,
  ...extra,
});

test("resourceKey combines the type and canonical URL", () => {
  assert.equal(
    resourceKey(issue("todo", 1)),
    "issue:https://gitlab.example/group/app/-/issues/1",
  );
});

test("buildWorkflow keeps active lanes and folds done", () => {
  const workflow = buildWorkflow({
    groups: {
      todo: [issue("todo", 1)],
      processing: [issue("processing", 2)],
      review: [issue("review", 3)],
      blocked: [issue("blocked", 4)],
      done: [
        issue("done", 5, {closed_at: "2026-07-26T08:00:00Z"}),
        issue("done", 6, {closed_at: "2026-07-25T08:00:00Z"}),
      ],
    },
  }, {doneDay: "2026-07-26"});

  assert.deepEqual(Object.keys(workflow.active), [
    "todo",
    "processing",
    "review",
    "blocked",
  ]);
  assert.equal(workflow.active.processing[0].key.endsWith("/issues/2"), true);
  assert.equal(workflow.done.length, 1);
});

test("buildWorkflow filters active cards by text and role", () => {
  const work = {
    groups: {
      todo: [
        issue("todo", 1, {
          title: "Export payments",
          route: "implementer",
          agent_action: {skill: "implementer-run"},
        }),
        issue("todo", 2, {
          title: "Audit imports",
          route: "research",
          agent_action: {skill: "research-run"},
        }),
      ],
    },
  };
  const workflow = buildWorkflow(work, {
    query: "payments",
    role: "implementer-run",
    doneDay: "2026-07-26",
  });
  assert.deepEqual(workflow.active.todo.map((item) => item.iid), [1]);
});

test("buildActionQueue orders blockers, failures, merge requests, proposals", () => {
  const queue = buildActionQueue({
    decisions: {
      pending: {
        ticket_id: "group/app#4",
        asked_at: "2026-07-20T10:00:00Z",
        question: "Choose a migration",
        ticket: issue("blocked", 4),
      },
    },
    proposals: {
      proposals: [{
        id: "proposal-1",
        title: "Harden OAuth",
        created_at: "2026-07-18T10:00:00Z",
      }],
    },
    snapshot: {
      agents: [{
        skill: "qa-run",
        health: "failed",
        latest_history: {finished_at: "2026-07-19T10:00:00Z"},
      }],
    },
    work: {
      merge_requests: [{
        resource_type: "merge_request",
        canonical_url: "https://gitlab.example/group/app/-/merge_requests/8",
        web_url: "https://gitlab.example/group/app/-/merge_requests/8",
        iid: 8,
        title: "Ready MR",
        target_branch: "develop",
        updated_at: "2026-07-21T10:00:00Z",
      }],
    },
  });

  assert.deepEqual(queue.map((entry) => entry.kind), [
    "decision",
    "agent-failure",
    "merge-request",
    "proposal",
  ]);
});

test("protected deployment merge requests never enter the action queue", () => {
  const queue = buildActionQueue({
    decisions: {},
    proposals: {},
    snapshot: {},
    work: {
      merge_requests: [{
        resource_type: "merge_request",
        canonical_url: "https://gitlab.example/group/app/-/merge_requests/9",
        iid: 9,
        title: "Prod MR",
        target_branch: "prod",
      }],
    },
  });
  assert.deepEqual(queue, []);
});
```

- [ ] **Step 2: Run the Node test and verify failure**

Run:

```bash
node --test tests/dashboard_view_model.test.mjs
```

Expected: failure with `ERR_MODULE_NOT_FOUND` for `dashboard/view-model.mjs`.

- [ ] **Step 3: Implement the pure model**

Create `dashboard/view-model.mjs`:

```javascript
export const ACTIVE_LIFECYCLES = ["todo", "processing", "review", "blocked"];

const ACTION_PRIORITY = {
  decision: 0,
  "agent-failure": 1,
  "merge-request": 2,
  proposal: 3,
};

function timestamp(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER;
}

export function resourceKey(resource, fallbackType = "resource") {
  const type = typeof resource?.resource_type === "string"
    ? resource.resource_type
    : fallbackType;
  const canonical = typeof resource?.canonical_url === "string"
    ? resource.canonical_url
    : typeof resource?.web_url === "string"
      ? resource.web_url
      : "";
  return canonical ? `${type}:${canonical}` : "";
}

function normalizeIssue(issue, lifecycle) {
  return {
    ...issue,
    key: resourceKey(issue, "issue"),
    kind: "issue",
    lifecycle,
  };
}

function dayKey(value) {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("fr-CA", {
    timeZone: "Europe/Paris",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function matchesFilters(item, query, role) {
  const normalizedQuery = query.trim().toLocaleLowerCase("fr");
  const searchable = `${item.iid ?? ""} ${item.title ?? ""}`.toLocaleLowerCase("fr");
  const itemRole = item.agent_action?.skill || item.route || "";
  return (!normalizedQuery || searchable.includes(normalizedQuery))
    && (!role || itemRole === role);
}

export function buildWorkflow(work = {}, {
  query = "",
  role = "",
  doneDay = dayKey(new Date().toISOString()),
} = {}) {
  const groups = work?.groups && typeof work.groups === "object"
    ? work.groups
    : {};
  const active = Object.fromEntries(
    ACTIVE_LIFECYCLES.map((lifecycle) => [
      lifecycle,
      (Array.isArray(groups[lifecycle]) ? groups[lifecycle] : [])
        .map((issue) => normalizeIssue(issue, lifecycle))
        .filter((issue) => matchesFilters(issue, query, role)),
    ]),
  );
  const done = (Array.isArray(groups.done) ? groups.done : [])
    .map((issue) => normalizeIssue(issue, "done"))
    .filter((issue) => dayKey(issue.closed_at || issue.updated_at) === doneDay)
    .filter((issue) => matchesFilters(issue, query, role));
  return {active, done};
}

function queueEntry(kind, key, title, updatedAt, resource, actionLabel) {
  return {
    kind,
    key,
    title,
    updatedAt,
    resource,
    actionLabel,
    priority: ACTION_PRIORITY[kind],
  };
}

export function buildActionQueue({
  decisions = {},
  proposals = {},
  snapshot = {},
  work = {},
} = {}) {
  const entries = [];
  const pending = decisions?.pending;
  if (pending && typeof pending === "object") {
    entries.push(queueEntry(
      "decision",
      resourceKey(pending.ticket, "issue") || `decision:${pending.ticket_id}`,
      pending.question || pending.ticket?.title || "Décision à prendre",
      pending.asked_at,
      pending,
      "Répondre",
    ));
  }

  (Array.isArray(snapshot?.agents) ? snapshot.agents : [])
    .filter((agent) => agent.health === "failed" || agent.health === "warning")
    .forEach((agent) => entries.push(queueEntry(
      "agent-failure",
      `agent:${agent.skill}`,
      `${agent.skill} nécessite une vérification`,
      agent.latest_history?.finished_at,
      agent,
      "Diagnostiquer",
    )));

  (Array.isArray(work?.merge_requests) ? work.merge_requests : [])
    .filter((mergeRequest) => !["preprod", "prod"].includes(mergeRequest.target_branch))
    .forEach((mergeRequest) => entries.push(queueEntry(
      "merge-request",
      resourceKey(mergeRequest, "merge_request"),
      mergeRequest.title || `MR !${mergeRequest.iid}`,
      mergeRequest.updated_at,
      mergeRequest,
      "Fusionner",
    )));

  (Array.isArray(proposals?.proposals) ? proposals.proposals : [])
    .forEach((proposal) => entries.push(queueEntry(
      "proposal",
      `proposal:${proposal.id}`,
      proposal.title || "Proposition sans titre",
      proposal.created_at || proposal.updated_at,
      proposal,
      "Examiner",
    )));

  return entries.sort((left, right) => (
    left.priority - right.priority
    || timestamp(left.updatedAt) - timestamp(right.updatedAt)
    || left.key.localeCompare(right.key)
  ));
}
```

- [ ] **Step 4: Add Node tests to the deterministic suite**

Insert before the Python command in `tests/run.sh`:

```bash
node --test tests/dashboard_view_model.test.mjs
```

```bash
node --test tests/dashboard_view_model.test.mjs
```

Expected: five passing tests.

- [ ] **Step 5: Commit the view model**

```bash
git add dashboard/view-model.mjs tests/dashboard_view_model.test.mjs tests/run.sh
git commit -m "feat: model daily dashboard work"
```

### Task 3: Introduce the application shell and safe module routes

**Files:**
- Modify: `tests/test_dashboard.py:1441-1708,2059-2334`
- Modify: `bin/pitcrew-dashboard:29-36`
- Modify: `dashboard/index.html`
- Create: `dashboard/navigation.mjs`
- Modify: `dashboard/app.js`

- [ ] **Step 1: Write failing shell and asset tests**

Extend `DashboardAssetContractTest.setUp()`:

```python
self.modules = {
    path.name: path.read_text(encoding="utf-8")
    for path in self.dashboard.glob("*.mjs")
}
```

Replace the old region assertion with:

```python
for identifier in (
    "app-header",
    "app-navigation",
    "view-pilotage",
    "view-agents",
    "view-history",
    "action-queue",
    "workflow-board",
    "done-work",
    "crew-health",
    "detail-panel",
):
    self.assertIn(f'id="{identifier}"', self.html)
self.assertLess(
    self.html.index('id="action-queue"'),
    self.html.index('id="workflow-board"'),
)
self.assertIn("<dialog", self.html)
```

Add this HTTP assertion to `test_real_asset_root_serves_interface_and_replaces_only_index_token`:

```python
for path in (
    "/assets/navigation.mjs",
    "/assets/view-model.mjs",
):
    with self.subTest(path=path):
        status, payload = self.request(path)
        self.assertEqual(200, status)
        self.assertTrue(payload)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest \
  tests.test_dashboard.DashboardRealAssetsHttpTest -v
```

Expected: failures for missing shell IDs and module routes.

- [ ] **Step 3: Allowlist browser modules**

Replace `ASSET_ROUTES` with:

```python
ASSET_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    **{
        f"/assets/{name}": (name, "text/javascript; charset=utf-8")
        for name in (
            "agents.mjs",
            "api.mjs",
            "detail-panel.mjs",
            "format.mjs",
            "history.mjs",
            "navigation.mjs",
            "pilotage.mjs",
            "source-store.mjs",
            "view-model.mjs",
        )
    },
}
```

The allowlist may name modules created in later tasks; requests return `404` until
their task creates the file.

- [ ] **Step 4: Replace the page body with the semantic shell**

Keep the existing `<head>` and session marker. Replace `<body>` with this exact
structure:

```html
<body>
  <header id="app-header" class="app-header">
    <a class="brand" href="#pilotage" aria-label="Pitcrew · Pilotage">
      <span class="brand-mark" aria-hidden="true">P</span>
      <span>Pitcrew</span>
    </a>
    <div class="header-status">
      <span id="refresh-state">Connexion au service local…</span>
      <button id="refresh-button" class="button button-quiet" type="button">Actualiser</button>
      <button id="global-stop-button" class="button button-danger" type="button">Tout arrêter</button>
      <button id="global-resume-button" class="button button-primary" type="button" hidden>Réactiver</button>
    </div>
  </header>

  <p id="operational-status" class="sr-only" aria-live="polite">
    Initialisation du tableau de bord.
  </p>

  <div class="app-shell">
    <aside class="app-sidebar">
      <p class="project-name">GetBill</p>
      <nav id="app-navigation" aria-label="Navigation principale">
        <button class="nav-item" type="button" data-view-target="pilotage" aria-current="page">
          Pilotage <span id="action-count" class="nav-count">0</span>
        </button>
        <button class="nav-item" type="button" data-view-target="agents">Agents</button>
        <button class="nav-item" type="button" data-view-target="history">Historique</button>
      </nav>
      <section id="crew-health" aria-labelledby="crew-health-title">
        <h2 id="crew-health-title">Santé du crew</h2>
      </section>
    </aside>

    <main class="app-main">
      <section id="view-pilotage" data-view="pilotage" aria-labelledby="pilotage-title">
        <h1 id="pilotage-title">Voici ce qui bouge</h1>
        <section id="action-queue" aria-labelledby="action-queue-title" aria-live="polite">
          <h2 id="action-queue-title">Ton intervention est requise</h2>
        </section>
        <section aria-labelledby="workflow-title">
          <div class="workflow-heading">
            <h2 id="workflow-title">Flux actif</h2>
            <label>Rechercher <input id="workflow-search" type="search"></label>
            <label>Rôle <select id="workflow-role"><option value="">Tous</option></select></label>
          </div>
          <div id="workflow-board" aria-live="polite"></div>
          <details id="done-work">
            <summary>Terminés aujourd’hui <span id="done-count">0</span></summary>
            <div id="done-list"></div>
          </details>
        </section>
      </section>

      <section id="view-agents" data-view="agents" aria-labelledby="agents-title" hidden>
        <h1 id="agents-title">Agents</h1>
        <div id="agent-list" aria-busy="true"></div>
        <details id="disabled-roles">
          <summary>Rôles désactivés <span id="disabled-count">0</span></summary>
          <div id="disabled-list"></div>
        </details>
      </section>

      <section id="view-history" data-view="history" aria-labelledby="history-title" hidden>
        <h1 id="history-title">Historique · 7 jours</h1>
        <form id="history-filters" class="filters">
          <label>Agent <select id="history-skill"><option value="">Tous</option></select></label>
          <label>Résultat
            <select id="history-outcome">
              <option value="">Tous</option><option value="success">Succès</option>
              <option value="noop">Sans action</option><option value="failed">Échec</option>
              <option value="interrupted">Interrompu</option>
            </select>
          </label>
          <button class="button" type="submit">Appliquer</button>
        </form>
        <ol id="activity-list"></ol>
      </section>
    </main>
  </div>

  <dialog id="detail-panel" aria-labelledby="detail-title">
    <div class="detail-header">
      <h2 id="detail-title">Détail</h2>
      <button id="detail-close" class="button button-quiet" type="button" aria-label="Fermer">Fermer</button>
    </div>
    <div id="detail-content"></div>
    <p id="detail-action-status" aria-live="polite"></p>
  </dialog>

  <script type="module" src="/assets/app.js"></script>
</body>
```

- [ ] **Step 5: Implement view navigation**

Create `dashboard/navigation.mjs`:

```javascript
export function createNavigation(root, views) {
  const buttons = [...root.querySelectorAll("[data-view-target]")];

  function show(name, {focus = true} = {}) {
    if (!Object.hasOwn(views, name)) return;
    Object.entries(views).forEach(([viewName, element]) => {
      element.hidden = viewName !== name;
    });
    buttons.forEach((button) => {
      if (button.dataset.viewTarget === name) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    window.history.replaceState(null, "", `#${name}`);
    if (focus) views[name].querySelector("h1")?.focus({preventScroll: true});
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => show(button.dataset.viewTarget));
  });
  const initial = window.location.hash.slice(1);
  show(Object.hasOwn(views, initial) ? initial : "pilotage", {focus: false});
  return {show};
}
```

Give the three page headings `tabindex="-1"`. Import and initialize the module at
the top of `dashboard/app.js`:

```javascript
import {createNavigation} from "./navigation.mjs";

const navigation = createNavigation(
  document.querySelector("#app-navigation"),
  {
    pilotage: document.querySelector("#view-pilotage"),
    agents: document.querySelector("#view-agents"),
    history: document.querySelector("#view-history"),
  },
);
```

Keep `navigation` for agent/history links used in later tasks.

- [ ] **Step 6: Run shell tests and commit**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest \
  tests.test_dashboard.DashboardRealAssetsHttpTest -v
```

Expected: all selected tests pass.

```bash
git add bin/pitcrew-dashboard dashboard/index.html dashboard/navigation.mjs dashboard/app.js tests/test_dashboard.py
git commit -m "feat: add dashboard application shell"
```

### Task 4: Render the action queue, Kanban, and detail panel

**Files:**
- Create: `dashboard/detail-panel.mjs`
- Create: `dashboard/pilotage.mjs`
- Modify: `dashboard/app.js`
- Modify: `tests/test_dashboard.py:2059-2226`

- [ ] **Step 1: Add failing module contract tests**

Load the new module text in `DashboardAssetContractTest.setUp()` and add:

```python
def test_pilotage_uses_safe_dom_and_a_single_detail_dialog(self):
    pilotage = self.modules["pilotage.mjs"]
    detail = self.modules["detail-panel.mjs"]
    self.assertIn("buildActionQueue", pilotage)
    self.assertIn("buildWorkflow", pilotage)
    self.assertIn("queue.slice(0, 3)", pilotage)
    self.assertIn("Voir toutes les actions", pilotage)
    self.assertIn("ACTIVE_LIFECYCLES", pilotage)
    self.assertIn("showModal()", detail)
    self.assertIn("returnFocus?.focus()", detail)
    self.assertIn('addEventListener("close"', detail)
    self.assertNotIn("innerHTML", pilotage + detail)
```

- [ ] **Step 2: Run the contract test and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest.test_pilotage_uses_safe_dom_and_a_single_detail_dialog -v
```

Expected: error because `pilotage.mjs` and `detail-panel.mjs` do not exist.

- [ ] **Step 3: Implement the detail-panel controller**

Create `dashboard/detail-panel.mjs`:

```javascript
export function createDetailPanel(dialog, title, content, closeButton) {
  let returnFocus = null;

  function close() {
    if (dialog.open) dialog.close();
  }

  function open({heading, body}) {
    returnFocus = document.activeElement;
    title.textContent = heading;
    content.replaceChildren(body);
    dialog.showModal();
    closeButton.focus();
  }

  closeButton.addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => {
    returnFocus?.focus();
    returnFocus = null;
  });

  return {open, close};
}
```

- [ ] **Step 4: Implement pilotage rendering**

Create `dashboard/pilotage.mjs` with these exported boundaries:

```javascript
import {
  ACTIVE_LIFECYCLES,
  buildActionQueue,
  buildWorkflow,
} from "./view-model.mjs";

const LABELS = {
  todo: "À faire",
  processing: "En cours",
  review: "En revue",
  blocked: "Bloqué",
};

function text(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function button(label, onClick, className = "button button-primary") {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = label;
  element.addEventListener("click", onClick);
  return element;
}

function queueCard(entry, onOpen) {
  const card = document.createElement("article");
  card.className = `action-card action-card-${entry.kind}`;
  card.append(
    text("p", "action-kind", entry.kind),
    text("h3", "", entry.title),
    button(entry.actionLabel, () => onOpen(entry)),
  );
  return card;
}

export function renderActionList(queue, onOpen) {
  const list = document.createElement("div");
  list.className = "action-list-all";
  queue.forEach((entry) => list.append(queueCard(entry, onOpen)));
  return list;
}

function workCard(item, onOpen) {
  const card = document.createElement("article");
  card.className = "work-card";
  card.dataset.resourceKey = item.key;
  card.append(
    text("p", "work-reference", `#${item.iid ?? "?"}`),
    text("h3", "", item.title || "Ticket sans titre"),
    text("p", "work-route", item.route || item.agent_action?.skill || "Non assigné"),
    button(
      item.agent_action?.label || "Ouvrir",
      () => onOpen({...item, kind: "issue"}),
      "button button-quiet",
    ),
  );
  return card;
}

export function renderCrewHealth(root, snapshot = {}) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  const values = [
    ["Sains", agents.filter((agent) => agent.health === "healthy").length],
    ["Alertes", agents.filter((agent) => agent.health === "warning").length],
    ["Échecs", agents.filter((agent) => agent.health === "failed").length],
    ["En cours", agents.filter((agent) => agent.running).length],
  ];
  const list = document.createElement("dl");
  values.forEach(([label, value]) => {
    list.append(text("dt", "", label), text("dd", "", value));
  });
  root.replaceChildren(root.querySelector("h2"), list);
}

export function renderPilotage(roots, sources, handlers) {
  const queue = buildActionQueue(sources);
  const workflow = buildWorkflow(sources.work, handlers.filters());
  roots.actionCount.textContent = String(queue.length);

  const queueList = document.createElement("div");
  queueList.className = "action-queue-list";
  queue.slice(0, 3).forEach((entry) => {
    queueList.append(queueCard(entry, handlers.openItem));
  });
  if (queue.length > 3) {
    queueList.append(button(
      `Voir toutes les actions (${queue.length})`,
      () => handlers.openAllActions(queue),
      "button button-quiet",
    ));
  }
  roots.actionQueue.replaceChildren(roots.actionQueue.querySelector("h2"), queueList);

  const board = document.createElement("div");
  board.className = "workflow-columns";
  ACTIVE_LIFECYCLES.forEach((lifecycle) => {
    const lane = document.createElement("section");
    lane.className = `workflow-lane workflow-lane-${lifecycle}`;
    lane.append(text("h3", "", `${LABELS[lifecycle]} · ${workflow.active[lifecycle].length}`));
    workflow.active[lifecycle].forEach((item) => lane.append(workCard(item, handlers.openItem)));
    board.append(lane);
  });
  roots.workflowBoard.replaceChildren(board);

  roots.doneCount.textContent = String(workflow.done.length);
  roots.doneList.replaceChildren(
    ...workflow.done.map((item) => workCard(item, handlers.openItem)),
  );
  renderCrewHealth(roots.crewHealth, sources.snapshot);
}

export function renderItemDetail(entry, handlers) {
  const body = document.createElement("div");
  body.className = "detail-body";
  const resource = entry.resource || entry;
  body.append(text("p", "detail-kind", entry.kind || resource.lifecycle || "Détail"));
  if (resource.title || resource.ticket?.title) {
    body.append(text("h3", "", resource.title || resource.ticket.title));
  }
  if (resource.description || resource.ticket?.description) {
    body.append(text("p", "", resource.description || resource.ticket.description));
  }
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const action = handlers.forEntry(entry);
  if (action) actions.append(button(action.label, action.run));
  body.append(actions);
  return body;
}
```

- [ ] **Step 5: Wire pilotage and the shared detail panel**

In `dashboard/app.js`, import the modules and create a single `sources` object:

```javascript
import {createDetailPanel} from "./detail-panel.mjs";
import {renderActionList, renderItemDetail, renderPilotage} from "./pilotage.mjs";

const sources = {
  snapshot: {},
  history: [],
  decisions: {},
  proposals: {},
  work: {},
};

const detailPanel = createDetailPanel(
  document.querySelector("#detail-panel"),
  document.querySelector("#detail-title"),
  document.querySelector("#detail-content"),
  document.querySelector("#detail-close"),
);

function openItem(entry) {
  detailPanel.open({
    heading: entry.title || entry.resource?.title || "Détail",
    body: renderItemDetail(entry, {forEntry: actionForEntry}),
  });
}

function openAllActions(queue) {
  detailPanel.open({
    heading: `Toutes les actions · ${queue.length}`,
    body: renderActionList(queue, (entry) => {
      detailPanel.close();
      openItem(entry);
    }),
  });
}
```

Define `actionForEntry(entry)` by adapting the existing handlers:

```javascript
function actionForEntry(entry) {
  if (entry.kind === "decision") {
    return {
      label: "Répondre",
      run: () => showDecisionChoices(entry.resource),
    };
  }
  if (entry.kind === "proposal") {
    return {label: "Examiner", run: () => showProposalActions(entry.resource)};
  }
  if (entry.kind === "merge-request") {
    return {label: "Fusionner", run: () => mergeMergeRequest(entry.resource)};
  }
  if (entry.kind === "issue" && entry.agent_action?.available) {
    return {label: entry.agent_action.label, run: () => launchTicketAgent(entry)};
  }
  if (entry.kind === "agent-failure") {
    return {label: "Voir l’agent", run: () => navigation.show("agents")};
  }
  return null;
}
```

Add these exact adapters around the existing authenticated action calls:

```javascript
function showDecisionChoices(pending) {
  const root = document.querySelector("#detail-content");
  const question = document.createElement("p");
  question.className = "decision-question";
  question.textContent = pending.question || "Quelle action faut-il prendre ?";
  const choices = document.createElement("div");
  choices.className = "decision-choices";
  (Array.isArray(pending.choices) ? pending.choices : []).forEach((answer) => {
    const choice = document.createElement("button");
    choice.type = "button";
    choice.className = "button button-primary";
    choice.textContent = answer;
    choice.addEventListener("click", () => submitDecision(pending, answer));
    choices.append(choice);
  });
  root.replaceChildren(question, choices);
}

function showProposalActions(proposal) {
  const root = document.querySelector("#detail-content");
  const summary = document.createElement("p");
  summary.textContent = proposal.summary || "Aucun résumé.";
  const evidence = document.createElement("p");
  evidence.textContent = `Preuves : ${(proposal.evidence || []).join(" · ") || "Indisponibles"}`;
  const recommendation = document.createElement("p");
  recommendation.textContent = `Recommandation : ${proposal.recommendation || "Indisponible"}`;
  const actions = document.createElement("div");
  actions.className = "proposal-actions";
  [
    ["approve", "Approuver", "button-primary"],
    ["investigate", "Investiguer", "button-quiet"],
    ["reject", "Rejeter", "button-danger"],
  ].forEach(([decision, label, className]) => {
    const control = document.createElement("button");
    control.type = "button";
    control.className = `button ${className}`;
    control.textContent = label;
    control.addEventListener("click", () => decideProposal(proposal, decision));
    actions.append(control);
  });
  root.replaceChildren(summary, evidence, recommendation, actions);
}

async function submitDecision(pending, answer) {
  await fetchJson("/api/actions", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Pitcrew-Session": sessionToken},
    body: JSON.stringify({
      action: "answer-decision",
      ticket_id: pending.ticket_id,
      answer,
      notes: "",
    }),
  });
  await refresh({manual: true});
}

async function decideProposal(proposal, decision) {
  const reason = decision === "reject"
    ? window.prompt("Pourquoi rejeter cette proposition ?", "")
    : "";
  if (decision === "reject" && !reason?.trim()) return;
  await fetchJson("/api/actions", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Pitcrew-Session": sessionToken},
    body: JSON.stringify({
      action: "decide-proposal",
      proposal_id: proposal.id,
      decision,
      reason: reason || "",
    }),
  });
  await refresh({manual: true});
}

async function mergeMergeRequest(mergeRequest) {
  if (!window.confirm(
    `Fusionner !${mergeRequest.iid} dans ${mergeRequest.target_branch} et supprimer ${mergeRequest.source_branch} ?`,
  )) return;
  await fetchJson("/api/actions", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Pitcrew-Session": sessionToken},
    body: JSON.stringify({action: "merge-merge-request", iid: mergeRequest.iid}),
  });
  await refresh({manual: true});
}

async function launchTicketAgent(issue) {
  if (!issue.agent_action?.available) return;
  await fetchJson("/api/actions", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Pitcrew-Session": sessionToken},
    body: JSON.stringify({
      action: "launch-ticket-agent",
      skill: issue.agent_action.skill,
      target: issue.agent_action.target,
    }),
  });
  await refresh({manual: true});
}
```

Call `renderPilotage()` after source updates with roots resolved once at startup.
Wire both board filters to the same pure rendering path:

```javascript
const workflowSearch = document.querySelector("#workflow-search");
const workflowRole = document.querySelector("#workflow-role");

function workflowFilters() {
  return {query: workflowSearch.value, role: workflowRole.value};
}

function syncWorkflowRoles() {
  const selected = workflowRole.value;
  const skills = new Set(
    Object.values(sources.work?.groups || {})
      .flat()
      .map((issue) => issue.agent_action?.skill || issue.route)
      .filter((value) => typeof value === "string" && value),
  );
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Tous";
  const options = [...skills].sort().map((skill) => {
    const option = document.createElement("option");
    option.value = skill;
    option.textContent = skill;
    return option;
  });
  workflowRole.replaceChildren(all, ...options);
  workflowRole.value = skills.has(selected) ? selected : "";
}

workflowSearch.addEventListener("input", renderPilotageView);
workflowRole.addEventListener("change", renderPilotageView);
```

`renderPilotageView()` calls `syncWorkflowRoles()` only after GitLab data changes,
then passes `filters: workflowFilters` to `renderPilotage()`.

- [ ] **Step 6: Run view-model and asset tests**

Run:

```bash
node --test tests/dashboard_view_model.test.mjs
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest -v
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the daily pilotage view**

```bash
git add dashboard/detail-panel.mjs dashboard/pilotage.mjs dashboard/app.js tests/test_dashboard.py
git commit -m "feat: render daily delivery pilotage"
```

### Task 5: Replace agent cards with compact expandable rows

**Files:**
- Create: `dashboard/format.mjs`
- Create: `dashboard/agents.mjs`
- Modify: `dashboard/app.js`
- Modify: `tests/test_dashboard.py:2171-2289`

- [ ] **Step 1: Write failing agent module contracts**

Add:

```python
def test_agents_are_compact_and_details_are_progressively_disclosed(self):
    agents = self.modules["agents.mjs"]
    self.assertIn('className = "agent-row"', agents)
    self.assertIn('document.createElement("details")', agents)
    self.assertIn("Dernier passage", agents)
    self.assertIn("Prochain passage", agents)
    for detail in ("Modèle", "Jetons", "Coût estimé", "Fréquence", "PID"):
        self.assertIn(detail, agents)
    for action in ("Déclencher", "Réinstaller", "Arrêter"):
        self.assertIn(action, agents)
    self.assertNotIn("innerHTML", agents)
```

- [ ] **Step 2: Run the contract and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest.test_agents_are_compact_and_details_are_progressively_disclosed -v
```

Expected: error because `agents.mjs` does not exist.

- [ ] **Step 3: Extract shared formatters**

Create `dashboard/format.mjs` by moving the existing formatter logic without
changing its exact-decimal behavior:

```javascript
export const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

export function formatDate(value) {
  if (!value) return "Aucune exécution";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date indisponible" : dateFormatter.format(date);
}

export function formatTokens(value) {
  if (typeof value === "string" && /^\d+$/.test(value)) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  return "Données indisponibles";
}

export function formatCost(value) {
  if (typeof value !== "string") return "Données indisponibles";
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return "Données indisponibles";
  const integer = new Intl.NumberFormat("fr-FR").format(BigInt(match[1]));
  const decimals = (match[2] || "").padEnd(4, "0").slice(0, 6);
  return `${integer},${decimals} USD`;
}
```

- [ ] **Step 4: Implement compact agent rows**

Create `dashboard/agents.mjs`:

```javascript
import {formatCost, formatDate, formatTokens} from "./format.mjs";

function fact(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "agent-fact";
  const term = document.createElement("span");
  term.textContent = label;
  const detail = document.createElement("strong");
  detail.textContent = value;
  wrapper.append(term, detail);
  return wrapper;
}

function actionButton(label, action, skill, onAction, destructive = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = destructive ? "button button-danger" : "button button-quiet";
  button.textContent = label;
  button.dataset.skill = skill;
  button.addEventListener("click", () => onAction(action, skill));
  return button;
}

function agentRow(agent, handlers) {
  const row = document.createElement("article");
  row.className = "agent-row";
  const header = document.createElement("div");
  header.className = "agent-row-main";
  const identity = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = agent.skill || "Agent sans nom";
  const description = document.createElement("p");
  description.textContent = agent.role_description || "Rôle non documenté.";
  identity.append(title, description);
  header.append(
    identity,
    fact("État", agent.running ? "En cours" : agent.loaded ? "Planifié" : "Arrêté"),
    fact("Dernier passage", formatDate(agent.latest_history?.finished_at)),
    fact("Prochain passage", formatDate(agent.estimated_next_pass)),
  );

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Voir les détails techniques et les contrôles";
  const detailGrid = document.createElement("div");
  detailGrid.className = "agent-detail-grid";
  detailGrid.append(
    fact("Modèle", agent.configured_model || "Indisponible"),
    fact("Jetons", formatTokens(agent.usage_7d?.tokens?.total_tokens)),
    fact("Coût estimé", formatCost(agent.usage_7d?.estimated_cost_usd)),
    fact("Fréquence", agent.interval_seconds ? `${agent.interval_seconds} s` : "Indisponible"),
    fact("PID", agent.pid || "Indisponible"),
  );
  const controls = document.createElement("div");
  controls.className = "agent-controls";
  controls.append(
    actionButton("Déclencher", "trigger", agent.skill, handlers.control),
    actionButton("Réinstaller", "restart", agent.skill, handlers.control),
    actionButton("Arrêter", "stop", agent.skill, handlers.control, true),
  );
  details.append(summary, detailGrid, handlers.modelControl(agent), controls);
  row.append(header, details);
  return row;
}

export function renderAgents(root, disabledRoot, disabledCount, snapshot, handlers) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  root.replaceChildren(...agents.map((agent) => agentRow(agent, handlers)));
  const disabled = Array.isArray(snapshot?.disabled_roles) ? snapshot.disabled_roles : [];
  disabledCount.textContent = String(disabled.length);
  disabledRoot.replaceChildren(...disabled.map((role) => {
    const line = document.createElement("p");
    line.textContent = `${role.skill || "Rôle inconnu"} · ${role.reason || "Non configuré"}`;
    return line;
  }));
}
```

- [ ] **Step 5: Wire agents without changing control semantics**

In `app.js`, replace the old card render call with:

```javascript
renderAgents(
  document.querySelector("#agent-list"),
  document.querySelector("#disabled-list"),
  document.querySelector("#disabled-count"),
  sources.snapshot,
  {
    control,
    modelControl: (agent) => createModelControl(
      agent,
      sources.snapshot?.model_catalog,
      sources.snapshot?.global_state === "stopped",
    ),
  },
);
```

Keep the existing `control()`, `changeModel()`, per-skill pending set, confirmation
copy, exact action payloads, and fresh local refresh after a model change.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest -v
```

Expected: all asset contracts pass.

```bash
git add dashboard/format.mjs dashboard/agents.mjs dashboard/app.js tests/test_dashboard.py
git commit -m "feat: compact dashboard agent controls"
```

### Task 6: Move seven-day activity into the History view

**Files:**
- Create: `dashboard/history.mjs`
- Modify: `dashboard/app.js`
- Modify: `tests/test_dashboard.py:2171-2226`

- [ ] **Step 1: Write a failing history module contract**

Add:

```python
def test_history_module_keeps_filters_and_safe_metadata(self):
    history = self.modules["history.mjs"]
    self.assertIn("export function renderHistory", history)
    self.assertIn("export function historyPath", history)
    self.assertIn("URLSearchParams", history)
    self.assertIn("total_tokens", history)
    self.assertIn("Modèle/usage indisponibles", history)
    self.assertNotIn("innerHTML", history)
```

- [ ] **Step 2: Run the contract and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest.test_history_module_keeps_filters_and_safe_metadata -v
```

Expected: error because `history.mjs` does not exist.

- [ ] **Step 3: Extract history rendering**

Create `dashboard/history.mjs`:

```javascript
import {formatDate, formatTokens} from "./format.mjs";

export function historyPath(skill, outcome) {
  const query = new URLSearchParams();
  if (skill) query.set("skill", skill);
  if (outcome) query.set("outcome", outcome);
  const suffix = query.toString();
  return suffix ? `/api/history?${suffix}` : "/api/history";
}

export function renderHistory(root, records, modelCatalog = {}) {
  root.replaceChildren();
  const safeRecords = Array.isArray(records) ? records : [];
  if (!safeRecords.length) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "Aucune activité ne correspond aux filtres.";
    root.append(empty);
    return;
  }
  safeRecords.forEach((record) => {
    const item = document.createElement("li");
    const heading = document.createElement("strong");
    heading.textContent = `${record.skill || "Agent inconnu"} · ${record.outcome || "inconnu"}`;
    const date = document.createElement("time");
    date.textContent = formatDate(record.finished_at);
    const summary = document.createElement("p");
    summary.textContent = record.summary || "Aucun résumé.";
    const metadata = document.createElement("p");
    metadata.className = "history-metadata";
    const modelKnown = typeof record.model === "string" && Object.hasOwn(modelCatalog, record.model);
    metadata.textContent = modelKnown && record.usage
      ? `${record.model} · ${formatTokens(record.usage.total_tokens)} jetons`
      : "Modèle/usage indisponibles";
    item.append(heading, date, summary, metadata);
    root.append(item);
  });
}

export function syncHistorySkills(select, agents) {
  const selected = select.value;
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Tous";
  const options = (Array.isArray(agents) ? agents : [])
    .map((agent) => agent.skill)
    .filter((skill) => typeof skill === "string" && skill)
    .sort()
    .map((skill) => {
      const option = document.createElement("option");
      option.value = skill;
      option.textContent = skill;
      return option;
    });
  select.replaceChildren(all, ...options);
  select.value = options.some((option) => option.value === selected) ? selected : "";
}
```

- [ ] **Step 4: Wire filters to the dedicated view**

Replace the old `historyPath()` and renderer in `app.js` with:

```javascript
import {historyPath, renderHistory, syncHistorySkills} from "./history.mjs";

const historySkill = document.querySelector("#history-skill");
const historyOutcome = document.querySelector("#history-outcome");
document.querySelector("#history-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  refreshHistory({manual: true});
});

async function refreshHistory() {
  sources.history = await fetchJson(historyPath(
    historySkill.value,
    historyOutcome.value,
  ));
  renderHistory(
    document.querySelector("#activity-list"),
    sources.history,
    sources.snapshot?.model_catalog,
  );
}
```

Call this immediately after a successful snapshot refresh:

```javascript
syncHistorySkills(historySkill, sources.snapshot?.agents);
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest -v
```

Expected: all asset contracts pass.

```bash
git add dashboard/history.mjs dashboard/app.js tests/test_dashboard.py
git commit -m "feat: add focused dashboard history view"
```

### Task 7: Keep sources independent and preserve stale data

**Files:**
- Create: `dashboard/api.mjs`
- Create: `dashboard/source-store.mjs`
- Create: `tests/dashboard_source_store.test.mjs`
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/pilotage.mjs`
- Modify: `tests/test_dashboard.py:2103-2169`

- [ ] **Step 1: Write failing source-store tests**

Create `tests/dashboard_source_store.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {createSourceStore} from "../dashboard/source-store.mjs";

test("a failed refresh returns the last successful payload as stale", async () => {
  let now = 1_000;
  const store = createSourceStore(() => now);
  const fresh = await store.load("gitlab", async () => ({groups: {todo: []}}));
  assert.deepEqual(fresh, {
    data: {groups: {todo: []}},
    stale: false,
    lastSuccess: 1_000,
    error: null,
  });

  now = 2_000;
  const stale = await store.load("gitlab", async () => {
    throw new Error("offline");
  });
  assert.equal(stale.stale, true);
  assert.equal(stale.lastSuccess, 1_000);
  assert.deepEqual(stale.data, {groups: {todo: []}});
  assert.equal(stale.error, "offline");
});

test("a first-load failure has no stale payload", async () => {
  const store = createSourceStore(() => 1_000);
  const failed = await store.load("local", async () => {
    throw new Error("unavailable");
  });
  assert.equal(failed.data, null);
  assert.equal(failed.stale, false);
  assert.equal(failed.lastSuccess, null);
});
```

- [ ] **Step 2: Run the Node tests and verify failure**

Run:

```bash
node --test tests/dashboard_source_store.test.mjs
```

Expected: failure with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Implement the source store**

Create `dashboard/source-store.mjs`:

```javascript
export function createSourceStore(now = () => Date.now()) {
  const states = new Map();

  async function load(name, loader) {
    const previous = states.get(name);
    try {
      const data = await loader();
      const state = {
        data,
        stale: false,
        lastSuccess: now(),
        error: null,
      };
      states.set(name, state);
      return state;
    } catch (error) {
      const state = {
        data: previous?.data ?? null,
        stale: previous?.data != null,
        lastSuccess: previous?.lastSuccess ?? null,
        error: error instanceof Error ? error.message : "service unavailable",
      };
      states.set(name, state);
      return state;
    }
  }

  function get(name) {
    return states.get(name) ?? {
      data: null,
      stale: false,
      lastSuccess: null,
      error: null,
    };
  }

  return {load, get};
}
```

- [ ] **Step 4: Centralize authenticated JSON access**

Create `dashboard/api.mjs`:

```javascript
export function createApi(sessionToken) {
  async function request(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
    });
    if (!response.ok) {
      throw new Error(`Requête refusée (${response.status})`);
    }
    return response.json();
  }

  function get(path) {
    return request(path);
  }

  function action(body) {
    return request("/api/actions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify(body),
    });
  }

  return {get, action};
}
```

Initialize it once in `app.js`:

```javascript
import {createApi} from "./api.mjs";

const sessionToken = document.querySelector('meta[name="pitcrew-session"]')?.content ?? "";
const api = createApi(sessionToken);
```

Replace every GET `fetchJson(path)` call with `api.get(path)` and every action
request with `api.action({...})`, preserving the exact action bodies established
in Tasks 4–6.

- [ ] **Step 5: Refactor refreshes by source**

In `app.js`, add:

```javascript
import {createSourceStore} from "./source-store.mjs";

const sourceStore = createSourceStore();
let lastGitLabRefresh = 0;

async function refreshLocal() {
  const [snapshot, history] = await Promise.all([
    sourceStore.load("snapshot", () => api.get("/api/status")),
    sourceStore.load("history", () => api.get(historyPath(
      historySkill.value,
      historyOutcome.value,
    ))),
  ]);
  if (snapshot.data) {
    sources.snapshot = snapshot.data;
    document.body.dataset.globalState = snapshot.data.global_state || "running";
    document.querySelector("#global-stop-button").hidden = snapshot.data.global_state === "stopped";
    document.querySelector("#global-resume-button").hidden = snapshot.data.global_state !== "stopped";
  }
  if (history.data) sources.history = history.data;
  renderAgentsView();
  renderHistoryView();
}

async function refreshHumanActions() {
  const [decisions, proposals] = await Promise.all([
    sourceStore.load("decisions", () => api.get("/api/decisions")),
    sourceStore.load("proposals", () => api.get("/api/proposals")),
  ]);
  if (decisions.data) sources.decisions = decisions.data;
  if (proposals.data) sources.proposals = proposals.data;
}

async function refreshGitLab({manual = false} = {}) {
  const now = Date.now();
  if (!manual && now - lastGitLabRefresh < 60_000) return;
  lastGitLabRefresh = now;
  const state = await sourceStore.load(
    "gitlab",
    () => api.get(manual ? "/api/gitlab?refresh=1" : "/api/gitlab"),
  );
  if (state.data) sources.work = state.data;
}

async function refresh({manual = false} = {}) {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    document.querySelector("#refresh-button").disabled = true;
    await Promise.all([
      refreshLocal(),
      refreshHumanActions(),
      refreshGitLab({manual}),
    ]);
    renderPilotageView();
    renderSourceStates();
  })();
  try {
    await refreshPromise;
  } finally {
    refreshPromise = null;
    document.querySelector("#refresh-button").disabled = false;
  }
}
```

Implement `renderSourceStates()` so every affected view receives a bounded
status line:

```javascript
function sourceStatus(name, label, retry) {
  const state = sourceStore.get(name);
  if (!state.error) return null;
  const status = document.createElement("div");
  status.className = state.stale ? "source-state source-state-stale" : "source-state source-state-error";
  const message = document.createElement("p");
  message.textContent = state.stale
    ? `${label} · Données anciennes · dernière réussite ${new Date(state.lastSuccess).toLocaleTimeString("fr-FR")}`
    : `${label} indisponible`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-quiet";
  button.textContent = "Réessayer";
  button.addEventListener("click", retry);
  status.append(message, button);
  return status;
}

function renderSourceState(root, states) {
  root.replaceChildren(...states.filter(Boolean));
}

function renderSourceStates() {
  renderSourceState(document.querySelector("#pilotage-source-state"), [
    sourceStatus("decisions", "Décisions", refreshHumanActions),
    sourceStatus("proposals", "Propositions", refreshHumanActions),
    sourceStatus("gitlab", "GitLab", () => refreshGitLab({manual: true})),
  ]);
  renderSourceState(document.querySelector("#agents-source-state"), [
    sourceStatus("snapshot", "État local", refreshLocal),
  ]);
  renderSourceState(document.querySelector("#history-source-state"), [
    sourceStatus("history", "Historique", refreshLocal),
  ]);
}
```

Add these containers to the start of their matching views in `index.html`:

```html
<div id="pilotage-source-state" aria-live="polite"></div>
<div id="agents-source-state" aria-live="polite"></div>
<div id="history-source-state" aria-live="polite"></div>
```

- [ ] **Step 6: Preserve per-resource action feedback**

Keep a module-level map in `app.js`:

```javascript
const actionStates = new Map();

function setActionState(key, kind, message) {
  actionStates.set(key, {kind, message});
  document.querySelector("#detail-action-status").className = `action-state action-state-${kind}`;
  document.querySelector("#detail-action-status").textContent = message;
}

async function runAction(key, messages, operation) {
  setActionState(key, "pending", messages.pending);
  try {
    const result = await operation();
    setActionState(key, "success", messages.success);
    return result;
  } catch (error) {
    setActionState(key, "error", messages.error);
    throw error;
  }
}
```

Wrap each existing operation with `runAction()`. For example, the MR handler uses:

```javascript
await runAction(
  `merge_request:${mergeRequest.canonical_url}`,
  {
    pending: `Fusion de !${mergeRequest.iid}…`,
    success: `MR !${mergeRequest.iid} fusionnée.`,
    error: `Impossible de fusionner la MR !${mergeRequest.iid}.`,
  },
  () => api.action({action: "merge-merge-request", iid: mergeRequest.iid}),
);
```

Use keys `decision:${ticket_id}`, `proposal:${id}`, the issue resource key,
`agent:${skill}`, and `model:${skill}` for the other operations. Rendering
refreshed source data must not clear `actionStates`; a new action on the same key
replaces the previous state.

- [ ] **Step 7: Add the source-store test to the deterministic suite**

Change the Node line in `tests/run.sh` to:

```bash
node --test tests/dashboard_view_model.test.mjs tests/dashboard_source_store.test.mjs
```

- [ ] **Step 8: Run Node, asset, and service tests**

Run:

```bash
node --test tests/dashboard_view_model.test.mjs tests/dashboard_source_store.test.mjs
python3 -m unittest \
  tests.test_dashboard.DashboardServiceTest \
  tests.test_dashboard.DashboardAssetContractTest -v
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit resilient refresh behavior**

```bash
git add dashboard/api.mjs dashboard/source-store.mjs tests/dashboard_source_store.test.mjs dashboard/index.html dashboard/app.js dashboard/pilotage.mjs tests/run.sh tests/test_dashboard.py
git commit -m "feat: preserve stale dashboard data"
```

### Task 8: Apply the laptop-first visual system and verify real workflows

**Files:**
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py:2227-2289`
- Modify: `README.md`

- [ ] **Step 1: Write failing visual contract assertions**

Replace selectors tied to the old card grid with:

```python
def test_css_supports_laptop_shell_workflow_and_accessibility(self):
    for selector in (
        ".app-header",
        ".app-shell",
        ".app-sidebar",
        ".action-queue-list",
        ".workflow-columns",
        ".workflow-lane",
        ".agent-row",
        "#detail-panel",
        ".source-state-stale",
    ):
        self.assertIn(selector, self.styles)
    self.assertIn("position: sticky", self.styles)
    self.assertIn("overflow-x: auto", self.styles)
    self.assertIn(":focus-visible", self.styles)
    self.assertIn("@media (max-width: 900px)", self.styles)
    self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)
```

- [ ] **Step 2: Run the CSS contract and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardAssetContractTest.test_css_supports_laptop_shell_workflow_and_accessibility -v
```

Expected: failure for the new application selectors.

- [ ] **Step 3: Replace the old page styling**

Keep the existing color semantics and implement these required layout rules in
`dashboard/styles.css`:

```css
:root {
  color-scheme: light;
  --ink: #17211c;
  --muted: #5a6860;
  --surface: #ffffff;
  --canvas: #eef2ef;
  --line: #d8ded9;
  --accent: #146b45;
  --accent-strong: #0b4f32;
  --accent-soft: #dff2e7;
  --warning: #8a5400;
  --warning-soft: #fff0cc;
  --error: #a32620;
  --error-soft: #fde7e5;
  --shadow: 0 12px 32px rgba(32, 48, 39, 0.08);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.app-header {
  position: sticky;
  z-index: 20;
  top: 0;
  display: flex;
  min-height: 64px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: var(--accent-strong);
  color: white;
}

.app-shell {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  min-height: calc(100vh - 64px);
}

.app-sidebar {
  position: sticky;
  top: 64px;
  align-self: start;
  height: calc(100vh - 64px);
  padding: 18px 12px;
  border-right: 1px solid var(--line);
  background: #f8faf8;
}

.app-main {
  min-width: 0;
  padding: 24px;
}

.action-queue-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.workflow-columns {
  display: grid;
  min-width: 920px;
  grid-template-columns: repeat(4, minmax(215px, 1fr));
  gap: 12px;
}

#workflow-board {
  overflow-x: auto;
  padding-bottom: 8px;
}

.workflow-lane,
.agent-row,
.action-card {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
}

#detail-panel {
  width: min(520px, 100vw);
  height: 100vh;
  max-height: none;
  margin: 0 0 0 auto;
  border: 0;
  border-left: 1px solid var(--line);
  padding: 24px;
}

#detail-panel::backdrop {
  background: rgba(11, 34, 23, 0.35);
}

.source-state-stale {
  color: var(--warning);
  border-color: #edcc82;
  background: var(--warning-soft);
}

@media (max-width: 900px) {
  .app-shell { grid-template-columns: 72px minmax(0, 1fr); }
  .app-sidebar .project-name,
  .nav-item:not([aria-current="page"]) .nav-count { display: none; }
  .action-queue-list { grid-template-columns: 1fr; }
  .app-main { padding: 16px; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
```

Add the remaining component rules:

```css
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; color: var(--ink); background: var(--canvas); }
button, input, select { font: inherit; }

.button {
  min-height: 38px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  color: var(--ink);
  font-weight: 750;
  cursor: pointer;
}
.button-primary { border-color: var(--accent); background: var(--accent); color: white; }
.button-danger { border-color: var(--error); color: var(--error); }
.button-quiet { background: transparent; }
.button:disabled { cursor: not-allowed; opacity: 0.55; }
button:focus-visible, a:focus-visible, input:focus-visible,
select:focus-visible, summary:focus-visible {
  outline: 3px solid #2d73da;
  outline-offset: 3px;
}

.brand { display: flex; align-items: center; gap: 9px; color: white; text-decoration: none; font-weight: 850; }
.brand-mark { display: grid; width: 30px; height: 30px; place-items: center; border-radius: 8px; background: #62d39a; color: var(--accent-strong); }
.header-status { display: flex; align-items: center; gap: 10px; }
.project-name { margin: 0 8px 16px; font-weight: 850; }
.nav-item { display: flex; width: 100%; justify-content: space-between; margin-bottom: 5px; padding: 10px; border: 0; border-radius: 9px; background: transparent; color: var(--muted); text-align: left; }
.nav-item[aria-current="page"] { background: var(--accent-soft); color: var(--accent); font-weight: 850; }
.nav-count { min-width: 22px; border-radius: 11px; background: rgba(20,107,69,.12); text-align: center; }
#crew-health { margin-top: 28px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: white; }
#crew-health h2 { margin: 0 0 8px; font-size: .85rem; }
#crew-health dl { display: grid; grid-template-columns: 1fr auto; gap: 5px; margin: 0; font-size: .8rem; }
#crew-health dd { margin: 0; font-weight: 850; }

.action-queue-list { margin-bottom: 24px; }
.action-card { display: grid; min-width: 0; grid-template-columns: 1fr auto; gap: 6px 10px; padding: 14px; border-color: #e5c471; background: #fff8df; }
.action-card h3 { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; font-size: .95rem; }
.action-kind { grid-column: 1 / -1; margin: 0; color: var(--warning); font-size: .7rem; font-weight: 850; text-transform: uppercase; }

.workflow-lane { min-height: 320px; padding: 10px; background: #e3e9e5; }
.workflow-lane-blocked { background: #f5e7e1; }
.workflow-lane > h3 { margin: 0 0 9px; font-size: .9rem; }
.work-card { margin-bottom: 8px; padding: 11px; box-shadow: 0 3px 8px rgba(28,43,36,.05); }
.work-card h3 { margin: 5px 0 9px; font-size: .9rem; }
.work-reference, .work-route { margin: 0; color: var(--muted); font-size: .75rem; }
#done-work { margin-top: 12px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: #f8faf8; }
#done-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }

.agent-row { margin-bottom: 10px; padding: 14px; }
.agent-row-main { display: grid; grid-template-columns: minmax(260px, 2fr) repeat(3, minmax(120px, 1fr)); align-items: center; gap: 12px; }
.agent-row h2 { margin: 0; font-size: 1rem; }
.agent-row p { margin: 3px 0 0; color: var(--muted); }
.agent-fact { display: grid; gap: 2px; }
.agent-fact span { color: var(--muted); font-size: .72rem; }
.agent-detail-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; padding: 12px 0; }
.agent-controls { display: flex; flex-wrap: wrap; gap: 8px; }
.agent-row details { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }

#activity-list { display: grid; gap: 9px; padding: 0; list-style: none; }
#activity-list li { display: grid; grid-template-columns: 1fr auto; gap: 6px 16px; padding: 14px; border: 1px solid var(--line); border-radius: 12px; background: white; }
#activity-list p { grid-column: 1 / -1; margin: 0; }
.history-metadata { color: var(--muted); font-size: .8rem; }
.empty-state { margin: 0; padding: 20px; color: var(--muted); text-align: center; }

#detail-panel[open] { display: flex; flex-direction: column; }
.detail-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 14px; }
#detail-content { overflow-y: auto; padding: 18px 0; }
.detail-actions, .decision-choices, .proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.action-state { margin-top: auto; padding: 10px; border-radius: 8px; }
.action-state-pending { color: var(--warning); background: var(--warning-soft); }
.action-state-success { color: var(--accent); background: var(--accent-soft); }
.action-state-error, .source-state-error { color: var(--error); background: var(--error-soft); }
.source-state { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border: 1px solid currentColor; border-radius: 9px; }
body[data-global-state="stopped"] .workflow-columns,
body[data-global-state="stopped"] .action-queue-list { opacity: .65; }
```

Remove selectors that are no longer referenced by the new HTML or modules.

- [ ] **Step 4: Update the operator documentation**

Replace the dashboard UI paragraph in `README.md` with:

```markdown
Open `http://127.0.0.1:8765`. **Pilotage** shows human decisions first and groups
active GitLab work into `À faire`, `En cours`, `En revue`, and `Bloqué`; work
completed today is folded below the board. **Agents** contains compact operational
rows with expandable model, usage, schedule, and control details. **Historique**
keeps the seven-day run log and filters. GitLab failures retain the last successful
remote data while local monitoring and controls continue to work.
```

- [ ] **Step 5: Run deterministic verification**

Run:

```bash
bash tests/run.sh
```

Expected final line: `Pitcrew verification passed`.

- [ ] **Step 6: Run the real-browser verification**

Start the dashboard:

```bash
./bin/pitcrew-dashboard
```

Use the `playwright` skill and verify at `1280x800`:

1. `Pilotage` shows the action count, first three actions, all four active lanes,
   crew health, and folded completed work without page-level horizontal scroll.
2. Opening a card shows the right-hand dialog; `Escape` closes it and restores
   focus to the originating card.
3. `Agents` reveals technical facts only after expanding a row.
4. `Historique` applies agent and outcome filters.
5. Keyboard-only navigation reaches every view and action with a visible focus
   indicator.

Capture one screenshot of `Pilotage` and one of the open detail panel in
`/private/tmp`; do not add screenshots to the repository.

- [ ] **Step 7: Run a simulated degraded-data check**

In the browser fixture or with a temporary request interception, make
`/api/gitlab` return `500` after one successful load. Confirm:

- the existing board remains visible;
- `Données anciennes` and the last-success time appear;
- agent controls still work;
- `Réessayer` performs a new GitLab request.

- [ ] **Step 8: Commit the finished UI**

```bash
git add dashboard/styles.css tests/test_dashboard.py README.md
git commit -m "feat: polish daily dashboard ui"
```

### Task 9: Final regression and scope audit

**Files:**
- Verify only; modify a file only to correct a regression found by this task.

- [ ] **Step 1: Confirm the intended diff**

Run:

```bash
git diff 9361fe0..HEAD --stat
git status --short
```

Expected: the UI modules, explicit server routes, tests, README, and plan are
present; unrelated working-tree files remain untouched.

- [ ] **Step 2: Confirm security and action invariants**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard.DashboardHttpTest \
  tests.test_dashboard.DashboardServiceTest.test_merge_merge_request_rejects_deployment_targets \
  tests.test_dashboard.DashboardServiceTest.test_control_uses_exact_safe_argument_arrays -v
```

Expected: all selected tests pass, including localhost/session protections and
the `preprod`/`prod` merge block.

- [ ] **Step 3: Run the complete suite once more**

Run:

```bash
bash tests/run.sh
```

Expected final line: `Pitcrew verification passed`.

- [ ] **Step 4: Review the final change**

Invoke `superpowers:requesting-code-review`. The reviewer must compare the final
diff to `docs/superpowers/specs/2026-07-26-dashboard-day-to-day-ui-design.md`,
check the preserved pre-existing dashboard changes, and report findings before
any completion claim.

- [ ] **Step 5: Commit any review corrections**

If the review identifies a concrete issue, write a failing regression test, run
it to confirm failure, apply the smallest fix, rerun the focused and complete
suites, then commit only those files:

```bash
git add dashboard/app.js dashboard/pilotage.mjs dashboard/agents.mjs \
  dashboard/history.mjs dashboard/source-store.mjs dashboard/styles.css \
  scripts/pitcrew_dashboard.py tests/test_dashboard.py \
  tests/dashboard_view_model.test.mjs tests/dashboard_source_store.test.mjs
git commit -m "fix: address dashboard ui review"
```

If the review has no findings, do not create an empty commit.
