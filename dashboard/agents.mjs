import {formatCost, formatDate, formatTokens} from "./format.mjs";

const healthLabels = {
  healthy: "Sain",
  warning: "Attention",
  failed: "Échec",
  stopped: "Arrêté",
  unknown: "Inconnu",
};

function fact(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "agent-fact";
  const term = document.createElement("span");
  term.textContent = label;
  const detail = document.createElement("strong");
  detail.textContent = value;
  wrapper.append(term, detail);
  return wrapper;
}

function actionButton(label, action, skill, handlers, destructive = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = destructive ? "button button-danger" : "button button-quiet";
  button.textContent = label;
  button.dataset.skill = skill;
  button.disabled = handlers.controlDisabled?.(skill) === true;
  button.addEventListener("click", () => handlers.control(action, skill));
  return button;
}

function usageMeasured(usage) {
  const value = usage?.measured_runs;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function usageNote(usage) {
  const value = usage?.unmeasured_runs;
  const unmeasured = typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
  return unmeasured > 0
    ? `Sous-total mesuré · ${unmeasured} passage(s) non mesuré(s)`
    : "";
}

function usageSection(title, usage) {
  const section = document.createElement("section");
  section.className = "agent-usage";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  if (usageMeasured(usage) === 0) {
    const unavailable = document.createElement("p");
    unavailable.textContent = "Données indisponibles";
    section.append(unavailable);
  } else {
    const grid = document.createElement("div");
    grid.className = "agent-usage-grid";
    grid.append(
      fact("Entrée", formatTokens(usage?.tokens?.input_tokens)),
      fact("Cache lu", formatTokens(usage?.tokens?.cached_input_tokens)),
      fact("Cache écrit", formatTokens(usage?.tokens?.cache_write_tokens)),
      fact("Sortie", formatTokens(usage?.tokens?.output_tokens)),
      fact("Total", formatTokens(usage?.tokens?.total_tokens)),
      fact("Coût estimé", formatCost(usage?.estimated_cost_usd)),
    );
    section.append(grid);
  }
  const note = usageNote(usage);
  if (note) {
    const detail = document.createElement("p");
    detail.className = "agent-usage-note";
    detail.textContent = note;
    section.append(detail);
  }
  return section;
}

function latestRunSummary(summary) {
  const normalized = typeof summary === "string"
    ? summary.replace(/\s+/gu, " ").trim()
    : "";
  if (!normalized) return "Aucun compte rendu récent.";
  return normalized.length > 160 ? `${normalized.slice(0, 157).trimEnd()}…` : normalized;
}

function agentRow(agent, handlers) {
  const row = document.createElement("article");
  row.className = "agent-row";
  const header = document.createElement("div");
  header.className = "agent-row-main";
  const identity = document.createElement("div");
  identity.className = "agent-identity";
  const title = document.createElement("h2");
  title.textContent = agent.skill || "Agent sans nom";
  const description = document.createElement("p");
  description.textContent = agent.role_description || "Rôle non documenté.";
  const compactSummary = document.createElement("p");
  compactSummary.className = "agent-compact-summary";
  compactSummary.textContent = `Dernier passage · ${latestRunSummary(agent.latest_history?.summary)}`;
  identity.append(
    title,
    description,
    compactSummary,
    fact("Santé", healthLabels[agent.health] || healthLabels.unknown),
  );
  header.append(
    identity,
    fact("État local", agent.running ? "En cours" : agent.loaded ? "Planifié" : "Arrêté"),
    fact("Dernier passage", formatDate(agent.latest_history?.finished_at)),
    fact("Prochain passage", formatDate(agent.estimated_next_pass)),
  );

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Voir les détails techniques et les contrôles";
  const detailGrid = document.createElement("div");
  detailGrid.className = "agent-detail-grid";
  detailGrid.append(
    fact("Modèle", agent.configured_model || "Indisponible"),
    fact("Jetons", formatTokens(agent.usage_7d?.tokens?.total_tokens)),
    fact("Coût estimé", formatCost(agent.usage_7d?.estimated_cost_usd)),
    fact("Fréquence", agent.interval_seconds ? `${agent.interval_seconds} s` : "Indisponible"),
    fact("PID", agent.pid || "Indisponible"),
  );
  const latestSummary = document.createElement("p");
  latestSummary.className = "agent-last-summary";
  const latestSummaryLabel = document.createElement("strong");
  latestSummaryLabel.textContent = "Résumé du dernier passage";
  const latestSummaryText = document.createElement("span");
  latestSummaryText.textContent = agent.latest_history?.summary || "Aucun compte rendu récent.";
  latestSummary.append(latestSummaryLabel, latestSummaryText);
  const controls = document.createElement("div");
  controls.className = "agent-controls";
  controls.append(
    actionButton("Déclencher", "trigger", agent.skill, handlers),
    actionButton("Réinstaller", "restart", agent.skill, handlers),
    actionButton("Arrêter", "stop", agent.skill, handlers, true),
  );
  details.append(
    summary,
    detailGrid,
    handlers.modelControl(agent),
    latestSummary,
    usageSection("Dernier passage", agent.latest_usage),
    usageSection("Usage · 7 jours", agent.usage_7d),
    controls,
  );
  row.append(header, details);
  return row;
}

export function renderAgents(root, disabledRoot, disabledCount, snapshot, handlers) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  const rows = agents.map((agent) => agentRow(agent, handlers));
  if (rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Aucun agent activé n’est disponible.";
    rows.push(empty);
  }
  root.replaceChildren(...rows);
  const disabled = Array.isArray(snapshot?.disabled_roles) ? snapshot.disabled_roles : [];
  disabledCount.textContent = String(disabled.length);
  disabledRoot.replaceChildren(...disabled.map((role) => {
    const line = document.createElement("p");
    line.textContent = [
      role.skill || "Rôle inconnu",
      role.reason || "Non configuré",
      `Modèle résolu : ${role.configured_model || "Indisponible"}`,
    ].join(" · ");
    return line;
  }));
}
