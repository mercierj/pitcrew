import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {createDetailPanel} from "../dashboard/detail-panel.mjs";

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

test("refresh loads decisions with its initial data and renders pilotage before GitLab", async () => {
  const appSource = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  const refreshSource = appSource.split("async function refresh({")[1].split("elements.refreshButton.addEventListener")[0];
  const initialLoad = refreshSource.split("const now = Date.now();")[0];

  assert.match(initialLoad, /const \[snapshot, history, decisions, proposals\] = await Promise\.all\(/);
  assert.match(initialLoad, /fetchJson\("\/api\/decisions"\)\.then\(\(payload\) => \{\s*sources\.decisions = payload;/);
  assert.ok(initialLoad.indexOf("renderDecision(decisions);") < refreshSource.indexOf("renderGitLab(await fetchJson(gitlabPath));"));
  assert.ok(initialLoad.indexOf("renderPilotageView();") < refreshSource.indexOf("renderGitLab(await fetchJson(gitlabPath));"));
});
