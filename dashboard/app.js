import {createNavigation} from "./navigation.mjs";
import {createDetailPanel} from "./detail-panel.mjs";
import {renderActionList, renderItemDetail, renderPilotage} from "./pilotage.mjs";
import {renderAgents as renderAgentRows} from "./agents.mjs";
import {dateFormatter, formatCost, formatDate, formatTokens} from "./format.mjs";
import {historyPath, renderHistory, syncHistorySkills} from "./history.mjs";

const POLL_INTERVAL_MS = 10_000;
const GITLAB_REFRESH_MS = 60_000;
const ACTIONS = new Set(["trigger", "stop", "restart"]);

const navigation = createNavigation(document.querySelector("#app-navigation"), {
  pilotage: document.querySelector("#view-pilotage"),
  agents: document.querySelector("#view-agents"),
  history: document.querySelector("#view-history"),
});

const sessionToken = document.querySelector('meta[name="pitcrew-session"]')?.content ?? "";

const elements = {
  refreshButton: document.querySelector("#refresh-button"),
  refreshState: document.querySelector("#refresh-state"),
  globalStopButton: document.querySelector("#global-stop-button"),
  globalResumeButton: document.querySelector("#global-resume-button"),
  globalState: document.querySelector("#global-state"),
  operationalStatus: document.querySelector("#operational-status"),
  decisionBanner: document.querySelector("#decision-banner"),
  decisionContent: document.querySelector("#decision-content"),
  proposalState: document.querySelector("#proposal-state"),
  proposalList: document.querySelector("#proposal-list"),
  globalBanner: document.querySelector("#global-banner"),
  overviewUsageNote: document.querySelector("#overview-usage-note"),
  liveAgentGrid: document.querySelector("#live-agent-grid"),
  agentGrid: document.querySelector("#agent-grid"),
  disabledList: document.querySelector("#disabled-list"),
  disabledCount: document.querySelector("#disabled-count"),
  activityList: document.querySelector("#activity-list"),
  historyFilters: document.querySelector("#history-filters"),
  historySkill: document.querySelector("#history-skill"),
  historyOutcome: document.querySelector("#history-outcome"),
  gitlabGroups: document.querySelector("#gitlab-groups"),
  gitlabState: document.querySelector("#gitlab-state"),
  mergeRequestList: document.querySelector("#merge-request-list"),
  mergeRequestsState: document.querySelector("#merge-requests-state"),
  actionQueueCount: document.querySelector("#action-queue-count"),
  actionQueueList: document.querySelector("#action-queue-list"),
  actionQueueMore: document.querySelector("#action-queue-more"),
  workflowBoard: document.querySelector("#workflow-board-content"),
  workflowSearch: document.querySelector("#workflow-search"),
  workflowRole: document.querySelector("#workflow-role"),
  doneCount: document.querySelector("#done-count"),
  doneList: document.querySelector("#done-list"),
  crewHealth: document.querySelector("#crew-health"),
  detailPanel: document.querySelector("#detail-panel"),
  detailTitle: document.querySelector("#detail-title"),
  detailContent: document.querySelector("#detail-content"),
  detailClose: document.querySelector("#detail-close"),
  metrics: {
    active: document.querySelector("#metric-active"),
    stopped: document.querySelector("#metric-stopped"),
    running: document.querySelector("#metric-running"),
    warning: document.querySelector("#metric-warning"),
    failed: document.querySelector("#metric-failed"),
    tokens7d: document.querySelector("#metric-tokens-7d"),
    cost7d: document.querySelector("#metric-cost-7d"),
  },
};

const sources = {snapshot: {}, history: [], decisions: {}, proposals: {}, work: {}};
const detailController = createDetailPanel(
  elements.detailPanel,
  elements.detailTitle,
  elements.detailContent,
  elements.detailClose,
);
let currentActionQueue = [];
let lastRoleWork = null;

const healthLabels = {
  healthy: "Sain",
  warning: "Attention",
  failed: "Échec",
  stopped: "Arrêté",
  unknown: "Inconnu",
};

const lifecycleLabels = {
  todo: "À faire",
  processing: "En cours",
  review: "En revue",
  blocked: "Bloqué",
  done: "Terminé",
};

let refreshPromise = null;
let lastGitLabRefresh = 0;
const pendingSkills = new Set();
const pendingTicketActions = new Set();
let decisionSubmitting = false;
let proposalSubmitting = false;
let mergeSubmitting = false;

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Requête refusée (${response.status})`);
  }
  const payload = await response.json();
  if (path.startsWith("/api/gitlab")) {
    sources.work = payload;
  }
  return payload;
}

function setText(element, value) {
  if (element) {
    element.textContent = value == null ? "" : String(value);
  }
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function formatUsd(value) {
  return formatCost(value);
}

function usageMeasured(usage) {
  return finiteNumber(usage?.measured_runs) || 0;
}

function usageNote(usage) {
  const unmeasured = finiteNumber(usage?.unmeasured_runs) || 0;
  return unmeasured > 0 ? `Sous-total mesuré · ${unmeasured} passage(s) non mesuré(s)` : "";
}

function renderUsage(title, usage) {
  const section = document.createElement("section");
  section.className = "usage-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.append(heading);
  if (usageMeasured(usage) === 0) {
    const unavailable = document.createElement("p");
    unavailable.className = "usage-unavailable";
    unavailable.textContent = "Données indisponibles";
    section.append(unavailable);
    return section;
  }
  const grid = document.createElement("dl");
  grid.className = "usage-grid";
  [
    ["Entrée", usage?.tokens?.input_tokens],
    ["Cache lu", usage?.tokens?.cached_input_tokens],
    ["Cache écrit", usage?.tokens?.cache_write_tokens],
    ["Sortie", usage?.tokens?.output_tokens],
    ["Total", usage?.tokens?.total_tokens],
    ["Coût estimé", usage?.estimated_cost_usd, formatUsd],
  ].forEach(([label, value, formatter = formatTokens]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = formatter(value);
    grid.append(term, detail);
  });
  section.append(grid);
  const note = usageNote(usage);
  if (note) {
    const detail = document.createElement("p");
    detail.className = "usage-note";
    detail.textContent = note;
    section.append(detail);
  }
  return section;
}

function modelLabel(modelCatalog, model) {
  const entry = modelCatalog && typeof modelCatalog === "object" ? modelCatalog[model] : null;
  return typeof entry?.label === "string" && entry.label ? `${model} · ${entry.label}` : model || "Indisponible";
}

function createModelControl(agent, modelCatalog, globalStopped) {
  const wrapper = document.createElement("div");
  wrapper.className = "agent-model";
  const skill = typeof agent.skill === "string" ? agent.skill : "";
  const label = document.createElement("label");
  const selectId = `model-${skill}`;
  label.htmlFor = selectId;
  label.textContent = "Modèle configuré";
  const select = document.createElement("select");
  select.id = selectId;
  select.dataset.skill = skill;
  const catalog = modelCatalog && typeof modelCatalog === "object" ? modelCatalog : {};
  Object.entries(catalog).forEach(([slug, details]) => {
    if (typeof slug !== "string" || !slug || !details || typeof details !== "object") {
      return;
    }
    const option = document.createElement("option");
    option.value = slug;
    option.textContent = modelLabel(catalog, slug);
    select.append(option);
  });
  select.value = typeof agent.configured_model === "string" ? agent.configured_model : "";
  select.disabled = globalStopped || pendingSkills.has(skill) || !select.value;
  const previous = select.value;
  select.addEventListener("change", () => changeModel(skill, select.value, previous, select));
  const latest = document.createElement("p");
  latest.className = "latest-model";
  latest.textContent = `Dernier modèle : ${modelLabel(catalog, agent.latest_model)}`;
  wrapper.append(label, select, latest);
  return wrapper;
}

function makeBadge(health) {
  const badge = document.createElement("span");
  badge.className = `badge badge-${health || "unknown"}`;
  badge.textContent = healthLabels[health] || healthLabels.unknown;
  return badge;
}

function renderOverview(snapshot) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  const values = {
    active: agents.filter((agent) => agent.loaded).length,
    stopped: agents.filter((agent) => !agent.loaded).length,
    running: agents.filter((agent) => agent.running).length,
    warning: agents.filter((agent) => agent.health === "warning").length,
    failed: agents.filter((agent) => agent.health === "failed").length,
  };
  Object.entries(values).forEach(([key, value]) => setText(elements.metrics[key], value));
  const usage = snapshot?.usage_7d;
  if (usageMeasured(usage) === 0) {
    setText(elements.metrics.tokens7d, "—");
    setText(elements.metrics.cost7d, "—");
  } else {
    setText(elements.metrics.tokens7d, formatTokens(usage?.tokens?.total_tokens));
    setText(elements.metrics.cost7d, formatUsd(usage?.estimated_cost_usd));
  }
  setText(elements.overviewUsageNote, usageNote(usage));

  elements.globalBanner.className = "banner";
  const globalStopped = snapshot?.global_state === "stopped";
  elements.globalState?.classList.toggle("global-state-blocked", globalStopped);
  setText(elements.globalState, globalStopped ? "Exécutions bloquées" : "État global : en fonctionnement");
  if (elements.globalStopButton) {
    elements.globalStopButton.hidden = globalStopped;
  }
  if (elements.globalResumeButton) {
    elements.globalResumeButton.hidden = !globalStopped;
  }
  if (globalStopped) {
    elements.globalBanner.classList.add("banner-error");
    setText(elements.globalBanner, "Exécutions bloquées : aucun nouvel agent ne sera lancé.");
  } else if (values.failed > 0) {
    elements.globalBanner.classList.add("banner-error");
    setText(elements.globalBanner, `${values.failed} agent(s) nécessitent une intervention.`);
  } else if (values.warning > 0) {
    elements.globalBanner.classList.add("banner-warning");
    setText(elements.globalBanner, `${values.warning} agent(s) signalent un point d’attention.`);
  } else {
    elements.globalBanner.classList.add("banner-success");
    setText(elements.globalBanner, "Les automatisations locales ne signalent aucun incident.");
  }
}

function formatElapsed(value) {
  if (!value) {
    return "Durée indisponible";
  }
  const started = new Date(value).getTime();
  if (!Number.isFinite(started)) {
    return "Durée indisponible";
  }
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (seconds < 60) {
    return `${seconds} s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${seconds % 60} s`;
}

function renderLiveAgents(snapshot) {
  const agents = (Array.isArray(snapshot?.agents) ? snapshot.agents : [])
    .filter((agent) => agent.running)
    .sort((left, right) => String(left.skill).localeCompare(String(right.skill)));
  elements.liveAgentGrid.replaceChildren();
  if (agents.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Aucun agent ne travaille actuellement.";
    elements.liveAgentGrid.append(empty);
    return;
  }
  agents.forEach((agent) => {
    const status = agent.live_status;
    const card = document.createElement("article");
    card.className = "live-agent-card";
    const heading = document.createElement("div");
    heading.className = "agent-heading";
    const title = document.createElement("h3");
    title.textContent = agent.skill || "Agent sans nom";
    heading.append(title, makeBadge("healthy"));

    const phase = document.createElement("p");
    phase.className = "live-agent-phase";
    phase.textContent = status?.phase || "Exécution du passage courant · résumé disponible à la fin";

    const facts = document.createElement("dl");
    facts.className = "live-agent-facts";
    [
      ["Depuis", status?.started_at ? formatDate(status.started_at) : "Début non enregistré"],
      ["Durée", formatElapsed(status?.started_at)],
      ["Modèle", modelLabel(snapshot?.model_catalog, status?.model || agent.configured_model)],
      ["PID", status?.pid || agent.pid || "Indisponible"],
    ].forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      facts.append(term, detail);
    });
    card.append(heading, phase, facts);
    elements.liveAgentGrid.append(card);
  });
}

function syncSkillButtons(skill) {
  elements.agentGrid.querySelectorAll("[data-skill]").forEach((control) => {
    if (control.dataset.skill === skill) {
      control.disabled = pendingSkills.has(skill);
    }
  });
}

function renderAgents(snapshot) {
  renderAgentRows(
    elements.agentGrid,
    elements.disabledList,
    elements.disabledCount,
    snapshot,
    {
      control,
      controlDisabled: (skill) => (
        snapshot?.global_state === "stopped" || pendingSkills.has(skill)
      ),
      modelControl: (agent) => createModelControl(
        agent,
        snapshot?.model_catalog,
        snapshot?.global_state === "stopped",
      ),
    },
  );
}

function safeExternalLink(value, label) {
  if (typeof value !== "string") {
    return null;
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") {
    return null;
  }
  const link = document.createElement("a");
  link.href = url.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  return link;
}

function renderDecision(payload) {
  const pending = payload?.pending;
  if (!elements.decisionBanner || !elements.decisionContent) return;
  elements.decisionContent.replaceChildren();
  if (!pending || typeof pending !== "object") {
    elements.decisionBanner.hidden = true;
    return;
  }
  elements.decisionBanner.hidden = true;
  const ticket = document.createElement("p");
  ticket.className = "decision-ticket";
  const link = safeExternalLink(pending.ticket?.web_url, pending.ticket?.title || pending.ticket_id);
  if (link) ticket.append(link);
  else ticket.textContent = pending.ticket?.title || pending.ticket_id || "Ticket inconnu";
  elements.decisionContent.append(ticket);
  const question = document.createElement("p");
  question.className = "decision-question";
  question.textContent = pending.question || "Quelle action faut-il prendre ?";
  elements.decisionContent.append(question);
  const context = document.createElement("details");
  context.className = "decision-context";
  context.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Contexte et findings";
  context.append(summary);
  const body = document.createElement("div");
  body.className = "decision-context-body";
  if (pending.ticket?.description) {
    const description = document.createElement("p");
    description.textContent = pending.ticket.description;
    body.append(description);
  }
  if (pending.findings) {
    const findings = document.createElement("pre");
    findings.textContent = pending.findings;
    body.append(findings);
  }
  if (!body.childNodes.length) body.textContent = "Aucun contexte complémentaire disponible.";
  context.append(body);
  elements.decisionContent.append(context);
  const choices = document.createElement("div");
  choices.className = "decision-choices";
  (Array.isArray(pending.choices) ? pending.choices : []).forEach((answer) => {
    if (typeof answer !== "string" || !answer) return;
    const button = document.createElement("button");
    button.className = "button button-primary";
    button.type = "button";
    button.textContent = answer;
    button.disabled = decisionSubmitting;
    button.addEventListener("click", () => submitDecision(pending, answer));
    choices.append(button);
  });
  elements.decisionContent.append(choices);
}

function renderProposals(payload) {
  if (!elements.proposalList) return;
  elements.proposalList.replaceChildren();
  const proposals = Array.isArray(payload?.proposals) ? payload.proposals : [];
  setText(elements.proposalState, proposals.length ? `${proposals.length} en attente` : "Aucune proposition en attente");
  if (!proposals.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Aucune proposition ne nécessite une décision.";
    elements.proposalList.append(empty);
    return;
  }
  proposals.forEach((proposal) => {
    const card = document.createElement("article");
    card.className = "proposal-card";
    const heading = document.createElement("h3");
    heading.textContent = proposal.title || "Proposition sans titre";
    card.append(heading);
    const meta = document.createElement("p");
    meta.className = "proposal-meta";
    meta.textContent = `${proposal.category || "—"} · ${proposal.severity || "—"} · ${proposal.source || "—"}`;
    card.append(meta);
    const summary = document.createElement("p");
    summary.textContent = proposal.summary || "";
    card.append(summary);
    const details = document.createElement("details");
    const label = document.createElement("summary");
    label.textContent = "Voir les preuves et la recommandation";
    details.append(label);
    const body = document.createElement("div");
    body.className = "proposal-detail";
    const evidence = document.createElement("p");
    evidence.textContent = `Preuves : ${(Array.isArray(proposal.evidence) ? proposal.evidence : []).join(" · ")}`;
    const recommendation = document.createElement("p");
    recommendation.textContent = `Recommandation : ${proposal.recommendation || "—"}`;
    body.append(evidence, recommendation);
    details.append(body);
    card.append(details);
    const actions = document.createElement("div");
    actions.className = "proposal-actions";
    [["approve", "Approuver", "button-primary"], ["investigate", "Investiguer", "button-quiet"], ["reject", "Rejeter", "button-danger"]].forEach(([decision, text, style]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button ${style}`;
      button.textContent = text;
      button.disabled = proposalSubmitting;
      button.addEventListener("click", () => decideProposal(proposal, decision));
      actions.append(button);
    });
    card.append(actions);
    elements.proposalList.append(card);
  });
}

async function decideProposal(proposal, decision) {
  if (proposalSubmitting) return;
  const reason = decision === "reject" ? window.prompt("Pourquoi rejeter cette proposition ?", "") : "";
  if (decision === "reject" && (!reason || !reason.trim())) return;
  proposalSubmitting = true;
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pitcrew-Session": sessionToken },
      body: JSON.stringify({ action: "decide-proposal", proposal_id: proposal.id, decision, reason: reason || "" }),
    });
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, "Impossible d’enregistrer la décision sur la proposition.");
  } finally {
    proposalSubmitting = false;
  }
}

async function submitDecision(pending, answer) {
  if (decisionSubmitting) return;
  decisionSubmitting = true;
  renderDecision({ pending });
  setText(elements.operationalStatus, "Transmission de votre décision…");
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pitcrew-Session": sessionToken },
      body: JSON.stringify({ action: "answer-decision", ticket_id: pending.ticket_id, answer, notes: "" }),
    });
    setText(elements.operationalStatus, "Décision enregistrée ; unblock est lancé.");
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, "Impossible d’enregistrer la décision.");
  } finally {
    decisionSubmitting = false;
  }
}

function renderGitLab(work) {
  elements.gitlabGroups.replaceChildren();
  renderMergeRequests(work);
  if (work?.degraded) {
    setText(elements.gitlabState, "GitLab indisponible, données locales maintenues");
  } else {
    setText(elements.gitlabState, `Actualisé ${formatDate(work?.last_successful_refresh)}`);
  }
  const groups = work?.groups && typeof work.groups === "object" ? work.groups : {};
  Object.entries(lifecycleLabels).forEach(([state, label]) => {
    const column = document.createElement("article");
    column.className = "work-column";
    const heading = document.createElement("h3");
    const issues = Array.isArray(groups[state]) ? groups[state] : [];
    heading.textContent = `${label} · ${issues.length}`;
    column.append(heading);

    issues.forEach((issue) => {
      const card = document.createElement("div");
      card.className = "work-item";
      const issueLink = safeExternalLink(issue.web_url, issue.title || `Ticket #${issue.iid || "?"}`);
      if (issueLink) {
        card.append(issueLink);
      } else {
        const title = document.createElement("strong");
        title.textContent = issue.title || "Ticket sans titre";
        card.append(title);
      }
      const metadata = document.createElement("p");
      metadata.textContent = [issue.route, issue.source].filter(Boolean).join(" · ") || "Sans routage";
      card.append(metadata);

      const related = Array.isArray(issue.related_merge_requests) ? issue.related_merge_requests : [];
      related.forEach((url, index) => {
        const link = safeExternalLink(url, `MR liée ${index + 1}`);
        if (link) {
          card.append(link);
        }
      });
      const agentAction = issue.agent_action;
      if (agentAction && typeof agentAction === "object") {
        const actions = document.createElement("div");
        actions.className = "ticket-agent-actions";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button button-primary";
        button.textContent = agentAction.label || "Lancer l’agent";
        const target = typeof agentAction.target === "string" ? agentAction.target : "";
        const pending = pendingTicketActions.has(target);
        button.disabled = !agentAction.available || pending || !target;
        button.addEventListener("click", () => launchTicketAgent(issue));
        actions.append(button);
        if (!agentAction.available) {
          const unavailable = document.createElement("span");
          unavailable.className = "ticket-agent-unavailable";
          unavailable.textContent = agentAction.unavailable_reason || "Agent indisponible";
          actions.append(unavailable);
        }
        card.append(actions);
      }
      column.append(card);
    });
    elements.gitlabGroups.append(column);
  });
}

function renderTicketActionState(button, actions, target, agentAction) {
  const state = ticketActionStates.get(target);
  const pending = pendingTicketActions.has(target);
  button.disabled = !agentAction.available || pending || !target;
  button.textContent = pending ? "Lancement…" : agentAction.label || "Lancer l’agent";
  button.setAttribute("aria-busy", pending ? "true" : "false");
  let status = actions.querySelector(".ticket-agent-status");
  if (!state) {
    status?.remove();
    return;
  }
  if (!status) {
    status = document.createElement("span");
    status.className = "ticket-agent-status";
    actions.append(status);
  }
  status.className = `ticket-agent-status ticket-agent-status-${state.kind}`;
  status.textContent = state.text;
}

async function launchTicketAgent(issue, button, actions) {
  const agentAction = issue?.agent_action;
  const target = typeof agentAction?.target === "string" ? agentAction.target : "";
  const skill = typeof agentAction?.skill === "string" ? agentAction.skill : "";
  if (!target || !skill || pendingTicketActions.has(target) || !agentAction?.available) {
    return;
  }
  pendingTicketActions.add(target);
  setText(elements.operationalStatus, `Lancement de ${skill} pour le ticket en cours.`);
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pitcrew-Session": sessionToken },
      body: JSON.stringify({ action: "launch-ticket-agent", skill, target }),
    });
    setText(elements.operationalStatus, `${skill} lancé pour le ticket sélectionné.`);
    await refresh({ manual: true });
    return true;
  } catch {
    setText(elements.operationalStatus, `Impossible de lancer ${skill} pour ce ticket.`);
    return false;
  } finally {
    pendingTicketActions.delete(target);
  }
}

function renderMergeRequests(work) {
  if (!elements.mergeRequestList) return;
  elements.mergeRequestList.replaceChildren();
  const mergeRequests = Array.isArray(work?.merge_requests) ? work.merge_requests : [];
  setText(elements.mergeRequestsState, mergeRequests.length ? `${mergeRequests.length} ouverte(s)` : "Aucune MR ouverte");
  if (!mergeRequests.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = work?.degraded ? "Données GitLab indisponibles." : "Aucune merge request ouverte.";
    elements.mergeRequestList.append(empty);
    return;
  }
  mergeRequests.forEach((mergeRequest) => {
    const card = document.createElement("article");
    card.className = "merge-request-card";
    const title = document.createElement("h4");
    const link = safeExternalLink(mergeRequest.web_url, `${mergeRequest.title || "MR sans titre"} · !${mergeRequest.iid || "?"}`);
    if (link) title.append(link);
    else title.textContent = `${mergeRequest.title || "MR sans titre"} · !${mergeRequest.iid || "?"}`;
    card.append(title);

    const branches = document.createElement("p");
    branches.className = "merge-request-branches";
    branches.textContent = `${mergeRequest.source_branch || "Branche source inconnue"} → ${mergeRequest.target_branch || "Branche cible inconnue"}`;
    card.append(branches);

    const metadata = document.createElement("p");
    metadata.className = "merge-request-meta";
    metadata.textContent = [
      mergeRequest.author_username ? `Auteur : ${mergeRequest.author_username}` : "Auteur : inconnu",
      `Pipeline : ${mergeRequest.pipeline_status || "absent"}`,
    ].join(" · ");
    card.append(metadata);

    const actions = document.createElement("div");
    actions.className = "merge-request-actions";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-danger";
    button.textContent = "Fusionner et supprimer la branche";
    button.disabled = mergeSubmitting;
    button.addEventListener("click", () => mergeMergeRequest(mergeRequest, work));
    actions.append(button);
    card.append(actions);
    elements.mergeRequestList.append(card);
  });
}

async function mergeMergeRequest(mergeRequest, work) {
  if (mergeSubmitting || typeof mergeRequest?.iid !== "number") return;
  const sourceBranch = mergeRequest.source_branch || "la branche source";
  if (!window.confirm(`Fusionner !${mergeRequest.iid} dans ${mergeRequest.target_branch || "la branche cible"} et supprimer ${sourceBranch} ?`)) return;
  mergeSubmitting = true;
  renderMergeRequests(work);
  setText(elements.operationalStatus, `Fusion de la MR !${mergeRequest.iid} en cours.`);
  try {
    const result = await fetchJson("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pitcrew-Session": sessionToken },
      body: JSON.stringify({ action: "merge-merge-request", iid: mergeRequest.iid }),
    });
    setText(
      elements.operationalStatus,
      result?.partial
        ? `MR !${mergeRequest.iid} fusionnée ; suppression de ${sourceBranch} à vérifier.`
        : `MR !${mergeRequest.iid} fusionnée et branche ${sourceBranch} supprimée.`,
    );
    mergeSubmitting = false;
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, `Impossible de fusionner la MR !${mergeRequest.iid}.`);
    mergeSubmitting = false;
    renderMergeRequests(work);
  }
}

function workflowFilters() {
  return {
    query: elements.workflowSearch?.value || "",
    role: elements.workflowRole?.value || "",
  };
}

function syncWorkflowRoles(work) {
  if (!elements.workflowRole) return;
  const selected = elements.workflowRole.value;
  const groups = work?.groups && typeof work.groups === "object" ? work.groups : {};
  const roles = new Set();
  Object.values(groups).forEach((issues) => {
    (Array.isArray(issues) ? issues : []).forEach((issue) => {
      const role = issue?.agent_action?.skill || issue?.route;
      if (typeof role === "string" && role) {
        roles.add(role);
      }
    });
  });
  if (selected) {
    roles.add(selected);
  }
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Tous les rôles";
  const options = [...roles].sort((left, right) => left.localeCompare(right, "fr"))
    .map((role) => {
      const option = document.createElement("option");
      option.value = role;
      option.textContent = role;
      return option;
    });
  elements.workflowRole.replaceChildren(defaultOption, ...options);
  elements.workflowRole.value = selected;
}

function actionForEntry(entry) {
  const resource = entry?.resource && typeof entry.resource === "object"
    ? entry.resource
    : entry;
  if (!resource || typeof resource !== "object") return [];

  if (entry?.kind === "decision") {
    return (Array.isArray(resource.choices) ? resource.choices : [])
      .filter((answer) => typeof answer === "string" && answer)
      .map((answer) => ({
        label: answer,
        disabled: decisionSubmitting,
        run: () => submitDecision(resource, answer),
      }));
  }
  if (entry?.kind === "proposal") {
    return [
      ["approve", "Approuver", "button button-primary"],
      ["investigate", "Investiguer", "button button-quiet"],
      ["reject", "Rejeter", "button button-danger"],
    ].map(([decision, label, className]) => ({
      label,
      className,
      disabled: proposalSubmitting,
      run: () => decideProposal(resource, decision),
    }));
  }
  if (entry?.kind === "merge-request") {
    return [{
      label: "Fusionner et supprimer la branche",
      className: "button button-danger",
      disabled: mergeSubmitting,
      run: () => mergeMergeRequest(resource, sources.work),
    }];
  }
  if (entry?.kind === "agent-failure") {
    return [{
      label: "Voir les agents",
      run: () => {
        detailController.close();
        navigation.show("agents", {historyMode: "push"});
      },
    }];
  }
  const agentAction = resource.agent_action;
  const target = typeof agentAction?.target === "string" ? agentAction.target : "";
  if (
    entry?.kind === "issue"
    && agentAction?.available
    && target
  ) {
    return [{
      label: agentAction.label || "Lancer l’agent",
      disabled: pendingTicketActions.has(target),
      singleUse: true,
      successLabel: "Lancement accepté",
      run: (button, actions) => launchTicketAgent(resource, button, actions),
    }];
  }
  return [];
}

function openItem(entry) {
  if (!entry) return;
  detailController.open(renderItemDetail(entry, {forEntry: actionForEntry}));
}

function openAllActions(queue = currentActionQueue, returnFocusTo = null) {
  const body = document.createElement("div");
  body.className = "all-actions";
  renderActionList(body, queue, openItem);
  detailController.open({
    heading: `Toutes les actions (${queue.length})`,
    body,
    returnFocusTo,
  });
}

function renderPilotageView() {
  if (sources.work !== lastRoleWork) {
    syncWorkflowRoles(sources.work);
    lastRoleWork = sources.work;
  }
  const rendered = renderPilotage(
    {
      actionQueueCount: elements.actionQueueCount,
      actionQueueList: elements.actionQueueList,
      actionQueueMore: elements.actionQueueMore,
      workflowBoard: elements.workflowBoard,
      doneCount: elements.doneCount,
      doneList: elements.doneList,
      crewHealth: elements.crewHealth,
    },
    sources,
    {
      filters: workflowFilters,
      openItem,
      openAllActions,
    },
  );
  currentActionQueue = rendered.queue;
}

async function control(action, skill) {
  if (pendingSkills.has(skill)) {
    return;
  }
  if (!ACTIONS.has(action) || typeof skill !== "string" || !skill) {
    return;
  }
  if (action === "stop" && !window.confirm(`Arrêter ${skill} et son passage courant ?`)) {
    return;
  }
  pendingSkills.add(skill);
  syncSkillButtons(skill);
  setText(elements.operationalStatus, `Action ${action} en cours pour ${skill}.`);
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify({ action, skill }),
    });
    setText(elements.operationalStatus, `Action ${action} acceptée pour ${skill}.`);
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, `Impossible d’exécuter l’action pour ${skill}.`);
  } finally {
    pendingSkills.delete(skill);
    syncSkillButtons(skill);
  }
}

async function globalControl(action) {
  const isStop = action === "stop-all";
  const message = isStop
    ? "Arrêter tous les agents et bloquer les futures exécutions ?"
    : "Réactiver les agents et les exécutions planifiées ?";
  if (!window.confirm(message)) {
    return;
  }
  if (elements.globalStopButton) elements.globalStopButton.disabled = true;
  if (elements.globalResumeButton) elements.globalResumeButton.disabled = true;
  setText(elements.operationalStatus, isStop ? "Arrêt global en cours." : "Réactivation des agents en cours.");
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify({ action }),
    });
    setText(elements.operationalStatus, isStop ? "Exécutions bloquées." : "Agents réactivés.");
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, "Impossible de modifier l’état global.");
  } finally {
    if (elements.globalStopButton) elements.globalStopButton.disabled = false;
    if (elements.globalResumeButton) elements.globalResumeButton.disabled = false;
  }
}

function restoreModelSelect(select, previous) {
  if (select && typeof previous === "string") {
    select.value = previous;
  }
}

async function changeModel(skill, model, previous, select) {
  if (pendingSkills.has(skill) || !skill || !model || model === previous) {
    return;
  }
  if (!window.confirm(`Le passage courant sera interrompu puis relancé immédiatement pour appliquer ${model}.`)) {
    restoreModelSelect(select, previous);
    void refresh({ manual: true });
    return;
  }
  pendingSkills.add(skill);
  syncSkillButtons(skill);
  setText(elements.operationalStatus, `Changement de modèle en cours pour ${skill}.`);
  try {
    await fetchJson("/api/actions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify({ action: "change-model", skill, model }),
    });
    setText(elements.operationalStatus, `Changement de modèle accepté pour ${skill}.`);
  } catch {
    restoreModelSelect(select, previous);
    setText(elements.operationalStatus, `Impossible de changer le modèle pour ${skill}.`);
  } finally {
    pendingSkills.delete(skill);
    syncSkillButtons(skill);
    await refreshFresh({ manual: true, skipGitLab: true });
  }
}

async function refreshHistory() {
  const history = await fetchJson(historyPath(
    elements.historySkill.value,
    elements.historyOutcome.value,
  ));
  sources.history = history;
  renderHistory(
    elements.activityList,
    history,
    sources.snapshot?.model_catalog,
  );
}

async function refreshFresh(options = {}) {
  if (refreshPromise) {
    await refreshPromise;
  }
  return refresh(options);
}

async function refresh({ manual = false, skipGitLab = false } = {}) {
  if (refreshPromise) {
    return refreshPromise;
  }
  refreshPromise = (async () => {
    elements.agentGrid.setAttribute("aria-busy", "true");
    elements.refreshButton.disabled = true;
    setText(elements.refreshState, "Actualisation en cours…");
    try {
      const [snapshot, history, decisions, proposals] = await Promise.all([
        fetchJson("/api/status").then((payload) => {
          sources.snapshot = payload;
          syncHistorySkills(elements.historySkill, payload?.agents);
          return payload;
        }),
        fetchJson(historyPath(
          elements.historySkill.value,
          elements.historyOutcome.value,
        )).then((payload) => {
          sources.history = payload;
          return payload;
        }),
        fetchJson("/api/decisions").then((payload) => {
          sources.decisions = payload;
          return payload;
        }),
        fetchJson("/api/proposals").then((payload) => {
          sources.proposals = payload;
          return payload;
        }),
      ]);
      renderOverview(snapshot);
      renderLiveAgents(snapshot);
      renderAgents(snapshot);
      renderHistory(elements.activityList, history, snapshot?.model_catalog);
      renderDecision(decisions);
      renderProposals(proposals);
      renderPilotageView();

      const now = Date.now();
      if (!skipGitLab && (manual || now - lastGitLabRefresh >= GITLAB_REFRESH_MS)) {
        lastGitLabRefresh = now;
        try {
          const gitlabPath = manual ? "/api/gitlab?refresh=1" : "/api/gitlab";
          renderGitLab(await fetchJson(gitlabPath));
        } catch {
          renderGitLab({ degraded: true, groups: {} });
        }
      }
      renderPilotageView();
      setText(elements.refreshState, `Actualisé à ${dateFormatter.format(new Date())}`);
      setText(elements.operationalStatus, "Tableau de bord actualisé.");
    } catch {
      elements.globalBanner.className = "banner banner-error";
      setText(elements.globalBanner, "Le service local ne répond pas. Nouvelle tentative automatique.");
      setText(elements.refreshState, "Actualisation impossible");
      setText(elements.operationalStatus, "Échec de l’actualisation du tableau de bord.");
    } finally {
      elements.agentGrid.setAttribute("aria-busy", "false");
      elements.refreshButton.disabled = false;
    }
  })();
  try {
    await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

elements.refreshButton.addEventListener("click", () => refresh({ manual: true }));
elements.globalStopButton?.addEventListener("click", () => globalControl("stop-all"));
elements.globalResumeButton?.addEventListener("click", () => globalControl("resume-all"));
elements.workflowSearch?.addEventListener("input", renderPilotageView);
elements.workflowRole?.addEventListener("change", renderPilotageView);
elements.historyFilters.addEventListener("submit", (event) => {
  event.preventDefault();
  refreshHistory();
});

refresh({ manual: true });
window.setInterval(() => refresh(), POLL_INTERVAL_MS);
