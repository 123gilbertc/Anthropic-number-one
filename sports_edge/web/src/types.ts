// Shapes of the product endpoints. Every number in them is computed by the backend.
export type BoardLabel = "VALUE CANDIDATE" | "WATCH" | "NO CURRENT EDGE" | "UNAVAILABLE" | "FINAL";
export type ContractState = "WATCH" | "VALUE CANDIDATE" | "PAPER ENTRY ELIGIBLE" | "HOLD" | "NO ADD" | "DATA UNAVAILABLE";
export type Sport = "NHL" | "NFL" | "TENNIS" | "MLB";

export interface EventInfo {
  game_id: string; sport: Sport; competition: string | null; participants: [string, string];
  names: Record<string, string>; participant_kind: "TEAM" | "PLAYER"; home: string; away: string;
  scheduled_start: string; round: string | null; venue_name?: string | null; details?: Record<string, any>;
  status: string;
}
export interface LikelyWinner {
  available: boolean; reason?: string; participant?: string; name?: string; probability?: number;
  low?: number; high?: number; status?: string; model_version?: string; as_of?: string;
  fair_odds?: { probability: number; decimal_odds: number; american_odds: number } | null;
}
export interface BoardRow {
  game_id: string; event: EventInfo; scoreboard: any; likely_winner: LikelyWinner;
  value_side: { participant: string; name: string; contract_id: string; state: ContractState } | null;
  assessment: BoardLabel;
  selected_market: { contract_id: string; participant: string; name: string; ask: string | null; bid: string | null; source: string; valid: boolean; state: ContractState } | null;
  sparkline: number[]; freshness: any; model_status: string; validated_net_edge: number | null;
  probability: number | null; has_market: boolean; forecast_ready: boolean;
}
export interface SportCoverage {
  schedule_known: boolean; discovered: number | null; monitored: number; forecast_ready: number;
  unavailable: number | null; note?: string | null; sources?: any[]; source?: string;
}
export interface Board {
  mode: "live" | "demo"; data_label: string; as_of: string; date: string; tz: string;
  rows: BoardRow[]; sports: Record<Sport, SportCoverage>; clock: string;
  meta: { mode: string; data_label: string; banners: string[]; run_id?: string };
}
export interface ContractView {
  contract_id: string; participant: string; name: string; venue: string; settlement_rule: string;
  settlement_text: string;
  model: null | { probability: number; low: number; high: number; void_probability: number; status: string;
    model_version: string; as_of: string; valid_until: string;
    fair_odds: { probability: number; decimal_odds: number; american_odds: number } | null };
  abstention: string | null;
  market: { source: string; valid: boolean; bid: string | null; ask: string | null; ask_size: number | null;
    bid_size: number | null; as_of: string | null;
    ask_formats: { cents: number; implied_probability: number; decimal_odds: number; american_odds: number } | null };
  entry: null | { stake: string; quantity: number; average_price: string; cost: string; entry_fees: string; all_in: string; depth_haircut: string; note: string };
  ev: null | { quantity: number; entry_cost: string; expected_fees: string; probability: number; probability_low: number;
    ev_point: string; ev_conservative: string; ev_point_cents_per_contract: string; ev_point_return_pct: string;
    edge_probability_points: number; plain_english: string };
  break_even_ask: string | null; state: ContractState; action: string | null;
  reasons: { code: string; text: string }[]; notes: string[];
  readiness: { check: string; ok: boolean; detail: string }[]; ready: boolean;
  decision_id: string | null; decision_time: string | null; expires_at: string | null; eligible: boolean;
  has_usable_forecast: boolean; line: any;
}
export interface Workspace {
  event: EventInfo; scoreboard: any; likely_winner: LikelyWinner;
  value_side: BoardRow["value_side"]; assessment: BoardLabel; contracts: ContractView[];
  freshness: any; as_of: string; evidence: any; meta: Board["meta"] & { session_mode?: string };
  model_cards: any[];
}
