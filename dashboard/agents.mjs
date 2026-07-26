import {formatCost, formatDate, formatTokens} from "./format.mjs";

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

function agentRow(agent, handlers) {
  const row = document.createElement("article");
  row.className = "agent-row";
  const header = document.createElement("div");
  header.className = "agent-row-main";
  const identity = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = agent.skill || "Agent sans nom";
  const description = document.createElement("p");
  description.textContent = agent.role_description || "Rôle non documenté.";
  identity.append(title, description);
  header.append(
    identity,
    fact("État", agent.running ? "En cours" : agent.loaded ? "Planifié" : "Arrêté"),
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
  const controls = document.createElement("div");
  controls.className = "agent-controls";
  controls.append(
    actionButton("Déclencher", "trigger", agent.skill, handlers),
    actionButton("Réinstaller", "restart", agent.skill, handlers),
    actionButton("Arrêter", "stop", agent.skill, handlers, true),
  );
  details.append(summary, detailGrid, handlers.modelControl(agent), controls);
  row.append(header, details);
  return row;
}

export function renderAgents(root, disabledRoot, disabledCount, snapshot, handlers) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  root.replaceChildren(...agents.map((agent) => agentRow(agent, handlers)));
  const disabled = Array.isArray(snapshot?.disabled_roles) ? snapshot.disabled_roles : [];
  disabledCount.textContent = String(disabled.length);
  disabledRoot.replaceChildren(...disabled.map((role) => {
    const line = document.createElement("p");
    line.textContent = `${role.skill || "Rôle inconnu"} · ${role.reason || "Non configuré"}`;
    return line;
  }));
}
