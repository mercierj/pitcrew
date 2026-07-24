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
  operationalStatus: document.querySelector("#operational-status"),
  globalBanner: document.querySelector("#global-banner"),
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

  elements.globalBanner.className = "banner";
  if (values.failed > 0) {
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

function createActionButton(action, skill, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.skill = skill;
  button.className = action === "trigger" ? "button button-primary" : "button button-quiet";
  button.textContent = label;
  button.disabled = pendingSkills.has(skill);
  button.addEventListener("click", () => control(action, skill));
  return button;
}

function syncSkillButtons(skill) {
  elements.agentGrid.querySelectorAll("button[data-skill]").forEach((button) => {
    if (button.dataset.skill === skill) {
      button.disabled = pendingSkills.has(skill);
    }
  });
}

function renderAgents(snapshot) {
  const agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
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
      createActionButton("trigger", agent.skill, "Déclencher"),
      createActionButton("restart", agent.skill, "Réinstaller"),
      createActionButton("stop", agent.skill, "Arrêter"),
    );
    card.append(heading, facts, summary, actions);
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
    item.append(name, reason);
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

function renderHistory(records) {
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
    item.append(top, date, summary);
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

async function refresh({ manual = false } = {}) {
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
      renderAgents(snapshot);
      renderHistory(history);

      const now = Date.now();
      if (manual || now - lastGitLabRefresh >= GITLAB_REFRESH_MS) {
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
elements.historyFilters.addEventListener("submit", (event) => {
  event.preventDefault();
  refresh();
});

refresh({ manual: true });
window.setInterval(() => refresh(), POLL_INTERVAL_MS);
