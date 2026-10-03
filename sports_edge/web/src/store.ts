// One shared server-data cache for every screen.
//
// Screens never compute probabilities, prices, fees, risk or fills: they read what the
// backend returned. The update stream (SSE) says *what changed*; the store refetches the
// affected views. On a gap or reconnect it refetches everything (reconciliation).
// Every key has a request generation counter so a slow, older response can never
// overwrite a newer one.
import { useSyncExternalStore } from "react";
import {
  ApiError, GameIntel, Health, Ledger, get, gameIntelSchema, healthSchema,
} from "./api";
import type { Board, Workspace } from "./types";

export type StreamState = "CONNECTING" | "CONNECTED" | "RECONNECTING" | "CLOSED";
export type DataMode = "live" | "demo";

export interface Selection { gameId: string | null; contractId: string | null; }
export interface Interest { boardMode: DataMode | null; gameId: string | null; window: string; date: string | null; tz: string; }

export interface State {
  health: Health | null;
  me: any | null;
  mode: DataMode;
  boards: Partial<Record<DataMode, Board>>;
  workspaces: Record<string, Workspace>;
  histories: Record<string, any>;
  decisionsByGame: Record<string, any[]>;
  portfolio: any | null;
  alerts: any | null;
  coverage: any | null;
  billing: any | null;
  // legacy research screens
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
  interest: Interest;
}

function savedMode(): DataMode {
  try { return (localStorage.getItem("te.mode") as DataMode) || "demo"; } catch { return "demo"; }
}

let state: State = {
  health: null, me: null, mode: savedMode(), boards: {}, workspaces: {}, histories: {},
  decisionsByGame: {}, portfolio: null, alerts: null, coverage: null, billing: null,
  games: [], signals: [], ledger: null, connections: null, evaluation: null, decisions: [],
  selection: { gameId: null, contractId: null }, stream: "CONNECTING", lastEventAt: null,
  lastSeq: 0, errors: {}, loading: true,
  interest: { boardMode: null, gameId: null, window: "all", date: null, tz: "UTC" },
};
const subs = new Set<() => void>();

function set(patch: Partial<State>) {
  state = { ...state, ...patch };
  subs.forEach((f) => f());
}

export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => sel(state));
}
export const getState = () => state;

export function select(gameId: string | null, contractId: string | null = null) {
  set({ selection: { gameId, contractId } });
  try { sessionStorage.setItem("se.selection", JSON.stringify({ gameId, contractId })); } catch { /* optional */ }
}

export function setMode(mode: DataMode) {
  try { localStorage.setItem("te.mode", mode); } catch { /* per-viewer convenience only */ }
  set({ mode });
  if (state.interest.boardMode) setInterest({ boardMode: mode });
}

const generation: Record<string, number> = {};
async function loadKey(key: string, fn: () => Promise<any>, write: (v: any) => Partial<State>) {
  const gen = (generation[key] = (generation[key] ?? 0) + 1);
  try {
    const v = await fn();
    if (generation[key] !== gen) return;  // a newer request superseded this one
    const errors = { ...state.errors };
    delete errors[key];
    set({ ...write(v), errors });
  } catch (e) {
    if (generation[key] !== gen) return;
    const msg = e instanceof ApiError ? `${e.code}: ${e.detail}` : String(e);
    set({ errors: { ...state.errors, [key]: msg } });
  }
}
async function load<K extends keyof State>(key: K, fn: () => Promise<State[K]>) {
  return loadKey(key as string, fn, (v) => ({ [key]: v }) as Partial<State>);
}

export const loaders = {
  health: () => load("health", () => get("/api/health", healthSchema)),
  me: () => load("me", () => get("/api/account/me")),
  board: (mode: DataMode) => {
    const i = state.interest;
    const q = new URLSearchParams({ mode, tz: i.tz });
    if (i.date) q.set("date", i.date);
    return loadKey(`board:${mode}`, () => get(`/api/board?${q}`),
      (v) => ({ boards: { ...state.boards, [mode]: v } }));
  },
  workspace: (id: string) => loadKey(`ws:${id}`, () => get(`/api/events/${encodeURIComponent(id)}/workspace`),
    (v) => ({ workspaces: { ...state.workspaces, [id]: v } })),
  history: (id: string, window: string) => loadKey(`hist:${id}`,
    () => get(`/api/events/${encodeURIComponent(id)}/history?window=${window}&max_points=700`),
    (v) => ({ histories: { ...state.histories, [id]: v } })),
  decisions: (id: string) => loadKey(`dec:${id}`, () => get(`/api/decisions?game_id=${encodeURIComponent(id)}&limit=200`),
    (v) => ({ decisionsByGame: { ...state.decisionsByGame, [id]: v } })),
  portfolio: () => load("portfolio", () => get("/api/paper/portfolio")),
  alerts: () => load("alerts", () => get("/api/alerts")),
  coverage: () => load("coverage", () => get("/api/coverage")),
  billing: () => load("billing", () => get("/api/billing")),
  // legacy
  games: () => load("games", () => get("/api/games", gameIntelSchema.array())),
  signals: () => load("signals", () => get("/api/signals")),
  ledger: () => load("ledger", () => get("/api/paper/ledger")),
  connections: () => load("connections", () => get("/api/connections")),
  evaluation: () => load("evaluation", () => get("/api/evaluation")),
  legacyDecisions: () => load("decisions", () => get("/api/decisions?limit=300")),
};

/** Screens declare what they show; the stream keeps exactly that fresh. */
export function setInterest(patch: Partial<Interest>) {
  const next = { ...state.interest, ...patch };
  const changed = JSON.stringify(next) !== JSON.stringify(state.interest);
  set({ interest: next });
  if (changed) refreshInterest();
}

export async function refreshInterest() {
  const i = state.interest;
  const jobs: Promise<void>[] = [];
  if (i.boardMode) jobs.push(loaders.board(i.boardMode));
  if (i.gameId) {
    jobs.push(loaders.workspace(i.gameId), loaders.history(i.gameId, i.window), loaders.decisions(i.gameId));
  }
  await Promise.all(jobs);
}

export async function refreshAll() {
  await Promise.all([
    loaders.health(), loaders.me(), loaders.portfolio(), loaders.alerts(),
    loaders.games(), loaders.signals(), loaders.ledger(), loaders.connections(), loaders.evaluation(),
    loaders.legacyDecisions(), refreshInterest(),
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

// Coalesce bursts of events into one refetch per short window.
const dirty = new Set<string>();
let timer: number | null = null;
function invalidate(...keys: string[]) {
  keys.forEach((k) => dirty.add(k));
  if (timer != null) return;
  timer = window.setTimeout(async () => {
    timer = null;
    const ks = [...dirty];
    dirty.clear();
    const jobs: Promise<void>[] = [loaders.health()];
    if (ks.includes("games")) { jobs.push(loaders.games()); jobs.push(refreshInterest()); }
    if (ks.includes("signals")) jobs.push(loaders.signals());
    if (ks.includes("ledger")) { jobs.push(loaders.ledger(), loaders.evaluation(), loaders.portfolio(), loaders.alerts()); }
    if (ks.includes("decisions")) jobs.push(loaders.legacyDecisions());
    await Promise.all(jobs);
  }, 250);
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
  on("private", []);
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

/** The stream's identity is fixed when it opens: reopen it after sign-in or sign-out so
 *  the caller's own private paper records start (or stop) arriving. */
export async function identityChanged() {
  connectStream();
  await refreshAll();
}

export function disconnectStream() {
  es?.close();
  es = null;
  set({ stream: "CLOSED" });
}
