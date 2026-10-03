// Display helpers. They only format numbers the backend already computed.
export type OddsFormat = "probability" | "american" | "decimal" | "cents";

export const pct = (p: number | null | undefined, d = 0) => (p == null ? "—" : `${(p * 100).toFixed(d)}%`);
export const cents = (s: string | number | null | undefined) => (s == null ? "—" : `${Math.round(Number(s) * 100)}¢`);
export const usd = (s: string | number | null | undefined, signed = false) => {
  if (s == null) return "—";
  const v = Number(s);
  const t = `$${Math.abs(v).toFixed(2)}`;
  return signed ? `${v >= 0 ? "+" : "−"}${t}` : v < 0 ? `−${t}` : t;
};
export const pp = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} pts`);
export const clock = (iso: string | null | undefined) => (iso ? new Date(iso).toISOString().slice(11, 19) + "Z" : "—");

export function localTime(iso: string, tz: string) {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz }).format(new Date(iso));
  } catch { return clock(iso); }
}
export function localDate(day: string, tz: string) {
  const d = new Date(`${day}T12:00:00Z`);
  try { return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(d); }
  catch { return day; }
}
export function shiftDay(day: string, n: number) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export function ago(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

/** A contract price (dollars) in the chosen format, using backend-provided formats when given. */
export function priceAs(price: string | number | null | undefined, f: OddsFormat,
  formats?: { cents: number; implied_probability: number; decimal_odds: number; american_odds: number } | null): string {
  if (price == null) return "—";
  const p = Number(price);
  if (!(p > 0 && p < 1)) return "—";
  if (f === "cents") return `${Math.round(p * 100)}¢`;
  if (f === "probability") return `${(p * 100).toFixed(0)}% implied`;
  if (formats) return f === "decimal" ? formats.decimal_odds.toFixed(2) : signedAmerican(formats.american_odds);
  return `${Math.round(p * 100)}¢`;
}
export function probAs(p: number | null | undefined, f: OddsFormat,
  fair?: { decimal_odds: number; american_odds: number } | null): string {
  if (p == null) return "—";
  if (f === "decimal" && fair) return `${fair.decimal_odds.toFixed(2)} fair`;
  if (f === "american" && fair) return `${signedAmerican(fair.american_odds)} fair`;
  return pct(p, 0);
}
export const signedAmerican = (a: number) => (a > 0 ? `+${a}` : `${a}`);

export function initials(name: string) {
  const parts = name.replace(/[^A-Za-z .\/-]/g, "").split(/[\s/]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return name.slice(0, 3).toUpperCase();
}

export const SPORT_LABEL: Record<string, string> = { NHL: "NHL", NFL: "NFL", TENNIS: "Tennis", MLB: "MLB" };
