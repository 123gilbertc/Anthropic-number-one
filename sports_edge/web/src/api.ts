export type Json = Record<string, any>;

export async function get<T = Json>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function post<T = Json>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const pct = (p: number | null | undefined) =>
  p == null ? "—" : `${(p * 100).toFixed(1)}%`;

export const cents = (s: string | null | undefined) =>
  s == null ? "—" : `${(Number(s) * 100).toFixed(0)}¢`;

export const usd = (s: string | number | null | undefined) =>
  s == null ? "—" : `$${Number(s).toFixed(2)}`;

export const time = (iso: string | null | undefined) =>
  iso ? new Date(iso).toISOString().slice(11, 19) + "Z" : "—";
