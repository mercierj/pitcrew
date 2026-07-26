import test from "node:test";
import assert from "node:assert/strict";

import {
  formatCost,
  formatDate,
  formatTokens,
} from "../dashboard/format.mjs";

test("formatTokens preserves exact large integer strings", () => {
  assert.equal(
    formatTokens("1000000000000000000000000"),
    "1\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000",
  );
  assert.equal(formatTokens(Number.MAX_SAFE_INTEGER + 1), "Données indisponibles");
});

test("formatCost preserves decimal text without floating-point conversion", () => {
  assert.equal(formatCost("7"), "7,0000 USD");
  assert.equal(formatCost("0.1"), "0,1000 USD");
  assert.equal(
    formatCost("1000000000000000000000000.123456789"),
    "1\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000,123456 USD",
  );
  assert.equal(formatCost(0.1), "Données indisponibles");
});

test("formatDate retains unavailable-value fallbacks", () => {
  assert.equal(formatDate(null), "Aucune exécution");
  assert.equal(formatDate("not-a-date"), "Date indisponible");
});
