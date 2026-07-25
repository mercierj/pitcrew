const POLL_INTERVAL_MS = 10_000;
const GITLAB_REFRESH_MS = 60_000;
const ACTIONS = new Set(["trigger", "stop", "restart"]);

const sessionToken = document.querySelector('meta[name="pitcrew-session"]')?.content ?? "";
const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

const elements = {
  refreshButton: document.querySelector("#refresh-button"),
  refreshState: document.querySelector("#refresh-state"),
  globalStopButton: document.querySelector("#global-stop-button"),
  globalResumeButton: document.querySelector("#global-resume-button"),
  globalState: document.querySelector("#global-state"),
  operationalStatus: document.querySelector("#operational-status"),
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

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Requête refusée (${response.status})`);
  }
  return response.json();
}

function setText(element, value) {
  if (element) {
    element.textContent = value == null ? "" : String(value);
  }
}

function formatDate(value) {
  if (!value) {
    return "Aucune exécution";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Date indisponible";
  }
  return dateFormatter.format(date);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function formatTokens(value) {
  if (typeof value === "string" && /^\d+$/.test(value)) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  return "Données indisponibles";
}

function formatCost(value) {
  if (typeof value !== "string") {
    return "Données indisponibles";
  }
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) {
    return "Données indisponibles";
  }
  const integer = new Intl.NumberFormat("fr-FR").format(BigInt(match[1]));
  const decimals = (match[2] || "").padEnd(4, "0").slice(0, 6);
  return `${integer},${decimals} USD`;
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

function createActionButton(action, skill, label, globalStopped) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.skill = skill;
  button.className = action === "trigger" ? "button button-primary" : "button button-quiet";
  button.textContent = label;
  button.disabled = globalStopped || pendingSkills.has(skill);
  button.addEventListener("click", () => control(action, skill));
  return button;
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
    phase.textContent = status?.phase || "Exécution du passage courant";

    const facts = document.createElement("dl");
    facts.className = "live-agent-facts";
    [
      ["Depuis", formatDate(status?.started_at)],
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
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  const globalStopped = snapshot?.global_state === "stopped";
  elements.agentGrid.replaceChildren();

  if (agents.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Aucun agent activé n’est disponible.";
    elements.agentGrid.append(empty);
  }

  agents.forEach((agent) => {
    const card = document.createElement("article");
    card.className = "agent-card";

    const heading = document.createElement("div");
    heading.className = "agent-heading";
    const title = document.createElement("h3");
    title.textContent = agent.skill || "Agent sans nom";
    heading.append(title, makeBadge(agent.health));

    const facts = document.createElement("dl");
    facts.className = "agent-facts";
    const factValues = [
      ["État local", agent.running ? "En cours" : agent.loaded ? "Planifié" : "Arrêté"],
      ["Dernier passage", formatDate(agent.latest_history?.finished_at)],
      ["Prochain passage estimé", formatDate(agent.estimated_next_pass)],
      ["Intervalle", agent.interval_seconds ? `${agent.interval_seconds} s` : "Indisponible"],
    ];
    factValues.forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      facts.append(term, detail);
    });

    const summary = document.createElement("p");
    summary.className = "agent-summary";
    summary.textContent = agent.latest_history?.summary || "Aucun compte rendu récent.";

    const actions = document.createElement("div");
    actions.className = "agent-actions";
    actions.append(
      createActionButton("trigger", agent.skill, "Déclencher", globalStopped),
      createActionButton("restart", agent.skill, "Réinstaller", globalStopped),
      createActionButton("stop", agent.skill, "Arrêter", globalStopped),
    );
    card.append(
      heading,
      facts,
      createModelControl(agent, snapshot?.model_catalog, globalStopped),
      summary,
      renderUsage("Dernier passage", agent.latest_usage),
      renderUsage("Usage · 7 jours", agent.usage_7d),
      actions,
    );
    elements.agentGrid.append(card);
  });

  renderDisabled(snapshot?.disabled_roles);
  refreshSkillFilter(agents);
}

function renderDisabled(disabledRoles) {
  const roles = Array.isArray(disabledRoles) ? disabledRoles : [];
  elements.disabledList.replaceChildren();
  setText(elements.disabledCount, roles.length);
  roles.forEach((role) => {
    const item = document.createElement("article");
    const name = document.createElement("strong");
    name.textContent = role.skill || "Rôle sans nom";
    const reason = document.createElement("p");
    reason.textContent = role.reason || "Ce rôle n’est pas configuré.";
    const model = document.createElement("p");
    model.className = "latest-model";
    model.textContent = `Modèle résolu : ${modelLabel(null, role.configured_model)}`;
    item.append(name, reason, model);
    elements.disabledList.append(item);
  });
}

function refreshSkillFilter(agents) {
  const selected = elements.historySkill.value;
  const fragment = document.createDocumentFragment();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Tous les agents";
  fragment.append(all);
  agents.forEach((agent) => {
    const option = document.createElement("option");
    option.value = agent.skill;
    option.textContent = agent.skill;
    fragment.append(option);
  });
  elements.historySkill.replaceChildren(fragment);
  if ([...elements.historySkill.options].some((option) => option.value === selected)) {
    elements.historySkill.value = selected;
  }
}

function historyMetadata(record, modelCatalog) {
  const model = record?.model;
  const usage = record?.usage;
  const usageFields = [
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
  ];
  if (
    typeof model !== "string"
    || !modelCatalog
    || typeof modelCatalog !== "object"
    || !Object.hasOwn(modelCatalog, model)
    || !usage
    || typeof usage !== "object"
    || usageFields.some((field) => formatTokens(usage[field]) === "Données indisponibles")
  ) {
    return "Modèle/usage indisponibles";
  }
  return `Modèle : ${modelLabel(modelCatalog, model)} · Total : ${formatTokens(usage.total_tokens)} jetons`;
}

function renderHistory(records, modelCatalog) {
  const history = Array.isArray(records) ? records : [];
  elements.activityList.replaceChildren();
  if (history.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "Aucune activité ne correspond à ces filtres.";
    elements.activityList.append(empty);
    return;
  }
  history.forEach((record) => {
    const item = document.createElement("li");
    const top = document.createElement("div");
    const skill = document.createElement("strong");
    skill.textContent = record.skill || "Agent inconnu";
    const outcome = document.createElement("span");
    outcome.className = `badge badge-${record.outcome === "failed" ? "failed" : "healthy"}`;
    outcome.textContent = record.outcome || "inconnu";
    top.append(skill, outcome);

    const date = document.createElement("time");
    date.dateTime = record.finished_at || "";
    date.textContent = formatDate(record.finished_at);
    const summary = document.createElement("p");
    summary.textContent = record.summary || "Aucun résumé.";
    const metadata = document.createElement("p");
    metadata.className = "history-metadata";
    metadata.textContent = historyMetadata(record, modelCatalog);
    item.append(top, date, summary, metadata);
    elements.activityList.append(item);
  });
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

function renderGitLab(work) {
  elements.gitlabGroups.replaceChildren();
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
      column.append(card);
    });
    elements.gitlabGroups.append(column);
  });
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

function historyPath() {
  const query = new URLSearchParams();
  if (elements.historySkill.value) {
    query.set("skill", elements.historySkill.value);
  }
  if (elements.historyOutcome.value) {
    query.set("outcome", elements.historyOutcome.value);
  }
  const suffix = query.toString();
  return suffix ? `/api/history?${suffix}` : "/api/history";
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
      const [snapshot, history] = await Promise.all([
        fetchJson("/api/status"),
        fetchJson(historyPath()),
      ]);
      renderOverview(snapshot);
      renderLiveAgents(snapshot);
      renderAgents(snapshot);
      renderHistory(history, snapshot?.model_catalog);

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
elements.historyFilters.addEventListener("submit", (event) => {
  event.preventDefault();
  refresh();
});

refresh({ manual: true });
window.setInterval(() => refresh(), POLL_INTERVAL_MS);
