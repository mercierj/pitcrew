import {ACTIVE_LIFECYCLES, buildActionQueue, buildWorkflow} from "./view-model.mjs";

const LIFECYCLE_LABELS = {
  todo: "À faire",
  processing: "En cours",
  review: "En revue",
  blocked: "Bloqué",
};

const asArray = (value) => Array.isArray(value) ? value : [];
const ACTION_CONTEXT_LIMIT = 180;

const normalizeContextText = (value) => (
  typeof value === "string" ? value.replace(/\s+/g, " ").trim() : ""
);

const boundedContextText = (value, limit = ACTION_CONTEXT_LIMIT) => {
  const normalized = normalizeContextText(value);
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit - 1).trimEnd()}…`;
};

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
    return `!${resource.iid ?? "?"} · ${resource.title || "Merge request sans titre"}`;
  }
  if (kind === "proposal") {
    return resource.title || "Proposition sans titre";
  }
  return `#${resource.iid ?? "?"} · ${resource.title || "Ticket sans titre"}`;
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

const createItemCard = (entry, onOpen, {done = false, focusScope = "action"} = {}) => {
  const card = document.createElement(done ? "li" : "article");
  card.className = done ? "done-card" : "action-card";
  const title = document.createElement("h3");
  title.textContent = titleOf(entry);
  const context = document.createElement("p");
  context.textContent = contextOf(entry);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-quiet";
  button.textContent = done ? "Voir le détail" : entry?.label || "Ouvrir";
  button.setAttribute("data-focus-key", focusKeyFor(focusScope, entry));
  button.addEventListener("click", () => onOpen?.(entry));
  card.append(title, context, button);
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
    title.textContent = `#${entry.iid ?? "?"} · ${entry.title || "Ticket sans titre"}`;
    const route = document.createElement("p");
    route.textContent = entry.agent_action?.skill
      || entry.route
      || entry.agent_action?.label
      || "Sans routage";
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button button-quiet";
    action.textContent = entry.agent_action?.label || "Voir le détail";
    action.setAttribute("data-focus-key", focusKeyFor("workflow", entry));
    action.addEventListener("click", () => onOpen?.(entry));
    card.append(title, route, action);
    lane.append(card);
  });
  return lane;
};

export function renderPilotage(roots = {}, sources = {}, handlers = {}) {
  const queue = buildActionQueue(sources);
  const workflow = buildWorkflow(sources?.work, handlers.filters?.() || {});
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

  const description = resource.description
    || resource.ticket?.description
    || resource.summary
    || resource.question;
  if (description) {
    const paragraph = document.createElement("p");
    paragraph.textContent = description;
    body.append(paragraph);
  }

  const externalUrl = resource.web_url
    || resource.canonical_url
    || resource.ticket?.web_url
    || resource.ticket?.canonical_url;
  const link = safeExternalLink(externalUrl, "Ouvrir dans GitLab");
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
    appendFact(body, "Pipeline", resource.pipeline_status || "absent");
  } else if (kind === "agent-failure") {
    appendFact(body, "Dernier résumé", resource.latest_history?.summary);
  } else if (kind === "issue") {
    appendFact(body, "Rôle", resource.agent_action?.skill || resource.route);
    appendFact(body, "Action", resource.agent_action?.label);
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
        if (action.singleUse) button.disabled = true;
        const completed = await action.run?.(button, actionRoot);
        if (!action.singleUse) return;
        if (completed === false) {
          button.disabled = false;
          return;
        }
        button.textContent = action.successLabel || "Action acceptée";
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
