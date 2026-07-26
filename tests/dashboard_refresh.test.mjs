import assert from "node:assert/strict";
import test from "node:test";

import {refreshAfterPending} from "../dashboard/refresh.mjs";

test("waits for an in-flight refresh before starting a fresh read", async () => {
  let finishPending;
  const pending = new Promise((resolve) => {
    finishPending = resolve;
  });
  const calls = [];

  const result = refreshAfterPending(pending, async (options) => {
    calls.push(options);
    return {fix_autonomy: "on"};
  }, {manual: true});

  assert.deepEqual(calls, []);
  finishPending();
  assert.deepEqual(await result, {fix_autonomy: "on"});
  assert.deepEqual(calls, [{manual: true}]);
});
