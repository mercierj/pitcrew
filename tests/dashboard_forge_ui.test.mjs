import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";

const [html, javascript, styles] = await Promise.all([
  readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8"),
]);

test("dashboard exposes provider-neutral forge work regions", () => {
  for (const id of ["forge-work", "forge-groups", "change-list", "changes-title"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const legacy of ["gitlab-work", "gitlab-groups", "merge-request-list"]) {
    assert.doesNotMatch(html, new RegExp(`id="${legacy}"`));
  }
  assert.match(html, /Travail GitHub · GitLab/);
});

test("dashboard renders normalized issues, changes, evidence and durable run state", () => {
  for (const token of [
    "fetchJson(forgePath)",
    "renderForgeWork",
    "related_change_urls",
    "issue.bugfix.reproduction",
    "issue.bugfix.verification",
    "issue.bugfix.blocked_reason",
    "change.reference",
    "change.kind",
    "agentAction.run_state",
  ]) {
    assert.match(javascript, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  for (const legacy of ["renderGitLab", "gitlabGroups", "mergeRequestList"]) {
    assert.doesNotMatch(javascript, new RegExp(legacy));
  }
  assert.match(javascript, /const forgePath = "\/api\/forge-work"/);
});

test("provider-neutral changes retain GitLab manual merge only as a capability", () => {
  assert.match(javascript, /change\.kind === "merge_request"/);
  assert.match(javascript, /work\.provider === "gitlab"/);
  assert.match(javascript, /action: "merge-merge-request"/);
  for (const selector of [
    ".changes-panel",
    ".change-list",
    ".change-card",
    ".change-branches",
    ".change-meta",
    ".change-actions",
  ]) {
    assert.match(styles, new RegExp(selector.replace(".", "\\.")));
  }
  assert.doesNotMatch(styles, /\.merge-request-(?:list|card|branches|meta|actions)/);
});
