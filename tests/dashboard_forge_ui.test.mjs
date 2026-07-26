import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";

const [html, javascript, styles] = await Promise.all([
  readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8"),
]);

test("dashboard keeps forge data in the Workflow kanban only", () => {
  for (const id of ["forge-work", "forge-groups", "change-list", "changes-title"]) {
    assert.doesNotMatch(html, new RegExp(`id="${id}"`));
  }
  assert.doesNotMatch(html, /Travail GitHub · GitLab/);
  assert.match(javascript, /const forgePath = "\/api\/forge-work"/);
  assert.match(javascript, /sources\.work = state\.data/);
  assert.match(javascript, /renderPilotageView\(\);/);
});

test("dashboard keeps normalized forge work feeding the Workflow kanban", () => {
  for (const token of [
    "fetchJson(forgePath)",
    "renderPilotageView",
    "sources.work = state.data",
    "latestForgeWork = state.data",
  ]) {
    assert.match(javascript, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  for (const legacy of ["renderGitLab", "gitlabGroups", "mergeRequestList"]) {
    assert.doesNotMatch(javascript, new RegExp(legacy));
  }
  assert.match(javascript, /const forgePath = "\/api\/forge-work"/);
});

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
