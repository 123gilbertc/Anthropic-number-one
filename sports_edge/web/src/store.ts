// One shared server-data cache + selection state for every screen.
//
// Screens never keep their own copies of predictions or ledger data: they
// read from this store, which is filled only from backend responses. The
// live event stream (SSE) tells the store what changed; on any gap or
// reconnect the store refetches everything (reconciliation).
import { useSyncExternalStore } from "react";
import {
  ApiError, GameIntel, Health, Ledger, get, gameIntelSchema, healthSchema,
} from "./api";

export type StreamState = "CONNECTING" | "CONNECTED" | "RECONNECTING" | "CLOSED";

export interface Selection {
  gameId: string | null;
  contractId: string | null;
}

export interface State {
  health: Health | null;
  games: GameIntel[];
  signals: any[];
  ledger: Ledger | null;
  connections: any | null;
  evaluation: any | null;
  decisions: any[];
  selection: Selection;
  stream: StreamState;
  lastEventAt: number | null;
  lastSeq: number;
  errors: Record<string, string>;
  loading: boolean;
}

let state: State = {
  health: null, games: [], signals: [], ledger: null, connections: null, evaluation: null,
  decisions: [], selection: { gameId: null, contractId: null }, stream: "CONNECTING",
  lastEventAt: null, lastSeq: 0, errors: {}, loading: true,
};
const subs = new Set<() => void>();

function set(patch: Partial<State>) {
  state = { ...state, ...patch };
  subs.forEach((f) => f());
}

export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore(
    (f) => { subs.add(f); return () => subs.delete(f); },
    () => sel(state),
  );
}
export const getState = () => state;

export function select(gameId: string | null, contractId: string | null = null) {
  set({ selection: { gameId, contractId } });
  try { sessionStorage.setItem("se.selection", JSON.stringify({ gameId, contractId })); } catch { /* optional */ }
}

// Responses can arrive out of order (slow network, reconnect). Each key keeps a
// request counter and only the newest request may write; older ones are dropped.
const generation: Record<string, number> = {};
async function load<K extends keyof State>(key: K, fn: () => Promise<State[K]>) {
  const gen = (generation[key as string] = (generation[key as string] ?? 0) + 1);
  try {
    const v = await fn();
    if (generation[key as string] !== gen) return;  // a newer request superseded this one
    const errors = { ...state.errors };
    delete errors[key as string];
    set({ [key]: v, errors } as Partial<State>);
  } catch (e) {
    if (generation[key as string] !== gen) return;
    const msg = e instanceof ApiError ? `${e.code}: ${e.detail}` : String(e);
    set({ errors: { ...state.errors, [key as string]: msg } });
  }
}

export async function refreshAll() {
  await Promise.all([
    load("health", () => get("/api/health", healthSchema)),
    load("games", () => get("/api/games", gameIntelSchema.array())),
    load("signals", () => get("/api/signals")),
    load("ledger", () => get("/api/paper/ledger")),
    load("connections", () => get("/api/connections")),
    load("evaluation", () => get("/api/evaluation")),
    load("decisions", () => get("/api/decisions?limit=300")),
  ]);
  const s = state;
  if (!s.selection.gameId && s.games.length) {
    let saved: Selection | null = null;
    try { saved = JSON.parse(sessionStorage.getItem("se.selection") || "null"); } catch { /* ignore */ }
    const g = s.games.find((x) => x.game.game_id === saved?.gameId) ?? s.games[0];
    select(g.game.game_id, saved?.contractId ?? g.contracts[0]?.contract_id ?? null);
  }
  set({ loading: false });
}

// Coalesce bursts of events into one refetch per animation frame-ish window.
const dirty = new Set<string>();
let timer: number | null = null;
function invalidate(...keys: string[]) {
  keys.forEach((k) => dirty.add(k));
  if (timer != null) return;
  timer = window.setTimeout(async () => {
    timer = null;
    const ks = [...dirty];
    dirty.clear();
    const jobs: Promise<void>[] = [load("health", () => get("/api/health", healthSchema))];
    if (ks.includes("games")) jobs.push(load("games", () => get("/api/games", gameIntelSchema.array())));
    if (ks.includes("signals")) jobs.push(load("signals", () => get("/api/signals")));
    if (ks.includes("ledger")) {
      jobs.push(load("ledger", () => get("/api/paper/ledger")));
      jobs.push(load("evaluation", () => get("/api/evaluation")));
    }
    if (ks.includes("decisions")) jobs.push(load("decisions", () => get("/api/decisions?limit=300")));
    await Promise.all(jobs);
  }, 150);
}

let es: EventSource | null = null;
export function connectStream() {
  es?.close();
  set({ stream: "CONNECTING" });
  es = new EventSource("/api/stream");
  const seen = (e: MessageEvent) => {
    const seq = Number(e.lastEventId);
    if (seq && state.lastSeq && seq > state.lastSeq + 1) invalidate("games", "signals", "ledger", "decisions");
    set({ lastEventAt: Date.now(), lastSeq: seq || state.lastSeq, stream: "CONNECTED" });
  };
  es.onopen = () => { set({ stream: "CONNECTED" }); refreshAll(); };  // reconcile after (re)connect
  es.onerror = () => set({ stream: es?.readyState === EventSource.CLOSED ? "CLOSED" : "RECONNECTING" });
  const on = (name: string, keys: string[]) =>
    es!.addEventListener(name, (e) => { seen(e as MessageEvent); invalidate(...keys); });
  on("hello", []);
  on("state", ["games"]);
  on("clock", ["games"]);
  on("decision", ["games", "decisions"]);
  on("signal", ["signals", "games"]);
  on("order_result", ["ledger", "games"]);
  on("ledger", ["ledger", "games"]);
  on("settlement", ["ledger", "games"]);
  on("llm_review", []);
  on("session", ["games", "signals", "ledger", "decisions"]);
  es.addEventListener("resync", () => { set({ lastSeq: 0 }); refreshAll(); });
}

export function disconnectStream() {
  es?.close();
  es = null;
  set({ stream: "CLOSED" });
}
