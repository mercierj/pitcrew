import {createNavigation} from "./navigation.mjs";
import {createDetailPanel} from "./detail-panel.mjs";
import {
  renderActionFeedback,
  renderActionList,
  renderItemDetail,
  renderPilotage,
} from "./pilotage.mjs";
import {createApi} from "./api.mjs";
import {
  createSourceStore,
  createStableRenderGuard,
} from "./source-store.mjs";
import {renderAgents as renderAgentRows} from "./agents.mjs";
import {dateFormatter, formatCost, formatDate, formatTokens} from "./format.mjs";
import {
  createLatestRequestCoordinator,
  historyPath,
  renderHistory,
  syncHistorySkills,
} from "./history.mjs";

const ACTIVE_POLL_INTERVAL_MS = 2_000;
const IDLE_POLL_INTERVAL_MS = 10_000;
const GITLAB_REFRESH_MS = 60_000;
const ACTIONS = new Set(["trigger", "stop", "restart"]);

const navigation = createNavigation(document.querySelector("#app-navigation"), {
  pilotage: document.querySelector("#view-pilotage"),
  agents: document.querySelector("#view-agents"),
  history: document.querySelector("#view-history"),
});

const sessionToken = document.querySelector('meta[name="pitcrew-session"]')?.content ?? "";
const api = createApi(sessionToken);

const elements = {
  refreshButton: document.querySelector("#refresh-button"),
  refreshState: document.querySelector("#refresh-state"),
  globalStopButton: document.querySelector("#global-stop-button"),
  globalResumeButton: document.querySelector("#global-resume-button"),
  globalState: document.querySelector("#global-state"),
  globalActionStatus: document.querySelector("#global-action-status"),
  operationalStatus: document.querySelector("#operational-status"),
  pilotageSourceState: document.querySelector("#pilotage-source-state"),
  agentsSourceState: document.querySelector("#agents-source-state"),
  historySourceState: document.querySelector("#history-source-state"),
  decisionBanner: document.querySelector("#decision-banner"),
  decisionContent: document.querySelector("#decision-content"),
  proposalState: document.querySelector("#proposal-state"),
  proposalList: document.querySelector("#proposal-list"),
  preprodReviewState: document.querySelector("#preprod-review-state"),
  preprodReviewReport: document.querySelector("#preprod-review-report"),
  preprodReviewTrigger: document.querySelector("#preprod-review-trigger"),
  preprodReviewStop: document.querySelector("#preprod-review-stop"),
  preprodReviewActionStatus: document.querySelector("#preprod-review-action-status"),
  preprodReviewHistory: document.querySelector("#preprod-review-history-list"),
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
  detailActionStatus: document.querySelector("#detail-action-status"),
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

const sources = {snapshot: {}, history: [], decisions: {}, proposals: {}, preprod: {}, work: {}};
const sourceStore = createSourceStore();
const renderGitLabWhenChanged = createStableRenderGuard();
const coordinateHistoryRequest = createLatestRequestCoordinator();
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
let latestGitLabWork = null;
let latestRuns = {runs: [], capacity: {}, has_active: false};
let refreshTimer = null;
let detailTicketView = null;
const pendingSkills = new Set();
const pendingTicketActions = new Set();
const actionStates = new Map();
let decisionSubmitting = false;
let proposalSubmitting = false;
let mergeSubmitting = false;
let preprodReviewSubmitting = false;

const fetchJson = (path, options = {}) => api.get(path, options);

function setText(element, value) {
  if (element) {
    element.textContent = value == null ? "" : String(value);
  }
}

function setActionState(key, kind, message) {
  const state = {kind, message, text: message};
  actionStates.set(key, state);
  renderActionFeedback(elements.detailActionStatus, state);
  renderResourceActionState(key);
}

function renderResourceActionState(key) {
  const targets = {
    "global:crew": elements.globalActionStatus,
    "preprod:review": elements.preprodReviewActionStatus,
  };
  renderActionFeedback(targets[key], actionStates.get(key));
}

async function runAction(key, messages, operation) {
  setActionState(key, "pending", messages.pending);
  try {
    const result = await operation();
    setActionState(key, "success", messages.success);
    return result;
  } catch (error) {
    setActionState(key, "error", messages.error);
    throw error;
  }
}

function issueActionKey(issue) {
  const identity = issue?.canonical_url
    || issue?.web_url
    || issue?.agent_action?.target
    || "unknown";
  return `issue:${identity}`;
}

function appendActionState(root, key) {
  const state = actionStates.get(key);
  if (!root || !state) return;
  const status = document.createElement("p");
  status.className = `action-state action-state-${state.kind}`;
  status.textContent = state.message;
  root.append(status);
}

function preprodText(value, fallback = "Donnée indisponible") {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 800) : fallback;
}

function preprodTextList(value, fallback = "Donnée indisponible") {
  const values = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
  const safe = values.map((item) => preprodText(item, "")).filter(Boolean);
  return safe.length ? safe.join(", ") : fallback;
}

function preprodCount(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? String(value) : "—";
}

function preprodSha(value) {
  return typeof value === "string" && /^[0-9a-f]{7,64}$/i.test(value) ? value.slice(0, 12) : "—";
}

function preprodVerdict(verdict) {
  const labels = {
    ready: ["PRÊT", "preprod-verdict-ready"],
    changes_required: ["CORRECTIONS REQUISES", "preprod-verdict-changes"],
    incomplete: ["INCOMPLET", "preprod-verdict-incomplete"],
  };
  return labels[verdict] || ["INCOMPLET", "preprod-verdict-incomplete"];
}

function preprodLine(label, value) {
  const row = document.createElement("p");
  const strong = document.createElement("strong");
  strong.textContent = `${label} : `;
  row.append(strong, document.createTextNode(value));
  return row;
}

function renderPreprodReview(review) {
  const value = review && typeof review === "object" ? review : {};
  const unavailable = value.unavailable === true;
  const running = value.running === true;
  const stopped = value.global_state === "stopped";
  const latest = value.latest && typeof value.latest === "object" ? value.latest : null;
  const stale = value.report_stale === true;
  const [verdictLabel, verdictClass] = preprodVerdict(latest?.verdict);
  const state = unavailable ? "Revue locale indisponible" : running ? "Revue en cours" : stopped ? "Arrêt global" : latest ? `Dernier verdict : ${verdictLabel}` : "Aucun rapport disponible";
  setText(elements.preprodReviewState, state);
  if (elements.preprodReviewTrigger) elements.preprodReviewTrigger.disabled = unavailable || running || stopped || preprodReviewSubmitting;
  if (elements.preprodReviewStop) {
    elements.preprodReviewStop.hidden = !running;
    elements.preprodReviewStop.disabled = preprodReviewSubmitting;
  }
  renderResourceActionState("preprod:review");

  const reportRoot = elements.preprodReviewReport;
  if (!reportRoot) return;
  const content = [];
  if (stale) {
    const warning = document.createElement("p");
    warning.className = "preprod-stale";
    warning.textContent = "Dernier passage interrompu ou échoué : le rapport précédent est obsolète";
    content.push(warning);
  }
  if (unavailable) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "La revue locale est indisponible. Actualisez le tableau de bord pour réessayer.";
    content.push(empty);
  } else if (!latest) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = running ? "La revue est en cours. Le rapport sera disponible à la fin du passage." : "Aucun rapport de revue avant Preprod n’est disponible.";
    content.push(empty);
  } else {
    const heading = document.createElement("h3");
    heading.textContent = stale ? "Rapport précédent" : "Dernier rapport";
    const badge = document.createElement("p");
    badge.className = `preprod-verdict ${verdictClass}`;
    badge.textContent = verdictLabel;
    content.push(heading, badge);
    const scope = `${preprodText(latest.base_ref, preprodText(value.base_ref))}...${preprodText(latest.compare_ref, preprodText(value.compare_ref))}`;
    content.push(
      preprodLine("Périmètre", scope),
      preprodLine("SHA", `${preprodSha(latest.base_sha)} → ${preprodSha(latest.compare_sha)}`),
      preprodLine("Terminé", formatDate(latest.completed_at)),
      preprodLine("Commits / fichiers", `${preprodCount(latest.commit_count)} / ${preprodCount(latest.changed_file_count)}`),
      preprodLine("Fichiers relus", preprodCount(Array.isArray(latest.reviewed_files) ? latest.reviewed_files.length : null)),
      preprodLine("Synthèse", preprodText(latest.synthesis)),
    );
    if (latest.failure_reason) content.push(preprodLine("Motif", preprodText(latest.failure_reason)));
    const findings = Array.isArray(latest.findings) ? latest.findings : [];
    const findingRoot = document.createElement("div");
    findingRoot.className = "preprod-findings";
    for (const severity of ["critical", "high", "medium", "low"]) {
      const items = findings.filter((item) => item && item.severity === severity);
      if (!items.length) continue;
      const group = document.createElement("section");
      const title = document.createElement("h4");
      title.textContent = severity;
      const list = document.createElement("ol");
      for (const finding of items) {
        const item = document.createElement("li");
        item.append(
          preprodLine("Titre", preprodText(finding.title)),
          preprodLine("Preuve", preprodTextList(finding.evidence)),
          preprodLine("Fichiers", preprodTextList(finding.affected_files)),
          preprodLine("Impact", preprodText(finding.impact)),
          preprodLine("Recommandation", preprodText(finding.recommendation)),
        );
        list.append(item);
      }
      group.append(title, list);
      findingRoot.append(group);
    }
    if (findingRoot.childElementCount) content.push(findingRoot);
  }
  reportRoot.replaceChildren(...content);
  const history = Array.isArray(value.history) ? value.history : [];
  const historyItems = history.slice(0, 10).map((report) => {
    const item = document.createElement("li");
    const [label, cssClass] = preprodVerdict(report?.verdict);
    item.className = `preprod-history-item ${cssClass}`;
    item.textContent = `${label} · ${preprodSha(report?.base_sha)} → ${preprodSha(report?.compare_sha)} · ${formatDate(report?.completed_at)}`;
    return item;
  });
  if (!historyItems.length) {
    const item = document.createElement("li");
    item.textContent = "Aucun passage archivé.";
    historyItems.push(item);
  }
  elements.preprodReviewHistory?.replaceChildren(...historyItems);
}

async function runPreprodReviewAction(action) {
  const trigger = action === "trigger-preprod-review";
  const message = trigger
    ? "Lancer cette revue longue et coûteuse avec Sol xhigh sur origin/preprod...origin/develop ?"
    : "Arrêter la revue avant Preprod en cours ?";
  if (preprodReviewSubmitting || !window.confirm(message)) return;
  preprodReviewSubmitting = true;
  renderPreprodReview(sources.preprod);
  try {
    await runAction(
      "preprod:review",
      {
        pending: trigger ? "Lancement de la revue avant Preprod…" : "Arrêt de la revue avant Preprod…",
        success: trigger ? "Revue avant Preprod lancée." : "Revue avant Preprod arrêtée.",
        error: "Impossible de contrôler la revue avant Preprod.",
      },
      () => api.action({action}),
    );
    setText(elements.operationalStatus, trigger ? "Revue avant Preprod lancée." : "Revue avant Preprod arrêtée.");
  } catch {
    setText(elements.operationalStatus, "Impossible de contrôler la revue avant Preprod.");
  } finally {
    preprodReviewSubmitting = false;
    await refreshFresh({ manual: true, skipGitLab: true });
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
  const reasoning = document.createElement("p");
  reasoning.className = "latest-model";
  const configuredEffort = typeof agent.configured_reasoning_effort === "string" && agent.configured_reasoning_effort
    ? agent.configured_reasoning_effort
    : "hérité";
  reasoning.textContent = `Raisonnement : ${configuredEffort}`;
  wrapper.append(label, select, reasoning, latest);
  appendActionState(wrapper, `model:${skill}`);
  appendActionState(wrapper, `agent:${skill}`);
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
  renderResourceActionState("global:crew");
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
  appendActionState(elements.decisionContent, `decision:${pending.ticket_id}`);
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
    appendActionState(card, `proposal:${proposal.id}`);
    elements.proposalList.append(card);
  });
}

async function decideProposal(proposal, decision) {
  if (proposalSubmitting) return;
  const reason = decision === "reject" ? window.prompt("Pourquoi rejeter cette proposition ?", "") : "";
  if (decision === "reject" && (!reason || !reason.trim())) return;
  proposalSubmitting = true;
  try {
    await runAction(
      `proposal:${proposal.id}`,
      {
        pending: `Décision en cours pour ${proposal.title || "la proposition"}…`,
        success: `Décision enregistrée pour ${proposal.title || "la proposition"}.`,
        error: "Impossible d’enregistrer la décision sur la proposition.",
      },
      () => api.action({
        action: "decide-proposal",
        proposal_id: proposal.id,
        decision,
        reason: reason || "",
      }),
    );
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
    await runAction(
      `decision:${pending.ticket_id}`,
      {
        pending: "Transmission de votre décision…",
        success: "Décision enregistrée ; unblock est lancé.",
        error: "Impossible d’enregistrer la décision.",
      },
      () => api.action({
        action: "answer-decision",
        ticket_id: pending.ticket_id,
        answer,
        notes: "",
      }),
    );
    setText(elements.operationalStatus, "Décision enregistrée ; unblock est lancé.");
    await refresh({ manual: true });
  } catch {
    setText(elements.operationalStatus, "Impossible d’enregistrer la décision.");
  } finally {
    decisionSubmitting = false;
  }
}

function runsByTarget(snapshot) {
  return new Map((Array.isArray(snapshot?.runs) ? snapshot.runs : [])
    .filter((run) => typeof run?.target === "string" && run.target)
    .map((run) => [run.target, run]));
}

function issueResource(entry) {
  return entry?.resource && typeof entry.resource === "object"
    ? entry.resource
    : entry;
}

function currentIssueForTarget(target) {
  const groups = latestGitLabWork?.groups;
  if (!groups || typeof groups !== "object") return null;
  return Object.values(groups)
    .flatMap((issues) => Array.isArray(issues) ? issues : [])
    .find((issue) => issue?.agent_action?.target === target || issue?.web_url === target)
    || null;
}

function syncDetailTicketAction() {
  const view = detailTicketView;
  if (
    !view
    || !elements.detailPanel?.open
    || !document.contains(view.button)
    || !document.contains(view.actions)
  ) {
    detailTicketView = null;
    return;
  }
  const issue = currentIssueForTarget(view.target) || view.issue;
  const run = runsByTarget(latestRuns).get(view.target) || issue?.active_run;
  view.issue = issue;
  renderTicketActionState(view.button, view.actions, issue, run);
}

function upsertRun(run) {
  const runs = (Array.isArray(latestRuns?.runs) ? latestRuns.runs : [])
    .filter((item) => item?.run_id !== run.run_id);
  runs.push(run);
  latestRuns = {
    ...latestRuns,
    runs,
    has_active: runs.some((item) => item?.state === "queued" || item?.state === "running"),
  };
}

function renderGitLab(work = latestGitLabWork, runs = latestRuns) {
  let shouldRender = false;
  renderGitLabWhenChanged({work, runs}, () => {
    shouldRender = true;
  });
  if (!shouldRender) return false;
  if (!work) return;
  elements.gitlabGroups.replaceChildren();
  renderMergeRequests(work);
  if (work?.degraded) {
    setText(elements.gitlabState, "GitLab indisponible, données locales maintenues");
  } else {
    setText(elements.gitlabState, `Actualisé ${formatDate(work?.last_successful_refresh)}`);
  }
  const groups = work?.groups && typeof work.groups === "object" ? work.groups : {};
  const targetRuns = runsByTarget(runs);
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
        const run = targetRuns.get(target) || issue.active_run;
        const pending = pendingTicketActions.has(target);
        button.disabled = !agentAction.available || pending || run?.state === "queued" || run?.state === "running" || !target;
        button.addEventListener("click", () => launchTicketAgent(issue, button, actions));
        actions.append(button);
        if (!agentAction.available) {
          const unavailable = document.createElement("span");
          unavailable.className = "ticket-agent-unavailable";
          unavailable.textContent = agentAction.unavailable_reason || "Agent indisponible";
          actions.append(unavailable);
        }
        renderTicketActionState(button, actions, issue, run);
        card.append(actions);
      }
      column.append(card);
    });
    elements.gitlabGroups.append(column);
  });
  syncDetailTicketAction();
  return true;
}

function renderTicketActionState(button, actions, issue, run) {
  const agentAction = issue?.agent_action;
  const target = typeof agentAction?.target === "string" ? agentAction.target : "";
  const pending = pendingTicketActions.has(target);
  const actionState = actionStates.get(issueActionKey(issue));
  button.disabled = !agentAction.available || pending || run?.state === "queued" || run?.state === "running" || !target;
  button.textContent = pending ? "Lancement…" : agentAction.label || "Lancer l’agent";
  button.setAttribute("aria-busy", pending || run?.state === "queued" || run?.state === "running" ? "true" : "false");
  let status = actions.querySelector(".ticket-run-status");
  if (!pending && !run && !actionState) {
    status?.remove();
    return;
  }
  if (!status) {
    status = document.createElement("span");
    status.className = "ticket-run-status";
    actions.append(status);
  }
  if (pending) {
    status.className = "ticket-run-status ticket-run-pending";
    status.textContent = "Lancement…";
  } else if (run?.state === "queued") {
    status.className = "ticket-run-status ticket-run-queued";
    status.textContent = `En attente · position ${run.queue_position}`;
  } else if (run?.state === "running") {
    const role = latestRuns.capacity?.[run.skill];
    status.className = "ticket-run-status ticket-run-running";
    status.textContent = `En cours · ${role?.running ?? 1}/${role?.max_concurrent ?? 3} places utilisées`;
  } else if (run?.state === "failed") {
    status.className = "ticket-run-status ticket-run-failed";
    status.textContent = "Échec · Relancer";
  } else if (run?.state === "cancelled") {
    status.className = "ticket-run-status ticket-run-cancelled";
    status.textContent = "Annulé · Relancer";
  } else if (actionState) {
    status.className = `ticket-run-status action-state-${actionState.kind}`;
    status.textContent = actionState.message;
  } else {
    status.remove();
  }
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
    const operation = runAction(
      issueActionKey(issue),
      {
        pending: "Lancement…",
        success: "Lancement accepté — l’agent travaille sur ce ticket.",
        error: "Échec du lancement — réessayez.",
      },
      () => api.post("/api/ticket-runs", {skill, target}),
    );
    renderTicketActionState(button, actions, issue, runsByTarget(latestRuns).get(target));
    const run = await operation;
    upsertRun({...run, skill, target});
    renderGitLab(latestGitLabWork, latestRuns);
    setText(elements.operationalStatus, `${skill} lancé pour le ticket sélectionné.`);
    return true;
  } catch {
    setText(elements.operationalStatus, `Impossible de lancer ${skill} pour ce ticket.`);
    return false;
  } finally {
    pendingTicketActions.delete(target);
    renderGitLab(latestGitLabWork, latestRuns);
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
    appendActionState(actions, `merge_request:${mergeRequest.canonical_url || mergeRequest.web_url || mergeRequest.iid}`);
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
    const result = await runAction(
      `merge_request:${mergeRequest.canonical_url || mergeRequest.web_url || mergeRequest.iid}`,
      {
        pending: `Fusion de !${mergeRequest.iid}…`,
        success: `MR !${mergeRequest.iid} fusionnée.`,
        error: `Impossible de fusionner la MR !${mergeRequest.iid}.`,
      },
      () => api.action({action: "merge-merge-request", iid: mergeRequest.iid}),
    );
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
        state: actionStates.get(`decision:${resource.ticket_id}`),
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
      state: actionStates.get(`proposal:${resource.id}`),
      run: () => decideProposal(resource, decision),
    }));
  }
  if (entry?.kind === "merge-request") {
    return [{
      label: "Fusionner et supprimer la branche",
      className: "button button-danger",
      disabled: mergeSubmitting,
      state: actionStates.get(`merge_request:${resource.canonical_url || resource.web_url || resource.iid}`),
      run: () => mergeMergeRequest(resource, sources.work),
    }];
  }
  if (entry?.kind === "agent-failure") {
    return [{
      label: "Voir les agents",
      state: actionStates.get(`agent:${resource.skill}`),
      run: () => {
        detailController.close();
        navigation.show("agents", {historyMode: "push"});
      },
    }];
  }
  const agentAction = resource.agent_action;
  const target = typeof agentAction?.target === "string" ? agentAction.target : "";
  const run = runsByTarget(latestRuns).get(target) || resource.active_run;
  if (
    entry?.kind === "issue"
    && target
    && (
      agentAction?.available
      || run?.state === "queued"
      || run?.state === "running"
      || run?.state === "failed"
    )
  ) {
    return [{
      label: agentAction.label || "Lancer l’agent",
      disabled: pendingTicketActions.has(target) || run?.state === "queued" || run?.state === "running",
      state: actionStates.get(issueActionKey(resource)),
      run: (button, actions) => launchTicketAgent(resource, button, actions),
    }];
  }
  return [];
}

function openItem(entry) {
  if (!entry) return;
  const detail = renderItemDetail(entry, {forEntry: actionForEntry});
  detailController.open(detail);
  const resource = issueResource(entry);
  const target = typeof resource?.agent_action?.target === "string"
    ? resource.agent_action.target
    : "";
  const actions = detail?.body?.querySelector?.(".detail-actions");
  const button = actions?.querySelector?.("button");
  if (entry?.kind === "issue" && target && button && actions) {
    detailTicketView = {actions, button, issue: resource, target};
    syncDetailTicketAction();
  } else {
    detailTicketView = null;
  }
}

function openAllActions(queue = currentActionQueue, returnFocusTo = null) {
  detailTicketView = null;
  const body = document.createElement("div");
  body.className = "all-actions";
  renderActionList(body, queue, openItem);
  detailController.open({
    heading: `Toutes les actions (${queue.length})`,
    body,
    returnFocusTo,
  });
}

elements.detailPanel?.addEventListener("close", () => {
  detailTicketView = null;
});

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
    await runAction(
      `agent:${skill}`,
      {
        pending: `Action ${action} en cours pour ${skill}.`,
        success: `Action ${action} acceptée pour ${skill}.`,
        error: `Impossible d’exécuter l’action pour ${skill}.`,
      },
      () => api.action({action, skill}),
    );
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
    await runAction(
      "global:crew",
      {
        pending: isStop ? "Arrêt global en cours." : "Réactivation des agents en cours.",
        success: isStop ? "Exécutions bloquées." : "Agents réactivés.",
        error: "Impossible de modifier l’état global.",
      },
      () => api.action({action}),
    );
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
    await runAction(
      `model:${skill}`,
      {
        pending: `Changement de modèle en cours pour ${skill}.`,
        success: `Changement de modèle accepté pour ${skill}.`,
        error: `Impossible de changer le modèle pour ${skill}.`,
      },
      () => api.action({action: "change-model", skill, model}),
    );
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

async function loadHistory() {
  const path = historyPath(
    elements.historySkill.value,
    elements.historyOutcome.value,
  );
  const result = await coordinateHistoryRequest(() => fetchJson(path));
  if (!result.applied) {
    return result;
  }
  const state = await sourceStore.load("history", async () => {
    if (result.error) throw result.error;
    return result.data;
  });
  if (result.applied && !result.error && state.data != null) {
    sources.history = state.data;
  }
  return {
    applied: true,
    data: state.data,
    error: state.error ? result.error ?? new Error(state.error) : undefined,
    state,
  };
}

function showHistoryError() {
  const message = "Impossible d’actualiser l’historique. Les données précédentes restent affichées.";
  elements.globalBanner.className = "banner banner-error";
  setText(elements.globalBanner, message);
  setText(elements.operationalStatus, message);
}

async function refreshHistory() {
  const result = await loadHistory();
  if (!result.applied) {
    return;
  }
  if (result.error) {
    showHistoryError();
    renderSourceStates();
    return;
  }
  renderHistory(
    elements.activityList,
    result.data,
    sources.snapshot?.model_catalog,
  );
  renderOverview(sources.snapshot);
  setText(elements.operationalStatus, "Historique actualisé.");
  renderSourceStates();
}

async function refreshFresh(options = {}) {
  if (refreshPromise) {
    await refreshPromise;
  }
  return refresh(options);
}

async function refreshLocal() {
  const [snapshot, history, runs] = await Promise.all([
    sourceStore.load("snapshot", () => api.get("/api/status")),
    loadHistory(),
    sourceStore.load(
      "runs",
      () => api.get("/api/runs", {
        headers: {"X-Pitcrew-Session": sessionToken},
      }),
    ),
  ]);

  if (snapshot.data) {
    sources.snapshot = snapshot.data;
    syncHistorySkills(elements.historySkill, snapshot.data?.agents);
    document.body.dataset.globalState = snapshot.data.global_state || "running";
    if (elements.globalStopButton) {
      elements.globalStopButton.hidden = snapshot.data.global_state === "stopped";
    }
    if (elements.globalResumeButton) {
      elements.globalResumeButton.hidden = snapshot.data.global_state !== "stopped";
    }
  }
  if (runs.data) latestRuns = runs.data;
  renderOverview(sources.snapshot);
  renderLiveAgents(sources.snapshot);
  renderAgents(sources.snapshot);
  if (history.applied && !history.error && history.data != null) {
    renderHistory(
      elements.activityList,
      history.data,
      sources.snapshot?.model_catalog,
    );
  }
  renderSourceStates();
}

async function refreshHumanActions() {
  const [decisions, proposals] = await Promise.all([
    sourceStore.load("decisions", () => api.get("/api/decisions")),
    sourceStore.load("proposals", () => api.get("/api/proposals")),
  ]);
  if (decisions.data) sources.decisions = decisions.data;
  if (proposals.data) sources.proposals = proposals.data;
  renderDecision(sources.decisions);
  renderProposals(sources.proposals);
  renderPilotageView();
  renderSourceStates();
}

async function refreshPreprod() {
  const state = await sourceStore.load(
    "preprod",
    () => api.get("/api/preprod-review", {
      headers: {"X-Pitcrew-Session": sessionToken},
    }),
  );
  if (state.data) {
    sources.preprod = state.data;
  } else if (state.error) {
    sources.preprod = {unavailable: true};
  }
  renderPreprodReview(sources.preprod);
  if (state.stale) {
    setText(
      elements.preprodReviewState,
      `Revue locale · Données anciennes · dernière réussite ${formatDate(state.lastSuccess)}`,
    );
  }
}

async function refreshGitLab({manual = false, force = false} = {}) {
  const now = Date.now();
  if (!force && !manual && now - lastGitLabRefresh < GITLAB_REFRESH_MS) {
    return sourceStore.get("gitlab");
  }
  const state = await sourceStore.load(
    "gitlab",
    () => api.get(manual || force ? "/api/gitlab?refresh=1" : "/api/gitlab"),
    {
      validate(payload) {
        if (payload?.degraded === true) throw new Error("GitLab indisponible");
      },
    },
  );
  if (state.data) {
    sources.work = state.data;
    latestGitLabWork = state.data;
    renderGitLab(state.data, latestRuns);
  }
  if (!state.error) lastGitLabRefresh = now;
  renderPilotageView();
  renderSourceStates();
  return state;
}

function sourceStatus(name, label, retry) {
  const state = sourceStore.get(name);
  if (!state.error) return null;
  const status = document.createElement("div");
  status.className = state.stale
    ? "source-state source-state-stale"
    : "source-state source-state-error";
  const message = document.createElement("p");
  message.textContent = state.stale
    ? `${label} · Données anciennes · dernière réussite ${formatDate(state.lastSuccess)}`
    : `${label} indisponible`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-quiet";
  button.textContent = "Réessayer";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await retry();
    } finally {
      renderSourceStates();
    }
  });
  status.append(message, button);
  return status;
}

function renderSourceState(root, states) {
  root?.replaceChildren(...states.filter(Boolean));
}

function renderSourceStates() {
  renderSourceState(elements.pilotageSourceState, [
    sourceStatus("decisions", "Décisions", refreshHumanActions),
    sourceStatus("proposals", "Propositions", refreshHumanActions),
    sourceStatus("gitlab", "GitLab", () => refreshGitLab({manual: true})),
    sourceStatus("runs", "Exécutions", refreshLocal),
  ]);
  renderSourceState(elements.agentsSourceState, [
    sourceStatus("snapshot", "État local", refreshLocal),
    sourceStatus("runs", "Exécutions", refreshLocal),
  ]);
  renderSourceState(elements.historySourceState, [
    sourceStatus("history", "Historique", refreshHistory),
  ]);
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
      const hadActiveRuns = latestRuns.has_active === true;
      await Promise.all([
        refreshLocal(),
        refreshHumanActions(),
        refreshPreprod(),
        skipGitLab ? Promise.resolve() : refreshGitLab({manual}),
      ]);

      const becameTerminal = hadActiveRuns && !latestRuns.has_active;
      if (becameTerminal) lastGitLabRefresh = 0;
      if (!skipGitLab && !manual && becameTerminal) {
        await refreshGitLab({force: true});
      }
      renderGitLab(latestGitLabWork, latestRuns);
      renderPilotageView();
      renderSourceStates();
      const hasErrors = [
        "snapshot",
        "history",
        "decisions",
        "proposals",
        "preprod",
        "runs",
        "gitlab",
      ].some((name) => sourceStore.get(name).error);
      const refreshedAt = dateFormatter.format(new Date());
      setText(
        elements.refreshState,
        hasErrors ? `Actualisé à ${refreshedAt} · données partielles` : `Actualisé à ${refreshedAt}`,
      );
      setText(
        elements.operationalStatus,
        hasErrors ? "Tableau de bord actualisé avec des sources indisponibles." : "Tableau de bord actualisé.",
      );
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
elements.preprodReviewTrigger?.addEventListener("click", () => runPreprodReviewAction("trigger-preprod-review"));
elements.preprodReviewStop?.addEventListener("click", () => runPreprodReviewAction("stop-preprod-review"));
elements.workflowSearch?.addEventListener("input", renderPilotageView);
elements.workflowRole?.addEventListener("change", renderPilotageView);
elements.historyFilters.addEventListener("submit", (event) => {
  event.preventDefault();
  void refreshHistory().catch(showHistoryError);
});

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  const delay = latestRuns.has_active ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
  refreshTimer = window.setTimeout(async () => {
    await refresh();
    scheduleRefresh();
  }, delay);
}

refresh({manual: true}).finally(scheduleRefresh);
