// Typed HTTP client. Types are generated from the backend's OpenAPI schema
// (src/api.gen.ts); zod guards validate the payloads the workflow depends on.
import { z } from "zod";
import type { components } from "./api.gen";

export type S = components["schemas"];
export type GameIntel = S["GameIntel"];
export type ContractIntel = S["ContractIntel"];
export type Health = S["Health"];
export type Preview = S["Preview"];
export type PaperOrder = S["PaperOrder"];
export type Ledger = S["Ledger"];
export type SessionInfo = S["SessionInfo"];

export class ApiError extends Error {
  constructor(public status: number, public code: string, public detail: string) {
    super(`${code}: ${detail}`);
  }
}

// Minimal runtime contracts: if the server sends something else, we show an
// error instead of rendering wrong numbers.
const contractSchema = z.object({
  contract_id: z.string(),
  selection: z.string(),
  probability: z.number().min(0).max(1).nullable(),
  probability_low: z.number().min(0).max(1).nullable(),
  probability_high: z.number().min(0).max(1).nullable(),
  best_ask: z.string().nullable(),
  action: z.string().nullable(),
  reasons: z.array(z.string()),
  signal_eligible: z.boolean(),
  decision_id: z.string().nullable(),
});
export const gameIntelSchema = z.object({
  game: z.object({ game_id: z.string(), home_team: z.string(), away_team: z.string() }),
  contracts: z.array(contractSchema),
  as_of: z.string(),
  event_seq: z.number().int(),
}).passthrough();
export const healthSchema = z.object({
  banners: z.array(z.string()),
  authenticated: z.boolean(),
  event_seq: z.number().int(),
  session: z.object({ run_id: z.string(), mode: z.enum(["REPLAY", "LIVE"]), data_label: z.string() }).passthrough(),
}).passthrough();
export const previewSchema = z.object({
  eligible: z.boolean(),
  max_quantity: z.number().int().min(0),
  decision: z.object({ decision_id: z.string(), action: z.string() }).passthrough(),
}).passthrough();
export const orderResponseSchema = z.object({
  created: z.boolean(),
  order: z.object({ order_id: z.string(), status: z.enum(["PENDING", "FILLED", "PARTIAL", "REJECTED"]) }).passthrough(),
});

async function parse(r: Response) {
  const text = await r.text();
  const body = text ? JSON.parse(text) : null;
  if (!r.ok) {
    const d = body?.detail ?? body;
    const code = d?.code ?? body?.code ?? `HTTP_${r.status}`;
    const detail = d?.detail ?? body?.detail ?? (typeof d === "string" ? d : JSON.stringify(d));
    throw new ApiError(r.status, code, detail);
  }
  return body;
}

export async function get<T>(path: string, schema?: z.ZodTypeAny): Promise<T> {
  const body = await parse(await fetch(path, { credentials: "same-origin" }));
  if (schema) {
    const res = schema.safeParse(body);
    if (!res.success) throw new ApiError(0, "INVALID_PAYLOAD", `${path}: ${res.error.issues[0]?.message}`);
  }
  return body as T;
}

// Commands: session cookie (HttpOnly, set by /api/auth/login) + CSRF header.
export async function command<T>(method: string, path: string, body?: unknown, schema?: z.ZodTypeAny): Promise<T> {
  const r = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "content-type": "application/json", "X-SE-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const out = await parse(r);
  if (schema) {
    const res = schema.safeParse(out);
    if (!res.success) throw new ApiError(0, "INVALID_PAYLOAD", `${path}: ${res.error.issues[0]?.message}`);
  }
  return out as T;
}

export const pct = (p: number | null | undefined) => (p == null ? "—" : `${(p * 100).toFixed(1)}%`);
export const cents = (s: string | null | undefined) => (s == null ? "—" : `${(Number(s) * 100).toFixed(0)}¢`);
export const usd = (s: string | number | null | undefined) => (s == null ? "—" : `$${Number(s).toFixed(2)}`);
export const time = (iso: string | null | undefined) => (iso ? new Date(iso).toISOString().slice(11, 19) + "Z" : "—");
