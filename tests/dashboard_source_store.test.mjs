import test from "node:test";
import assert from "node:assert/strict";

import {
  createSourceStore,
  createStableRenderGuard,
} from "../dashboard/source-store.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return {promise, resolve, reject};
}

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
  assert.equal(failed.error, "unavailable");
});

test("the latest request wins for the same source name", async () => {
  let now = 1_000;
  const store = createSourceStore(() => now);
  const first = deferred();
  const second = deferred();

  const firstLoad = store.load("history", () => first.promise);
  now = 2_000;
  const secondLoad = store.load("history", () => second.promise);

  second.resolve(["latest"]);
  assert.deepEqual(await secondLoad, {
    data: ["latest"],
    stale: false,
    lastSuccess: 2_000,
    error: null,
  });

  now = 3_000;
  first.resolve(["older"]);
  assert.deepEqual(await firstLoad, {
    data: ["latest"],
    stale: false,
    lastSuccess: 2_000,
    error: null,
  });
  assert.deepEqual(store.get("history").data, ["latest"]);
});

test("request ordering is independent for each source name", async () => {
  const store = createSourceStore(() => 1_000);
  const oldHistory = deferred();
  const currentHistory = deferred();
  const snapshot = deferred();

  const oldHistoryLoad = store.load("history", () => oldHistory.promise);
  const snapshotLoad = store.load("snapshot", () => snapshot.promise);
  const currentHistoryLoad = store.load("history", () => currentHistory.promise);

  snapshot.resolve({agents: []});
  currentHistory.resolve(["current"]);
  oldHistory.reject(new Error("late failure"));

  assert.deepEqual((await snapshotLoad).data, {agents: []});
  assert.deepEqual((await currentHistoryLoad).data, ["current"]);
  assert.deepEqual((await oldHistoryLoad).data, ["current"]);
  assert.equal(store.get("history").error, null);
});

test("a degraded payload keeps the last healthy payload as stale", async () => {
  let now = 1_000;
  const store = createSourceStore(() => now);
  await store.load("gitlab", async () => ({degraded: false, groups: {todo: [1]}}));

  now = 2_000;
  const degraded = await store.load(
    "gitlab",
    async () => ({degraded: true, groups: {}}),
    {
      validate(payload) {
        if (payload?.degraded === true) throw new Error("GitLab indisponible");
      },
    },
  );

  assert.deepEqual(degraded.data, {degraded: false, groups: {todo: [1]}});
  assert.equal(degraded.stale, true);
  assert.equal(degraded.lastSuccess, 1_000);
  assert.equal(degraded.error, "GitLab indisponible");
});

test("stable render guard avoids a second DOM replacement for equal state", () => {
  const guardedRender = createStableRenderGuard();
  const root = {
    replacements: [],
    replaceChildren(value) {
      this.replacements.push(value);
    },
  };
  const render = (value) => root.replaceChildren(value.groups.todo[0]);

  assert.equal(guardedRender({groups: {todo: [1]}}, render), true);
  assert.equal(guardedRender({groups: {todo: [1]}}, render), false);
  assert.equal(guardedRender({groups: {todo: [2]}}, render), true);
  assert.deepEqual(root.replacements, [1, 2]);
});
