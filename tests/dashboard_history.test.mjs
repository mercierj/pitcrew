import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  createLatestRequestCoordinator,
  historyPath,
  renderHistory,
  syncHistorySkills,
} from "../dashboard/history.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return {promise, resolve, reject};
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.className = "";
    this.dateTime = "";
    this.textContent = "";
    this.value = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  set innerHTML(_value) {
    throw new Error("innerHTML must not be used");
  }
}

globalThis.document = {
  createElement(tagName) {
    return new FakeElement(tagName);
  },
};

test("historyPath keeps role and outcome filters in URLSearchParams", () => {
  assert.equal(historyPath("", ""), "/api/history");
  assert.equal(
    historyPath("qa run/équipe", "failed"),
    "/api/history?skill=qa+run%2F%C3%A9quipe&outcome=failed",
  );
});

test("renderHistory builds safe text nodes with known model usage", () => {
  const root = new FakeElement("ol");
  renderHistory(root, [{
    skill: "qa-run",
    outcome: "success",
    finished_at: "2026-07-26T08:00:00Z",
    summary: "<img src=x onerror=alert(1)>",
    model: "gpt-safe",
    usage: {
      input_tokens: 100,
      cached_input_tokens: 10,
      cache_write_tokens: 5,
      output_tokens: 20,
      total_tokens: 135,
    },
  }], {
    "gpt-safe": {label: "Modèle sûr"},
  });

  assert.equal(root.children.length, 1);
  const [top, date, summary, metadata] = root.children[0].children;
  assert.equal(top.children[0].textContent, "qa-run");
  assert.equal(top.children[1].textContent, "success");
  assert.equal(date.dateTime, "2026-07-26T08:00:00Z");
  assert.equal(summary.textContent, "<img src=x onerror=alert(1)>");
  assert.match(metadata.textContent, /^Modèle : gpt-safe · Modèle sûr · Total : 135 jetons$/);
});

test("renderHistory falls back when model or usage metadata is unsafe", () => {
  const root = new FakeElement("ol");
  renderHistory(root, [{
    skill: "research-run",
    outcome: "failed",
    model: "unknown-model",
    usage: {total_tokens: 42},
  }], {});

  assert.equal(
    root.children[0].children.at(-1).textContent,
    "Modèle/usage indisponibles",
  );
});

test("syncHistorySkills keeps a valid selection after a snapshot", () => {
  const select = new FakeElement("select");
  select.value = "qa-run";

  syncHistorySkills(select, [
    {skill: "research-run"},
    {skill: "qa-run"},
    {skill: "qa-run"},
    {skill: ""},
  ]);

  assert.deepEqual(
    select.children.map((option) => option.value),
    ["", "qa-run", "research-run"],
  );
  assert.equal(select.value, "qa-run");
});

test("latest request coordinator ignores a response completed out of order", async () => {
  const coordinate = createLatestRequestCoordinator();
  const first = deferred();
  const second = deferred();

  const firstResult = coordinate(() => first.promise);
  const secondResult = coordinate(() => second.promise);
  second.resolve(["latest"]);
  assert.deepEqual(await secondResult, {applied: true, data: ["latest"]});

  first.resolve(["stale"]);
  assert.deepEqual(await firstResult, {applied: false, data: ["stale"]});
});

test("latest request coordinator returns the current rejection without throwing", async () => {
  const coordinate = createLatestRequestCoordinator();
  const error = new Error("history unavailable");

  const result = await coordinate(() => Promise.reject(error));

  assert.equal(result.applied, true);
  assert.equal(result.error, error);
  assert.equal("data" in result, false);
});

test("the app instantiates one shared history request coordinator before loading history", async () => {
  const appSource = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  const declaration = appSource.indexOf(
    "const coordinateHistoryRequest = createLatestRequestCoordinator();",
  );
  const load = appSource.indexOf("async function loadHistory()");

  assert.ok(declaration >= 0);
  assert.ok(load > declaration);
  assert.match(
    appSource.slice(load),
    /coordinateHistoryRequest\(\(\) => fetchJson\(path\)\)/,
  );
});
