export const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Europe/Paris",
});

export function formatDate(value) {
  if (!value) return "Aucune exécution";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date indisponible" : dateFormatter.format(date);
}

export function formatTokens(value) {
  if (typeof value === "string" && /^\d+$/.test(value)) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
    return new Intl.NumberFormat("fr-FR").format(BigInt(value));
  }
  return "Données indisponibles";
}

export function formatCost(value) {
  if (typeof value !== "string") return "Données indisponibles";
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return "Données indisponibles";
  const integer = new Intl.NumberFormat("fr-FR").format(BigInt(match[1]));
  const decimals = (match[2] || "").padEnd(4, "0").slice(0, 6);
  return `${integer},${decimals} USD`;
}
