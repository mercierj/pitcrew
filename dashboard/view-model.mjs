export const ACTIVE_LIFECYCLES = ["todo", "processing", "review", "blocked"];

const PARIS_DAY_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Paris",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const dayInParis = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return PARIS_DAY_FORMATTER.format(date);
};

const asArray = (value) => Array.isArray(value) ? value : [];

const currentParisDay = () => dayInParis(new Date());

const timestampOf = (resource) => resource?.created_at || resource?.asked_at || resource?.updated_at || "";

const timestamp = (value) => {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER;
};

export function resourceKey(resource, fallbackType = "resource") {
  const type = typeof resource?.resource_type === "string" && resource.resource_type
    ? resource.resource_type
    : fallbackType;
  const url = typeof resource?.canonical_url === "string" && resource.canonical_url
    ? resource.canonical_url
    : resource?.web_url;
  return typeof url === "string" && url ? `${type}:${url}` : "";
}

const workflowIssues = (work) => {
  if (work?.groups && typeof work.groups === "object") {
    return Object.values(work.groups).flatMap(asArray);
  }
  return asArray(work?.issues);
};

const matchesFilters = (issue, query, role) => {
  const text = `${issue.iid ?? ""} ${issue.title ?? ""}`.toLocaleLowerCase("fr");
  const agentRole = String(issue.agent_action?.skill || issue.route || "").toLocaleLowerCase("fr");
  return (!query || text.includes(query)) && (!role || agentRole === role);
};

export function buildWorkflow(work = {}, { query = "", role = "", doneDay = currentParisDay() } = {}) {
  const groups = Object.fromEntries([...ACTIVE_LIFECYCLES, "done"].map((lifecycle) => [lifecycle, []]));
  const normalizedQuery = String(query).trim().toLocaleLowerCase("fr");
  const normalizedRole = String(role).toLocaleLowerCase("fr");

  for (const issue of workflowIssues(work)) {
    if (!issue || typeof issue !== "object") continue;
    const lifecycle = issue.lifecycle;
    const card = { ...issue, key: resourceKey(issue, "issue"), kind: "issue", lifecycle };
    if (ACTIVE_LIFECYCLES.includes(lifecycle)) {
      if (matchesFilters(issue, normalizedQuery, normalizedRole)) {
        groups[lifecycle].push(card);
      }
    } else if (lifecycle === "done" || lifecycle === "closed") {
      if (
        dayInParis(issue.closed_at || issue.updated_at) === doneDay
        && matchesFilters(issue, normalizedQuery, normalizedRole)
      ) groups.done.push(card);
    }
  }
  return groups;
}

export function buildActionQueue({ decisions = {}, proposals = {}, snapshot = {}, work = {} } = {}) {
  const actions = [];
  const decisionItems = asArray(decisions?.decisions).concat(decisions?.pending ? [decisions.pending] : []);
  for (const decision of decisionItems) {
    const ticket = decision?.ticket;
    const ticketId = typeof decision?.ticket_id === "string" ? decision.ticket_id.trim() : "";
    const key = resourceKey(ticket, "issue") || (ticketId ? `decision:${ticketId}` : "");
    if (!key) continue;
    actions.push({
      kind: "decision",
      key,
      label: "Répondre",
      priority: 0,
      timestamp: timestampOf(decision),
      resource: decision,
    });
  }
  for (const agent of asArray(snapshot?.agents)) {
    if (!agent || !["failed", "warning"].includes(agent.health)) continue;
    const skill = typeof agent.skill === "string" ? agent.skill.trim() : "";
    if (!skill) continue;
    actions.push({ kind: "agent-failure", key: `agent:${skill}`, label: "Diagnostiquer", priority: 1, timestamp: agent.latest_history?.finished_at || "", resource: agent });
  }
  for (const mergeRequest of asArray(work?.merge_requests)) {
    if (!mergeRequest || ["preprod", "prod"].includes(mergeRequest.target_branch)) continue;
    const key = resourceKey(mergeRequest, "merge_request");
    if (!key) continue;
    actions.push({
      kind: "merge-request",
      key,
      label: "Fusionner",
      priority: 2,
      timestamp: timestampOf(mergeRequest),
      resource: mergeRequest,
    });
  }
  for (const proposal of asArray(proposals?.proposals)) {
    const id = typeof proposal?.id === "string" ? proposal.id.trim() : "";
    if (!id) continue;
    actions.push({ kind: "proposal", key: `proposal:${id}`, label: "Examiner", priority: 3, timestamp: timestampOf(proposal), resource: proposal });
  }
  const sorted = actions.sort((left, right) => (
    left.priority - right.priority
    || timestamp(left.timestamp) - timestamp(right.timestamp)
    || left.key.localeCompare(right.key)
  ));
  const keys = new Set();
  return sorted.filter((action) => !keys.has(action.key) && keys.add(action.key));
}
