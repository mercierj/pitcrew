import {ACTIVE_LIFECYCLES, buildActionQueue, buildWorkflow} from "./view-model.mjs";
import {formatDate} from "./format.mjs";

const LIFECYCLE_LABELS = {
  todo: "À faire",
  processing: "En cours",
  review: "En revue",
  blocked: "Bloqué",
};

const ACTION_KIND_LABELS = {
  decision: "Décision",
  "agent-failure": "Incident agent",
  "merge-request": "Merge request",
  proposal: "Proposition",
};

const asArray = (value) => Array.isArray(value) ? value : [];
const ACTION_CONTEXT_LIMIT = 180;

export function describeFailedAgents(agents) {
  const skills = asArray(agents)
    .filter((agent) => agent?.health === "failed")
    .map((agent) => typeof agent.skill === "string" ? agent.skill.trim() : "")
    .filter(Boolean);
  if (skills.length === 1) return `${skills[0]} nécessite une intervention.`;
  if (skills.length > 1) return `${skills.join(", ")} nécessitent une intervention.`;
  return "Un agent nécessite une intervention.";
}

const normalizeContextText = (value) => (
  typeof value === "string" ? value.replace(/\s+/g, " ").trim() : ""
);

const markdownToPlainText = (value) => String(value ?? "")
  .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
  .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
  .replace(/`([^`]*)`/g, "$1")
  .replace(/\*\*([^*]+)\*\*/g, "$1")
  .replace(/__([^_]+)__/g, "$1")
  .replace(/^#{1,6}\s+/gm, "")
  .replace(/^\s*[-+*]\s+/gm, "")
  .replace(/\s+/g, " ")
  .trim();

const boundedContextText = (value, limit = ACTION_CONTEXT_LIMIT) => {
  const normalized = normalizeContextText(value);
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit - 1).trimEnd()}…`;
};

const canonicalUrlOf = (resource) => (
  typeof resource?.canonical_url === "string" && resource.canonical_url
    ? resource.canonical_url
    : typeof resource?.web_url === "string"
      ? resource.web_url
      : ""
);

const ticketRunStatus = (run, capacity) => {
  if (run?.state === "queued") {
    return `En attente · position ${run.queue_position ?? "?"}`;
  }
  if (run?.state === "running") {
    return (
      `En cours · ${capacity?.running ?? 1}/`
      + `${capacity?.max_concurrent ?? 3} places utilisées`
    );
  }
  if (run?.state === "failed") return "Échec · Relancer";
  if (run?.state === "cancelled") return "Annulé · Relancer";
  return "";
};

export function formatUpdateAge(value, now = Date.now()) {
  const updatedAt = Date.parse(value);
  if (!Number.isFinite(updatedAt)) return "date inconnue";
  const minutes = Math.max(0, Math.floor((now - updatedAt) / 60000));
  if (minutes < 1) return "à l’instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  return `il y a ${Math.floor(hours / 24)} j`;
}

export function enrichWorkflowEntry(entry, sources = {}) {
  const issueUrl = canonicalUrlOf(entry);
  const activeRun = asArray(sources?.runs?.runs)
    .find((run) => issueUrl && run?.target === issueUrl)
    || entry?.active_run
    || null;
  const decisions = asArray(sources?.decisions?.decisions)
    .concat(sources?.decisions?.pending ? [sources.decisions.pending] : []);
  const decision = decisions.find((candidate) => (
    issueUrl && canonicalUrlOf(candidate?.ticket) === issueUrl
  )) || null;
  const changesByUrl = new Map(
    asArray(sources?.work?.changes)
      .map((change) => [canonicalUrlOf(change), change])
      .filter(([url]) => url),
  );
  const changes = asArray(entry?.related_change_urls)
    .filter((url) => typeof url === "string")
    .map((url) => changesByUrl.get(url))
    .filter(Boolean);
  const activeSkill = typeof activeRun?.skill === "string"
    ? activeRun.skill
    : "";
  const capacity = activeSkill
    ? sources?.runs?.capacity?.[activeSkill]
    : null;
  const agent = asArray(sources?.snapshot?.agents)
    .find((candidate) => activeSkill && candidate?.skill === activeSkill);
  const latestHistory = agent?.latest_history && typeof agent.latest_history === "object"
    ? agent.latest_history
    : null;

  return {
    ...entry,
    active_run: activeRun,
    delivery_context: {
      decision,
      labels: asArray(entry?.labels).filter((label) => (
        typeof label === "string"
        && label
        && !/^pitcrew(?:-|::)/i.test(label)
      )),
      changes,
      run_status: agent?.live_status?.phase
        ? ""
        : ticketRunStatus(activeRun, capacity),
      active_agent: activeSkill
        ? {
          skill: activeSkill,
          phase: agent?.live_status?.phase || activeRun?.phase || activeRun?.state || "",
          latest_history: latestHistory,
        }
        : null,
    },
  };
}

export function summarizeAgentFailure(summary, fallback = "Diagnostic requis.") {
  let parsed = summary;
  if (typeof summary === "string") {
    try {
      parsed = JSON.parse(summary);
    } catch {
      return boundedContextText(summary) || boundedContextText(fallback);
    }
  }

  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const reason = normalizeContextText(
      parsed.reason || parsed.error || parsed.message || parsed.summary,
    );
    const nextAction = normalizeContextText(parsed.next_action || parsed.nextAction);
    if (reason && nextAction) {
      return boundedContextText(
        `Raison : ${reason} · Prochaine action : ${nextAction}`,
      );
    }
    if (reason) return boundedContextText(reason);
    if (nextAction) return boundedContextText(`Prochaine action : ${nextAction}`);
    return boundedContextText(fallback);
  }

  return boundedContextText(parsed) || boundedContextText(fallback);
}

const resourceOf = (entry) => (
  entry?.resource && typeof entry.resource === "object" ? entry.resource : entry
);

const kindOf = (entry, resource) => (
  entry?.kind || resource?.resource_type || (resource?.health ? "agent-failure" : "")
);

const titleOf = (entry) => {
  const resource = resourceOf(entry) || {};
  const kind = kindOf(entry, resource);
  if (kind === "decision") {
    return resource.ticket?.title || resource.ticket_id || "Décision requise";
  }
  if (kind === "agent-failure") {
    return resource.skill || "Agent à diagnostiquer";
  }
  if (kind === "merge-request") {
    return `${resource.reference || `!${resource.number ?? "?"}`} · ${resource.title || "Merge request sans titre"}`;
  }
  if (kind === "proposal") {
    return resource.title || "Proposition sans titre";
  }
  return `${resource.reference || `#${resource.number ?? resource.iid ?? "?"}`} · ${resource.title || "Ticket sans titre"}`;
};

const contextOf = (entry) => {
  const resource = resourceOf(entry) || {};
  const kind = kindOf(entry, resource);
  if (kind === "decision") {
    return resource.question || "Une réponse est requise.";
  }
  if (kind === "agent-failure") {
    return summarizeAgentFailure(
      resource.latest_history?.summary,
      resource.health || "Diagnostic requis.",
    );
  }
  if (kind === "merge-request") {
    return `${resource.source_branch || "Branche inconnue"} → ${resource.target_branch || "Branche inconnue"}`;
  }
  if (kind === "proposal") {
    return resource.summary || resource.recommendation || "Proposition à examiner.";
  }
  return resource.agent_action?.label
    || resource.agent_action?.skill
    || resource.route
    || "Sans routage";
};

const safeExternalLink = (value, label) => {
  if (typeof value !== "string") return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  const link = document.createElement("a");
  link.href = url.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = label;
  return link;
};

const appendFact = (root, label, value) => {
  if (!value) return;
  const paragraph = document.createElement("p");
  const strong = document.createElement("strong");
  strong.textContent = `${label} : `;
  paragraph.append(strong, document.createTextNode(String(value)));
  root.append(paragraph);
};

const focusKeyFor = (scope, entry) => `${scope}:${entry?.key || titleOf(entry)}`;

export function preserveFocus(root, render) {
  const active = document.activeElement;
  const focusKey = root?.contains?.(active) ? active?.getAttribute?.("data-focus-key") : null;
  render();
  if (!focusKey) return;
  const replacement = [...root.querySelectorAll("[data-focus-key]")]
    .find((control) => control.getAttribute("data-focus-key") === focusKey);
  replacement?.focus();
}

export function captureOpenDetails(root) {
  const openKeys = new Set(
    [...root?.querySelectorAll?.("[data-detail-key]") || []]
      .filter((details) => details.open)
      .map((details) => details.getAttribute("data-detail-key"))
      .filter(Boolean),
  );
  return () => {
    [...root?.querySelectorAll?.("[data-detail-key]") || []].forEach((details) => {
      details.open = openKeys.has(details.getAttribute("data-detail-key"));
    });
  };
}

const createItemCard = (entry, onOpen, {done = false, focusScope = "action"} = {}) => {
  const card = document.createElement(done ? "li" : "article");
  card.className = done ? "done-card" : "action-card";
  const kind = document.createElement("p");
  kind.className = "action-card-kind";
  kind.textContent = ACTION_KIND_LABELS[kindOf(entry, resourceOf(entry))]
    || "Action";
  const title = document.createElement("h3");
  title.textContent = titleOf(entry);
  const context = document.createElement("p");
  context.className = done ? "done-card-context" : "action-card-context";
  context.textContent = contextOf(entry);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-quiet";
  button.textContent = done ? "Voir le détail" : entry?.label || "Ouvrir";
  button.setAttribute("data-focus-key", focusKeyFor(focusScope, entry));
  button.addEventListener("click", () => onOpen?.(entry));
  card.append(...(done ? [] : [kind]), title, context, button);
  return card;
};

export function renderCrewHealth(root, snapshot = {}) {
  if (!root) return;
  const agents = asArray(snapshot?.agents);
  const values = [
    ["Sains", agents.filter((agent) => agent?.health === "healthy").length],
    ["Alertes", agents.filter((agent) => agent?.health === "warning").length],
    ["Échecs", agents.filter((agent) => agent?.health === "failed").length],
    ["En cours", agents.filter((agent) => agent?.running).length],
  ];
  const list = document.createElement("dl");
  values.forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = String(value);
    list.append(term, detail);
  });
  const heading = root.querySelector("h2");
  root.replaceChildren(...(heading ? [heading] : []), list);
}

export function renderActionList(root, queue = [], onOpen) {
  if (!root) return;
  preserveFocus(root, () => {
    root.replaceChildren();
    if (!queue.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Aucune action ne nécessite votre attention.";
      root.append(empty);
      return;
    }
    queue.forEach((entry) => root.append(createItemCard(entry, onOpen)));
  });
}

const renderWorkflowLane = (lifecycle, entries, onOpen) => {
  const lane = document.createElement("section");
  lane.className = "workflow-lane";
  const heading = document.createElement("h3");
  heading.textContent = `${LIFECYCLE_LABELS[lifecycle]} · ${entries.length}`;
  lane.append(heading);
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Aucun ticket.";
    lane.append(empty);
    return lane;
  }
  entries.forEach((entry) => {
    const card = document.createElement("article");
    card.className = "workflow-card";
    const title = document.createElement("h4");
    title.textContent = `${entry.reference || `#${entry.number ?? entry.iid ?? "?"}`} · Ticket · ${entry.title || "Ticket sans titre"}`;
    const update = document.createElement("p");
    update.className = "workflow-card-meta";
    update.textContent = `Ticket · mis à jour ${formatUpdateAge(entry.updated_at)}`;
    const labels = document.createElement("p");
    labels.className = "workflow-card-labels";
    const usefulLabels = asArray(entry.delivery_context?.labels).slice(0, 3);
    if (entry.delivery_context?.decision) usefulLabels.push("Décision requise");
    labels.textContent = usefulLabels.join(" · ");
    const route = document.createElement("p");
    route.className = "workflow-card-agent";
    const responsibleSkill = entry.delivery_context?.active_agent?.skill
      || entry.agent_action?.skill
      || entry.route
      || entry.agent_action?.label
      || "Sans routage";
    const phase = entry.delivery_context?.active_agent?.phase;
    const runStatus = entry.delivery_context?.run_status;
    route.textContent = runStatus
      ? `${responsibleSkill} · ${runStatus}`
      : phase
        ? `${responsibleSkill} · ${phase}`
        : responsibleSkill;
    const mergeRequest = entry.delivery_context?.changes?.[0];
    const merge = document.createElement("p");
    merge.className = "workflow-card-merge";
    merge.textContent = mergeRequest
      ? `${mergeRequest.reference || "Changement"} · ${mergeRequest.state || "ouvert"} · vérifications ${mergeRequest.checks_status || "inconnues"}`
      : "";
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button button-quiet";
    action.textContent = entry.agent_action?.label || "Voir le détail";
    const activeRun = ["queued", "running"].includes(entry.active_run?.state);
    action.disabled = activeRun;
    action.setAttribute("aria-busy", activeRun ? "true" : "false");
    action.setAttribute("data-focus-key", focusKeyFor("workflow", entry));
    action.addEventListener("click", () => onOpen?.(entry));
    card.append(
      title,
      update,
      ...(labels.textContent ? [labels] : []),
      route,
      ...(merge.textContent ? [merge] : []),
      action,
    );
    lane.append(card);
  });
  return lane;
};

export function renderPilotage(roots = {}, sources = {}, handlers = {}) {
  const queue = buildActionQueue(sources);
  const workflow = Object.fromEntries(
    Object.entries(buildWorkflow(sources?.work, handlers.filters?.() || {}))
      .map(([lifecycle, entries]) => [
        lifecycle,
        entries.map((entry) => enrichWorkflowEntry(entry, sources)),
      ]),
  );
  if (roots.actionQueueCount) {
    roots.actionQueueCount.textContent = String(queue.length);
  }
  renderActionList(roots.actionQueueList, queue.slice(0, 3), handlers.openItem);
  if (roots.actionQueueMore) {
    preserveFocus(roots.actionQueueMore, () => {
      roots.actionQueueMore.replaceChildren();
      if (queue.length > 3) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button button-quiet";
        button.textContent = `Voir toutes (${queue.length})`;
        button.setAttribute("data-focus-key", "action-queue-more");
        button.addEventListener("click", () => handlers.openAllActions?.(queue, button));
        roots.actionQueueMore.append(button);
      }
    });
  }

  if (roots.workflowBoard) {
    preserveFocus(roots.workflowBoard, () => roots.workflowBoard.replaceChildren(
      ...ACTIVE_LIFECYCLES.map((lifecycle) => (
        renderWorkflowLane(lifecycle, workflow[lifecycle], handlers.openItem)
      )),
    ));
  }

  if (roots.doneCount) {
    roots.doneCount.textContent = String(workflow.done.length);
  }
  if (roots.doneList) {
    preserveFocus(roots.doneList, () => {
      roots.doneList.replaceChildren();
      if (!workflow.done.length) {
        const empty = document.createElement("li");
        empty.className = "empty-state";
        empty.textContent = "Aucun travail terminé aujourd’hui.";
        roots.doneList.append(empty);
      } else {
        workflow.done.forEach((entry) => (
          roots.doneList.append(createItemCard(entry, handlers.openItem, {done: true, focusScope: "done"}))
        ));
      }
    });
  }

  renderCrewHealth(roots.crewHealth, sources?.snapshot);
  return {queue, workflow};
}

const actionDescriptors = (entry, handlers) => {
  const actions = handlers.forEntry?.(entry);
  return asArray(actions).filter((action) => action && typeof action === "object");
};

export function renderActionFeedback(root, state) {
  if (!root) return;
  root.className = state
    ? `action-state action-state-${state.kind}`
    : "action-state";
  root.textContent = state?.message || state?.text || "";
}

export function renderItemDetail(entry, handlers = {}) {
  const resource = resourceOf(entry) || {};
  const kind = kindOf(entry, resource);
  const body = document.createElement("div");
  body.className = "item-detail";

  const description = resource.body
    || resource.description
    || resource.ticket?.body
    || resource.ticket?.description
    || resource.summary
    || resource.question;
  if (description) {
    const paragraph = document.createElement("p");
    paragraph.textContent = markdownToPlainText(description);
    body.append(paragraph);
  }

  const externalUrl = resource.web_url
    || resource.canonical_url
    || resource.ticket?.web_url
    || resource.ticket?.canonical_url;
  const link = safeExternalLink(externalUrl, "Ouvrir dans la forge");
  if (link) {
    const paragraph = document.createElement("p");
    paragraph.append(link);
    body.append(paragraph);
  }

  if (kind === "decision") {
    appendFact(body, "Question", resource.question);
    appendFact(body, "Contexte", resource.findings);
  } else if (kind === "proposal") {
    appendFact(body, "Preuves", asArray(resource.evidence).join(" · "));
    appendFact(body, "Recommandation", resource.recommendation);
  } else if (kind === "merge-request") {
    appendFact(
      body,
      "Branches",
      `${resource.source_branch || "inconnue"} → ${resource.target_branch || "inconnue"}`,
    );
    appendFact(body, "Vérifications", resource.checks_status || "inconnues");
  } else if (kind === "agent-failure") {
    appendFact(body, "Dernier résumé", resource.latest_history?.summary);
  } else if (kind === "issue") {
    appendFact(body, "Rôle", resource.agent_action?.skill || resource.route);
    appendFact(body, "Action", resource.agent_action?.label);
    const labels = asArray(resource.delivery_context?.labels).slice(0, 5);
    if (resource.delivery_context?.decision) labels.push("Décision requise");
    appendFact(body, "Labels", labels.join(" · "));
    appendFact(body, "Reproduction", resource.bugfix?.reproduction);
    appendFact(body, "Vérification", resource.bugfix?.verification);
    appendFact(body, "Raison du blocage", resource.bugfix?.blocked_reason);
    asArray(resource.delivery_context?.changes).forEach((mergeRequest, index) => {
      appendFact(
        body,
        index ? `Changement lié ${index + 1}` : "Changement lié",
        `${mergeRequest.reference || "?"} · ${mergeRequest.source_branch || "branche inconnue"} → ${mergeRequest.target_branch || "branche inconnue"} · Auteur : ${mergeRequest.author || "inconnu"} · Vérifications : ${mergeRequest.checks_status || "inconnues"}`,
      );
    });
    const activeAgent = resource.delivery_context?.active_agent;
    if (activeAgent) {
      appendFact(
        body,
        "Agent courant",
        `${activeAgent.skill}${activeAgent.phase ? ` · Phase : ${activeAgent.phase}` : ""}`,
      );
      appendFact(
        body,
        "Dernier résumé",
        summarizeAgentFailure(activeAgent.latest_history?.summary, "Aucun résumé récent."),
      );
      const latestOutcome = activeAgent.latest_history?.outcome;
      const latestFinishedAt = activeAgent.latest_history?.finished_at;
      if (latestOutcome || latestFinishedAt) {
        appendFact(
          body,
          "Dernière exécution",
          [latestOutcome, latestFinishedAt ? formatDate(latestFinishedAt) : ""].filter(Boolean).join(" · "),
        );
      }
    }
  }

  const actions = actionDescriptors(entry, handlers);
  if (actions.length) {
    const actionRoot = document.createElement("div");
    actionRoot.className = "detail-actions";
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = action.className || "button button-primary";
      button.textContent = action.label || "Continuer";
      button.disabled = Boolean(action.disabled);
      button.addEventListener("click", async () => {
        if (button.disabled) return;
        const idleLabel = button.textContent;
        button.disabled = true;
        button.textContent = action.pendingLabel || "Traitement…";
        let completed = false;
        try {
          completed = await action.run?.(button, actionRoot);
        } catch {
          completed = false;
        }
        if (action.singleUse && completed !== false) {
          button.textContent = action.successLabel || "Action acceptée";
          return;
        }
        button.disabled = Boolean(action.disabled);
        button.textContent = idleLabel;
      });
      actionRoot.append(button);
    });
    const actionState = actions.map((action) => action.state).find(Boolean);
    if (actionState) {
      const status = document.createElement("p");
      status.className = `action-state action-state-${actionState.kind}`;
      status.textContent = actionState.message || actionState.text || "";
      actionRoot.append(status);
    }
    body.append(actionRoot);
  }

  return {heading: titleOf(entry), body};
}
