import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {createDetailPanel} from "../dashboard/detail-panel.mjs";
import {
  preserveFocus,
  renderActionFeedback,
  renderActionList,
  renderItemDetail,
  renderPilotage,
  summarizeAgentFailure,
} from "../dashboard/pilotage.mjs";

class Focusable extends EventTarget {
  constructor() {
    super();
    this.connected = true;
    this.focusCalls = 0;
    this.attributes = new Map();
  }

  focus() {
    document.activeElement = this;
    this.focusCalls += 1;
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }
}

class Dialog extends EventTarget {
  constructor() {
    super();
    this.open = false;
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
    this.dispatchEvent(new Event("close"));
  }
}

class Element extends EventTarget {
  constructor(tagName = "div") {
    super();
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.connected = true;
    this.disabled = false;
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  replaceChildren(...children) {
    this.children.forEach((child) => { child.connected = false; });
    this.children = children;
  }

  contains(node) {
    return node === this || this.children.some((child) => child.contains?.(node) || child === node);
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if (selector === "[data-focus-key]" && node.getAttribute?.("data-focus-key")) matches.push(node);
      node.children?.forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }

  focus() {
    document.activeElement = this;
  }
}

test("agent failure summaries extract useful JSON fields and stay bounded", () => {
  const context = summarizeAgentFailure(JSON.stringify({
    reason: "  Smoke   paiement échoué ",
    next_action: " Relancer\nle scénario ciblé ",
  }));
  assert.equal(
    context,
    "Raison : Smoke paiement échoué · Prochaine action : Relancer le scénario ciblé",
  );

  const longFallback = `Préfixe ${"incident ".repeat(40)}`;
  assert.equal(summarizeAgentFailure(longFallback).length <= 180, true);
  assert.equal(summarizeAgentFailure(longFallback).endsWith("…"), true);
});

test("action cards render the bounded agent failure context instead of raw JSON", () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    activeElement: null,
    createElement: (tagName) => new Element(tagName),
  };
  const root = new Element();
  renderActionList(root, [{
    kind: "agent-failure",
    key: "agent:qa-run",
    resource: {
      skill: "qa-run",
      health: "failed",
      latest_history: {
        summary: JSON.stringify({
          reason: "Le smoke test a échoué",
          next_action: "Inspecter le premier écart",
          noisy_payload: "x".repeat(500),
        }),
      },
    },
  }]);

  const [context] = root.children[0].children
    .filter((child) => child.className === "action-card-context");
  assert.equal(
    context.textContent,
    "Raison : Le smoke test a échoué · Prochaine action : Inspecter le premier écart",
  );
  assert.equal(context.textContent.includes("noisy_payload"), false);
  globalThis.document = originalDocument;
});

test("action queue cards expose a localized action type", () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    activeElement: null,
    createElement: (tagName) => new Element(tagName),
  };
  const root = new Element();

  renderActionList(root, [{
    kind: "merge-request",
    key: "merge-request:16",
    label: "Fusionner",
    resource: {
      iid: 16,
      title: "Corriger la prévisualisation",
      source_branch: "fix/preview",
      target_branch: "develop",
    },
  }]);

  const [kind] = root.children[0].children
    .filter((child) => child.className === "action-card-kind");
  assert.equal(kind.textContent, "Merge request");
  globalThis.document = originalDocument;
});

test("issue details present GitLab markdown as readable safe text", () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => new Element(tagName),
    createTextNode: (textContent) => ({textContent}),
  };

  const detail = renderItemDetail({
    kind: "issue",
    key: "issue:42",
    resource: {
      title: "Ticket",
      description: "**Source:** research. `getbill:doc-sync`\n\n**What:** Fix [the docs](https://example.test/docs).",
    },
  });

  assert.equal(
    detail.body.children[0].textContent,
    "Source: research. getbill:doc-sync What: Fix the docs.",
  );
  assert.equal(detail.body.children[0].textContent.includes("**"), false);
  assert.equal(detail.body.children[0].textContent.includes("`"), false);
  globalThis.document = originalDocument;
});

test("workflow cards and details expose correlated delivery context", () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    activeElement: null,
    createElement: (tagName) => new Element(tagName),
    createTextNode: (textContent) => ({textContent}),
  };
  const issueUrl = "https://gitlab.example/group/app/-/issues/42";
  const mergeRequestUrl = "https://gitlab.example/group/app/-/merge_requests/7";
  const workflowBoard = new Element();
  let opened;
  renderPilotage(
    {workflowBoard},
    {
      work: {
        groups: {
          processing: [{
            resource_type: "issue",
            canonical_url: issueUrl,
            web_url: issueUrl,
            iid: 42,
            lifecycle: "processing",
            title: "Fiabiliser les paiements",
            updated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
            labels: ["pitcrew-agent", "type::bug", "priority::high"],
            related_merge_requests: [mergeRequestUrl],
            active_run: {skill: "implementer-run", state: "running"},
            agent_action: {skill: "implementer-run", label: "Voir le détail"},
          }],
        },
        merge_requests: [{
          canonical_url: mergeRequestUrl,
          iid: 7,
          state: "opened",
          source_branch: "fix/payments",
          target_branch: "develop",
          author_username: "jo",
          pipeline_status: "passed",
        }],
      },
      decisions: {
        pending: {ticket_id: "42", ticket: {canonical_url: issueUrl}},
      },
      snapshot: {
        agents: [{
          skill: "implementer-run",
          live_status: {phase: "validation"},
          latest_history: {
            outcome: "success",
            finished_at: "2026-07-26T10:00:00Z",
            summary: `Paiement vérifié ${"sans écart ".repeat(30)}`,
          },
        }],
      },
    },
    {
      filters: () => ({}),
      openItem: (entry) => { opened = entry; },
    },
  );

  const processingLane = workflowBoard.children
    .find((lane) => lane.children[0]?.textContent === "En cours · 1");
  const card = processingLane.children[1];
  const cardText = card.children.map((child) => child.textContent).join(" | ");
  assert.match(cardText, /Ticket · mis à jour il y a 2 h/);
  assert.match(cardText, /type::bug · priority::high · Décision requise/);
  assert.equal(cardText.includes("pitcrew-agent"), false);
  assert.match(cardText, /implementer-run · validation/);
  assert.match(cardText, /MR !7 · opened · pipeline passed/);

  card.children.find((child) => child.tagName === "button").dispatchEvent(new Event("click"));
  const detail = renderItemDetail(opened);
  const flattenText = (node) => [
    node.textContent || "",
    ...(node.children || []).map(flattenText),
  ].join("");
  const detailText = flattenText(detail.body);
  assert.match(detailText, /MR liée : !7 · fix\/payments → develop · Auteur : jo · Pipeline : passed/);
  assert.match(detailText, /Agent courant : implementer-run · Phase : validation/);
  assert.match(detailText, /Dernier résumé : Paiement vérifié/);
  assert.match(detailText, /Dernière exécution : success · 26 juil. 2026/);
  assert.equal(detailText.includes("sans écart ".repeat(30)), false);
  globalThis.document = originalDocument;
});

test("detail launch action sends only one request after it resolves", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => new Element(tagName),
    createTextNode: (textContent) => ({textContent}),
  };
  let launches = 0;
  let resolveLaunch;
  const detail = renderItemDetail(
    {kind: "issue", key: "issue:42", resource: {title: "Ticket", agent_action: {label: "Lancer", available: true}}},
    {forEntry: () => [{label: "Lancer", singleUse: true, successLabel: "Lancement accepté", run: () => new Promise((resolve) => {
      launches += 1;
      resolveLaunch = () => resolve(true);
    })}]},
  );
  const [actions] = detail.body.children.filter((child) => child.className === "detail-actions");
  const [button] = actions.children;

  button.dispatchEvent(new Event("click"));
  button.dispatchEvent(new Event("click"));
  assert.equal(launches, 1);
  assert.equal(button.disabled, true);

  resolveLaunch();
  await new Promise((resolve) => setImmediate(resolve));
  button.dispatchEvent(new Event("click"));
  assert.equal(launches, 1);
  assert.equal(button.textContent, "Lancement accepté");
  globalThis.document = originalDocument;
});

test("ordinary detail actions remain repeatable", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => new Element(tagName),
    createTextNode: (textContent) => ({textContent}),
  };
  let calls = 0;
  const detail = renderItemDetail(
    {kind: "decision", key: "decision:42", resource: {title: "Décision"}},
    {forEntry: () => [{label: "Approuver", run: async () => { calls += 1; }}]},
  );
  const [actions] = detail.body.children.filter((child) => child.className === "detail-actions");
  const [button] = actions.children;

  button.dispatchEvent(new Event("click"));
  await new Promise((resolve) => setImmediate(resolve));
  button.dispatchEvent(new Event("click"));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(calls, 2);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Approuver");
  globalThis.document = originalDocument;
});

test("repeatable detail actions disable themselves and show a waiting label in flight", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tagName) => new Element(tagName),
    createTextNode: (textContent) => ({textContent}),
  };
  let calls = 0;
  let finish;
  const detail = renderItemDetail(
    {kind: "decision", key: "decision:42", resource: {title: "Décision"}},
    {forEntry: () => [{
      label: "Approuver",
      run: () => new Promise((resolve) => {
        calls += 1;
        finish = resolve;
      }),
    }]},
  );
  const [actions] = detail.body.children.filter((child) => child.className === "detail-actions");
  const [button] = actions.children;

  button.dispatchEvent(new Event("click"));
  button.dispatchEvent(new Event("click"));
  assert.equal(calls, 1);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, "Traitement…");

  finish();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Approuver");
  globalThis.document = originalDocument;
});

test("action feedback renders success and error states with safe DOM text", () => {
  const root = new Element("p");

  renderActionFeedback(root, {kind: "success", message: "Action acceptée."});
  assert.equal(root.className, "action-state action-state-success");
  assert.equal(root.textContent, "Action acceptée.");

  renderActionFeedback(root, {kind: "error", message: "Action refusée."});
  assert.equal(root.className, "action-state action-state-error");
  assert.equal(root.textContent, "Action refusée.");
});

test("polling render preserves a focused control by its stable key", () => {
  const originalDocument = globalThis.document;
  const root = new Element();
  const current = new Element("button");
  current.setAttribute("data-focus-key", "workflow:issue:42");
  root.append(current);
  globalThis.document = {activeElement: current};

  preserveFocus(root, () => {
    const replacement = new Element("button");
    replacement.setAttribute("data-focus-key", "workflow:issue:42");
    root.replaceChildren(replacement);
  });

  assert.equal(document.activeElement, root.children[0]);
  globalThis.document = originalDocument;
});

test("detail transitions preserve the explicit external focus target", () => {
  const external = new Focusable();
  const unrelated = new Focusable();
  const closeButton = new Focusable();
  const dialog = new Dialog();
  const title = {textContent: ""};
  const content = {
    children: [],
    replaceChildren(...children) {
      this.children.forEach((child) => {
        child.connected = false;
      });
      this.children = children;
    },
    append(child) {
      this.children.push(child);
    },
  };
  globalThis.document = {
    activeElement: unrelated,
    contains: (node) => node.connected,
  };
  const controller = createDetailPanel(dialog, title, content, closeButton);

  controller.open({
    heading: "Toutes les actions",
    body: {nodeType: 1, connected: true},
    returnFocusTo: external,
  });
  const internalAction = new Focusable();
  document.activeElement = internalAction;
  controller.open({heading: "Détail", body: {nodeType: 1, connected: true}});
  controller.close();

  assert.equal(external.focusCalls, 1);
  assert.equal(unrelated.focusCalls, 0);
  assert.equal(internalAction.focusCalls, 0);
  delete globalThis.document;
});

test("closing detail restores focus to a polling replacement with the same stable key", () => {
  const original = new Focusable();
  original.setAttribute("data-focus-key", "workflow:issue:42");
  const replacement = new Focusable();
  replacement.setAttribute("data-focus-key", "workflow:issue:42");
  const unrelated = new Focusable();
  unrelated.setAttribute("data-focus-key", "workflow:issue:99");
  const closeButton = new Focusable();
  const dialog = new Dialog();
  const title = {textContent: ""};
  const content = new Element();
  globalThis.document = {
    activeElement: original,
    contains: (node) => node.connected,
    querySelectorAll(selector) {
      assert.equal(selector, "[data-focus-key]");
      return [replacement, unrelated];
    },
  };
  const controller = createDetailPanel(dialog, title, content, closeButton);
  controller.open({
    body: {nodeType: 1, connected: true},
    returnFocusTo: original,
  });

  original.connected = false;
  controller.close();

  assert.equal(original.focusCalls, 0);
  assert.equal(replacement.focusCalls, 1);
  assert.equal(unrelated.focusCalls, 0);
  delete globalThis.document;
});

test("closing detail does not move focus when a detached trigger has no replacement", () => {
  const original = new Focusable();
  original.setAttribute("data-focus-key", "workflow:issue:42");
  const unrelated = new Focusable();
  const closeButton = new Focusable();
  const dialog = new Dialog();
  const title = {textContent: ""};
  const content = new Element();
  globalThis.document = {
    activeElement: original,
    contains: (node) => node.connected,
    querySelectorAll: () => [unrelated],
  };
  const controller = createDetailPanel(dialog, title, content, closeButton);
  controller.open({
    body: {nodeType: 1, connected: true},
    returnFocusTo: original,
  });

  original.connected = false;
  controller.close();

  assert.equal(original.focusCalls, 0);
  assert.equal(unrelated.focusCalls, 0);
  delete globalThis.document;
});

test("refresh keeps human actions, authenticated preprod, and GitLab independent", async () => {
  const appSource = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  const refreshSource = appSource.split("async function refresh({")[1].split("elements.refreshButton.addEventListener")[0];
  const humanActions = appSource.split("async function refreshHumanActions()")[1].split("async function refreshPreprod()")[0];
  const preprod = appSource.split("async function refreshPreprod()")[1].split("async function refreshGitLab(")[0];
  const gitlab = appSource.split("async function refreshGitLab(")[1].split("function sourceStatus(")[0];

  assert.match(refreshSource, /await Promise\.all\(\[\s*refreshLocal\(\),\s*refreshHumanActions\(\),\s*refreshPreprod\(\),\s*skipGitLab \? Promise\.resolve\(\) : refreshGitLab\(\{manual\}\),/);
  assert.match(humanActions, /sourceStore\.load\("decisions", \(\) => api\.get\("\/api\/decisions"\)\)/);
  assert.match(humanActions, /sourceStore\.load\("proposals", \(\) => api\.get\("\/api\/proposals"\)\)/);
  assert.match(preprod, /api\.get\("\/api\/preprod-review", \{\s*headers: \{"X-Pitcrew-Session": sessionToken\}/);
  assert.match(gitlab, /now - lastGitLabRefresh < GITLAB_REFRESH_MS/);
  assert.match(gitlab, /sourceStore\.load\(\s*"gitlab"/);
});
