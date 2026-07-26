import test from "node:test";
import assert from "node:assert/strict";

import {createApi} from "../dashboard/api.mjs";

function jsonResponse(payload, {ok = true, status = 200} = {}) {
  return {
    ok,
    status,
    async json() {
      return payload;
    },
  };
}

test("get centralizes no-store same-origin JSON reads", async () => {
  const calls = [];
  const api = createApi("session-token", async (...args) => {
    calls.push(args);
    return jsonResponse({agents: []});
  });

  assert.deepEqual(await api.get("/api/status"), {agents: []});
  assert.deepEqual(calls, [[
    "/api/status",
    {cache: "no-store", credentials: "same-origin"},
  ]]);
});

test("get accepts options and preserves authenticated GET headers", async () => {
  const calls = [];
  const api = createApi("session-token", async (...args) => {
    calls.push(args);
    return jsonResponse({running: false});
  });

  await api.get("/api/preprod-review", {
    headers: {"X-Pitcrew-Session": "session-token"},
  });

  assert.deepEqual(calls[0], [
    "/api/preprod-review",
    {
      cache: "no-store",
      credentials: "same-origin",
      headers: {"X-Pitcrew-Session": "session-token"},
    },
  ]);
});

test("action sends the exact payload with session authentication in headers", async () => {
  const calls = [];
  const api = createApi("session-token", async (...args) => {
    calls.push(args);
    return jsonResponse({accepted: true});
  });
  const payload = {
    action: "launch-ticket-agent",
    skill: "implementer-run",
    target: "group/app#42",
  };

  assert.deepEqual(await api.action(payload), {accepted: true});
  assert.deepEqual(calls[0], [
    "/api/actions",
    {
      cache: "no-store",
      credentials: "same-origin",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": "session-token",
      },
      body: JSON.stringify(payload),
    },
  ]);
});

test("request failures expose the HTTP status", async () => {
  const api = createApi("", async () => jsonResponse({}, {ok: false, status: 403}));

  await assert.rejects(
    () => api.get("/api/status"),
    /Requête refusée \(403\)/,
  );
});
