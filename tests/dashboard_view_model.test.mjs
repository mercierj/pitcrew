import test from "node:test";
import assert from "node:assert/strict";

import { buildActionQueue, buildWorkflow, resourceKey } from "../dashboard/view-model.mjs";

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

test("resourceKey uses the resource type and canonical URL", () => {
  assert.equal(
    resourceKey(issue("todo", 1)),
    "issue:https://gitlab.example/group/app/-/issues/1",
  );
  assert.equal(
    resourceKey({ resource_type: "issue", web_url: "https://gitlab.example/group/app/-/issues/2" }),
    "issue:https://gitlab.example/group/app/-/issues/2",
  );
  assert.equal(resourceKey({ resource_type: "issue" }), "");
});

test("buildWorkflow orders active lifecycle keys and only keeps today's done issues", () => {
  const workflow = buildWorkflow(
    {
      issues: [
        issue("review", 3),
        issue("closed", 1, { closed_at: "2026-07-26T08:00:00Z" }),
        issue("todo", 2),
        issue("blocked", 4),
        issue("processing", 5),
        issue("closed", 6, { closed_at: "2026-07-25T08:00:00Z" }),
        issue("closed", 7, { closed_at: undefined, updated_at: "2026-07-25T22:30:00Z" }),
        issue("closed", 8, { closed_at: undefined, updated_at: "2026-07-25T20:30:00Z" }),
      ],
    },
    { doneDay: "2026-07-26" },
  );

  assert.deepEqual(Object.keys(workflow), ["todo", "processing", "review", "blocked", "done"]);
  assert.deepEqual(workflow.todo.map(({ lifecycle }) => lifecycle), ["todo"]);
  assert.deepEqual(workflow.processing.map(({ lifecycle }) => lifecycle), ["processing"]);
  assert.deepEqual(workflow.review.map(({ lifecycle }) => lifecycle), ["review"]);
  assert.deepEqual(workflow.blocked.map(({ lifecycle }) => lifecycle), ["blocked"]);
  assert.deepEqual(workflow.done.map(({ iid }) => iid), [1, 7]);
  assert.equal(workflow.todo[0].key, "issue:https://gitlab.example/group/app/-/issues/2");
  assert.equal(workflow.todo[0].kind, "issue");
  assert.equal(workflow.todo[0].lifecycle, "todo");
});

test("buildWorkflow filters active cards by text and agent role", () => {
  const workflow = buildWorkflow(
    {
      issues: [
        issue("todo", 1, { title: "Payments need review", agent_action: { skill: "implementer-run" } }),
        issue("todo", 2, { title: "Payments without matching role", agent_action: { route: "qa-run" } }),
        issue("review", 3, { title: "Invoices", route: "implementer-run" }),
      ],
    },
    { query: "payments", role: "implementer-run", doneDay: "2026-07-26" },
  );

  assert.deepEqual(workflow.todo.map(({ iid }) => iid), [1]);
  assert.deepEqual(workflow.processing, []);
  assert.deepEqual(workflow.review, []);
  assert.deepEqual(workflow.blocked, []);

  const routeWorkflow = buildWorkflow(
    { issues: [issue("review", 3, { title: "Invoices", route: "implementer-run" })] },
    { query: "invoices", role: "implementer-run", doneDay: "2026-07-26" },
  );
  assert.deepEqual(routeWorkflow.review.map(({ iid }) => iid), [3]);
});

test("buildWorkflow applies text and role filters to done cards", () => {
  const workflow = buildWorkflow(
    { issues: [
      issue("done", 1, { title: "Payments", route: "implementer-run", closed_at: "2026-07-26T08:00:00Z" }),
      issue("done", 2, { title: "Payments", route: "qa-run", closed_at: "2026-07-26T08:00:00Z" }),
      issue("done", 3, { title: "Invoices", route: "implementer-run", closed_at: "2026-07-26T08:00:00Z" }),
    ] },
    { doneDay: "2026-07-26", query: "payments", role: "implementer-run" },
  );

  assert.deepEqual(workflow.done.map(({ iid }) => iid), [1]);
});

test("buildWorkflow treats whitespace query as no filter", () => {
  const workflow = buildWorkflow(
    { issues: [issue("todo", 1, { title: "Payments" })] },
    { query: "   ", doneDay: "2026-07-26" },
  );

  assert.deepEqual(workflow.todo.map(({ iid }) => iid), [1]);
});

test("buildActionQueue orders action kinds by priority and timestamps", () => {
  const queue = buildActionQueue({
    decisions: { decisions: [
      { ticket_id: "2", created_at: "2026-07-26T11:00:00Z" },
      { ticket_id: "1", created_at: "2026-07-26T09:00:00Z" },
    ] },
    snapshot: { agents: [
      { skill: "zeta-run", health: "warning", updated_at: "2026-07-26T12:00:00Z", latest_history: { finished_at: "2026-07-26T10:00:00Z" } },
      { skill: "alpha-run", health: "failed", updated_at: "2026-07-26T08:00:00Z", latest_history: { finished_at: "2026-07-26T11:00:00Z" } },
    ] },
    work: { merge_requests: [issue("opened", 3, {
      resource_type: "merge_request",
      canonical_url: "https://gitlab.example/group/app/-/merge_requests/3",
      target_branch: "develop",
      updated_at: "2026-07-26T13:00:00Z",
    })] },
    proposals: { proposals: [{ id: "proposal-1", created_at: "2026-07-26T14:00:00Z" }] },
  });

  assert.deepEqual(queue.map(({ kind }) => kind), [
    "decision", "decision", "agent-failure", "agent-failure", "merge-request", "proposal",
  ]);
  assert.deepEqual(queue.slice(0, 2).map(({ key }) => key), ["decision:1", "decision:2"]);
  assert.deepEqual(queue.slice(2, 4).map(({ key }) => key), ["agent:zeta-run", "agent:alpha-run"]);
  assert.deepEqual(queue.map(({ label }) => label), ["Répondre", "Répondre", "Diagnostiquer", "Diagnostiquer", "Fusionner", "Examiner"]);
});

test("buildActionQueue skips invalid keys and deduplicates sorted entries", () => {
  const queue = buildActionQueue({
    decisions: { decisions: [
      { created_at: "2026-07-26T08:00:00Z" },
      { ticket_id: "duplicate", created_at: "2026-07-26T10:00:00Z" },
      { ticket_id: "duplicate", created_at: "2026-07-26T09:00:00Z" },
    ] },
    snapshot: { agents: [{ health: "warning", latest_history: { finished_at: "2026-07-26T08:00:00Z" } }] },
    work: { merge_requests: [{ resource_type: "merge_request", updated_at: "2026-07-26T08:00:00Z" }] },
    proposals: { proposals: [{ created_at: "2026-07-26T08:00:00Z" }] },
  });

  assert.deepEqual(queue.map(({ key }) => key), ["decision:duplicate"]);
  assert.equal(queue[0].timestamp, "2026-07-26T09:00:00Z");
});

test("buildActionQueue excludes merge requests targeting protected deployment branches", () => {
  const queue = buildActionQueue({
    work: { merge_requests: [
      issue("opened", 1, { resource_type: "merge_request", target_branch: "prod" }),
      issue("opened", 2, { resource_type: "merge_request", target_branch: "preprod" }),
    ] },
  });

  assert.deepEqual(queue, []);
});
