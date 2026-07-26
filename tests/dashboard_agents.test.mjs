import test from "node:test";
import assert from "node:assert/strict";

import {renderAgents} from "../dashboard/agents.mjs";

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.disabled = false;
    this._textContent = "";
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    this.children = [];
  }

  get textContent() {
    return [
      this._textContent,
      ...this.children.map((child) => child.textContent ?? String(child)),
    ].join(" ");
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this._textContent = "";
    this.children = [...children];
  }

  addEventListener() {}
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
};

function element(tagName = "div") {
  return new FakeElement(tagName);
}

function normalizedText(node) {
  return node.textContent.replace(/\s+/gu, " ").trim();
}

function findByClass(node, className) {
  if (node.className.split(/\s+/u).includes(className)) return node;
  for (const child of node.children) {
    const match = findByClass(child, className);
    if (match) return match;
  }
  return null;
}

const handlers = {
  control() {},
  controlDisabled: () => false,
  modelControl() {
    const control = element();
    control.textContent = "Sélecteur de modèle";
    return control;
  },
};

test("renderAgents shows an empty state when no agent is enabled", () => {
  const root = element();

  renderAgents(root, element(), element(), {agents: []}, handlers);

  assert.equal(root.children.length, 1);
  assert.equal(root.children[0].className, "empty-state");
  assert.equal(normalizedText(root), "Aucun agent activé n’est disponible.");
});

test("renderAgents preserves health, summaries, detailed usage, and disabled models", () => {
  const root = element();
  const disabledRoot = element();
  const disabledCount = element();
  const usage = {
    measured_runs: 2,
    unmeasured_runs: 1,
    tokens: {
      input_tokens: "1000",
      cached_input_tokens: "200",
      cache_write_tokens: "30",
      output_tokens: "40",
      total_tokens: "1270",
    },
    estimated_cost_usd: "0.123456",
  };

  renderAgents(root, disabledRoot, disabledCount, {
    agents: [{
      skill: "qa-run",
      role_description: "Rejoue les parcours.",
      health: "failed",
      loaded: true,
      running: false,
      configured_model: "gpt-5.6-terra",
      interval_seconds: 600,
      pid: 4242,
      estimated_next_pass: "2026-07-26T14:00:00Z",
      latest_history: {
        finished_at: "2026-07-26T13:00:00Z",
        summary: "Le smoke test paiement a échoué.",
      },
      latest_usage: usage,
      usage_7d: usage,
    }],
    disabled_roles: [{
      skill: "security-run",
      reason: "Dépôt non configuré",
      configured_model: "gpt-5.6-sol",
    }],
  }, handlers);

  const rowText = normalizedText(root);
  assert.match(rowText, /Santé Échec/u);
  assert.match(rowText, /État local Planifié/u);
  assert.match(rowText, /Résumé du dernier passage Le smoke test paiement a échoué\./u);
  assert.match(rowText, /Dernier passage.*Entrée 1 000.*Cache lu 200/u);
  assert.match(rowText, /Cache écrit 30.*Sortie 40.*Total 1 270/u);
  assert.match(rowText, /Coût estimé 0,123456 USD/u);
  assert.match(rowText, /Usage · 7 jours/u);
  assert.match(rowText, /Sous-total mesuré · 1 passage\(s\) non mesuré\(s\)/u);
  assert.ok(findByClass(root, "agent-usage-grid"));
  assert.equal(disabledCount.textContent, "1");
  assert.match(
    normalizedText(disabledRoot),
    /security-run · Dépôt non configuré · Modèle résolu : gpt-5\.6-sol/u,
  );
});
