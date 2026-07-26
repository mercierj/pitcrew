import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {createDetailPanel} from "../dashboard/detail-panel.mjs";
import {
  preserveFocus,
  renderActionFeedback,
  renderItemDetail,
} from "../dashboard/pilotage.mjs";

class Focusable extends EventTarget {
  constructor() {
    super();
    this.connected = true;
    this.focusCalls = 0;
  }

  focus() {
    document.activeElement = this;
    this.focusCalls += 1;
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
