import {formatDate, formatTokens} from "./format.mjs";

export function historyPath(skill, outcome) {
  const query = new URLSearchParams();
  if (skill) {
    query.set("skill", skill);
  }
  if (outcome) {
    query.set("outcome", outcome);
  }
  const suffix = query.toString();
  return suffix ? `/api/history?${suffix}` : "/api/history";
}

function modelLabel(modelCatalog, model) {
  const entry = modelCatalog && typeof modelCatalog === "object" ? modelCatalog[model] : null;
  return typeof entry?.label === "string" && entry.label ? `${model} · ${entry.label}` : model || "Indisponible";
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

export function renderHistory(root, records, modelCatalog = {}) {
  const history = Array.isArray(records) ? records : [];
  root.replaceChildren();
  if (history.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "Aucune activité ne correspond à ces filtres.";
    root.append(empty);
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
    root.append(item);
  });
}

export function syncHistorySkills(select, agents) {
  const selected = select.value;
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Tous les agents";
  const skills = new Set(
    (Array.isArray(agents) ? agents : [])
      .map((agent) => agent?.skill)
      .filter((skill) => typeof skill === "string" && skill),
  );
  const options = [...skills]
    .sort()
    .map((skill) => {
      const option = document.createElement("option");
      option.value = skill;
      option.textContent = skill;
      return option;
    });
  select.replaceChildren(all, ...options);
  select.value = options.some((option) => option.value === selected) ? selected : "";
}
