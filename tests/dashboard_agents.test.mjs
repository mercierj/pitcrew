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
    this.attributes = new Map();
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

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if (selector === "[data-detail-key]" && node.getAttribute?.("data-detail-key")) {
        matches.push(node);
      }
      node.children?.forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }
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

test("renderAgents presents non-scheduled roles as on-demand", () => {
  const root = element();
  const onDemandRoot = element();

  renderAgents(root, onDemandRoot, element(), {
    agents: [],
    disabled_roles: [{
      skill: "qa-run",
      reason: "qa.test_flow_repo is not configured",
    }],
  }, handlers);

  assert.match(
    normalizedText(onDemandRoot),
    /qa-run · À la demande · Prérequis : qa\.test_flow_repo is not configured/u,
  );
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
    /security-run · À la demande · Prérequis : Dépôt non configuré · Modèle résolu : gpt-5\.6-sol/u,
  );
});

test("renderAgents surfaces a bounded latest-run summary in every compact row", () => {
  const root = element();
  const longSummary = `Compte rendu ${"utile ".repeat(80)}`;

  renderAgents(root, element(), element(), {
    agents: [{
      skill: "qa-run",
      latest_history: {summary: longSummary},
    }, {
      skill: "ops-run",
      latest_history: {},
    }],
  }, handlers);

  const rows = root.children;
  for (const row of rows) {
    const compactSummary = findByClass(row, "agent-compact-summary");
    assert.ok(compactSummary, "each compact row exposes its latest-run summary");
    assert.ok(
      normalizedText(compactSummary).length <= 200,
      "the compact summary remains scannable",
    );
  }
  assert.match(normalizedText(rows[0]), /Compte rendu utile/u);
  assert.match(normalizedText(rows[1]), /Aucun compte rendu récent./u);
});

test("renderAgents identifies event-driven roles without cadence or schedule controls", () => {
  const root = element();

  renderAgents(root, element(), element(), {
    agents: [{
      skill: "implementer-run",
      trigger_mode: "event",
      interval_seconds: null,
      restartable: false,
    }],
    disabled_roles: [],
  }, handlers);

  const rowText = normalizedText(root);
  assert.match(rowText, /Déclenchement Événement ou ticket/u);
  assert.match(rowText, /État local Prêt sur ticket/u);
  assert.doesNotMatch(rowText, /Fréquence/u);
  assert.doesNotMatch(rowText, /Déclencher/u);
  assert.doesNotMatch(rowText, /Arrêter/u);
  assert.doesNotMatch(rowText, /Réinstaller/u);
  assert.doesNotMatch(rowText, /Changer le modèle/u);
});

test("renderAgents keeps an opened agent diagnostic open across polling", () => {
  const root = element();
  const snapshot = {agents: [{skill: "qa-run"}], disabled_roles: []};

  renderAgents(root, element(), element(), snapshot, handlers);
  const diagnostic = root.querySelectorAll("[data-detail-key]")[0];
  diagnostic.open = true;

  renderAgents(root, element(), element(), snapshot, handlers);

  assert.equal(root.querySelectorAll("[data-detail-key]")[0].open, true);
});
