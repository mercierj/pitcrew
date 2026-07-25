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

export function buildWorkflow(work = {}, { query = "", role = "", doneDay = currentParisDay() } = {}) {
  const groups = Object.fromEntries([...ACTIVE_LIFECYCLES, "done"].map((lifecycle) => [lifecycle, []]));
  const normalizedQuery = String(query).toLocaleLowerCase("fr");
  const normalizedRole = String(role).toLocaleLowerCase("fr");

  for (const issue of workflowIssues(work)) {
    if (!issue || typeof issue !== "object") continue;
    const lifecycle = issue.lifecycle;
    const card = { ...issue, key: resourceKey(issue, "issue"), kind: "issue", lifecycle };
    if (ACTIVE_LIFECYCLES.includes(lifecycle)) {
      const text = `${issue.iid ?? ""} ${issue.title ?? ""}`.toLocaleLowerCase("fr");
      const agentRole = String(issue.agent_action?.skill || issue.route || "").toLocaleLowerCase("fr");
      if ((!normalizedQuery || text.includes(normalizedQuery)) && (!normalizedRole || agentRole === normalizedRole)) {
        groups[lifecycle].push(card);
      }
    } else if (lifecycle === "done" || lifecycle === "closed") {
      if (dayInParis(issue.closed_at || issue.updated_at) === doneDay) groups.done.push(card);
    }
  }
  return groups;
}

export function buildActionQueue({ decisions = {}, proposals = {}, snapshot = {}, work = {} } = {}) {
  const actions = [];
  const decisionItems = asArray(decisions?.decisions).concat(decisions?.pending ? [decisions.pending] : []);
  for (const decision of decisionItems) {
    const ticket = decision?.ticket;
    actions.push({
      kind: "decision",
      key: resourceKey(ticket, "issue") || `decision:${decision?.ticket_id ?? ""}`,
      label: "Répondre",
      priority: 0,
      timestamp: timestampOf(decision),
      resource: decision,
    });
  }
  for (const agent of asArray(snapshot?.agents)) {
    if (!agent || !["failed", "warning"].includes(agent.health)) continue;
    const skill = agent.skill || agent.role || "";
    actions.push({ kind: "agent-failure", key: `agent:${skill}`, label: "Diagnostiquer", priority: 1, timestamp: timestampOf(agent), resource: agent });
  }
  for (const mergeRequest of asArray(work?.merge_requests)) {
    if (!mergeRequest || ["preprod", "prod"].includes(mergeRequest.target_branch)) continue;
    actions.push({
      kind: "merge-request",
      key: resourceKey(mergeRequest, "merge_request"),
      label: "Fusionner",
      priority: 2,
      timestamp: timestampOf(mergeRequest),
      resource: mergeRequest,
    });
  }
  for (const proposal of asArray(proposals?.proposals)) {
    actions.push({ kind: "proposal", key: `proposal:${proposal?.id ?? ""}`, label: "Examiner", priority: 3, timestamp: timestampOf(proposal), resource: proposal });
  }
  return actions.sort((left, right) => (
    left.priority - right.priority
    || String(left.timestamp).localeCompare(String(right.timestamp))
    || left.key.localeCompare(right.key)
  ));
}
