// Selected-game workspace: three questions first, evidence one click away.
import { useEffect, useState } from "react";
import { ApiError, command } from "../api";
import { ModelTag, Notice, StatePill } from "../components/Bits";
import { DecisionPanel } from "../components/DecisionPanel";
import { EvidenceDrawer } from "../components/Evidence";
import { IconLeft } from "../components/Icons";
import { ProbPriceChart } from "../components/ProbPriceChart";
import { ScoreHeader } from "../components/ScoreHeader";
import { clock, pct, probAs, SPORT_LABEL, type OddsFormat } from "../format";
import { href } from "../router";
import { setInterest, useStore } from "../store";
import type { ContractView, Workspace } from "../types";

const WINDOWS: [string, string][] = [["pregame_to_live", "Pregame → live"], ["60m", "60 min"], ["15m", "15 min"], ["5m", "5 min"]];

export function GameWorkspace({ id }: { id: string }) {
  const ws = useStore((s) => s.workspaces[id]) as Workspace | undefined;
  const hist = useStore((s) => s.histories[id]);
  const err = useStore((s) => s.errors[`ws:${id}`]);
  const me = useStore((s) => s.me);
  const window_ = useStore((s) => s.interest.window);
  const fmt: OddsFormat = me?.preferences?.odds_format ?? "probability";
  const [sel, setSel] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [tab, setTab] = useState<"matchup" | "market" | "model" | "history">("matchup");

  useEffect(() => { setInterest({ gameId: id, boardMode: null }); setSel(null); return () => setInterest({ gameId: null }); }, [id]);

  if (err && !ws) {
    return (
      <div className="stack-lg">
        <a href={href("/board")} className="row small"><IconLeft /> Live Board</a>
        <Notice kind="caution">This game has no live workspace: {err.includes("UNKNOWN_GAME") ? "it is not being monitored (live game feed not connected)." : err}</Notice>
      </div>
    );
  }
  if (!ws) return <p className="muted">Loading…</p>;
  const ev = ws.event;
  const valueC = ws.value_side ? ws.contracts.find((c) => c.contract_id === ws.value_side!.contract_id) : null;
  const favC = ws.likely_winner.available ? ws.contracts.find((c) => c.participant === ws.likely_winner.participant) : null;
  const selected: ContractView | null = ws.contracts.find((c) => c.contract_id === sel) ?? valueC ?? favC ?? ws.contracts[0] ?? null;
  const lw = ws.likely_winner;
  const title = ev.participant_kind === "PLAYER" ? `${ev.names[ev.home]} vs ${ev.names[ev.away]}` : `${ev.names[ev.away]} at ${ev.names[ev.home]}`;
  const supporting = ws.evidence?.supporting?.[0];
  const contrary = ws.evidence?.contrary?.[0];

  return (
    <div className="stack-lg" data-testid="workspace">
      <div className="row between wrap">
        <div className="stack" style={{ gap: 2 }}>
          <a href={href("/board")} className="row small" style={{ gap: 4 }}><IconLeft /> Live Board</a>
          <h1 className="truncate">{title}</h1>
          <span className="small muted">{SPORT_LABEL[ev.sport]}{ev.competition && ev.competition !== ev.sport ? ` · ${ev.competition}` : ""}{ev.round ? ` · ${ev.round}` : ""}{ev.venue_name ? ` · ${ev.venue_name}` : ""}</span>
        </div>
        <div className="row wrap"><StatePill s={ws.assessment} /><span className="tiny muted">as of {clock(ws.as_of)}</span></div>
      </div>

      <section className="panel"><ScoreHeader ev={ev} sb={ws.scoreboard} /></section>

      <div className="questions">
        <section className="panel q" data-testid="q-likely">
          <span className="qn">1 · Most likely to win from here</span>
          {lw.available ? (
            <>
              <span className="qa">{lw.name} <span className="num">{probAs(lw.probability, fmt, lw.fair_odds)}</span></span>
              <span className="qs">Range {pct(lw.low)}–{pct(lw.high)} · {lw.model_version}</span>
              <span><ModelTag status={lw.status} /></span>
            </>
          ) : <><span className="qa muted">Unavailable</span><span className="qs">{lw.reason}</span></>}
        </section>
        <section className="panel q" data-testid="q-price">
          <span className="qn">2 · Is the price attractive after costs?</span>
          {valueC ? (
            <>
              <span className="qa">{valueC.name} at {Math.round(Number(valueC.market.ask) * 100)}¢</span>
              <span className="qs">{valueC.ev ? `Cautious net EV ${Number(valueC.ev.ev_conservative) >= 0 ? "+" : "−"}$${Math.abs(Number(valueC.ev.ev_conservative)).toFixed(2)} on a $${valueC.entry?.stake} paper entry` : ""}</span>
              <span><StatePill s={valueC.state} /></span>
            </>
          ) : (
            <>
              <span className="qa">Value side: none</span>
              <span className="qs">{ws.contracts.length === 0 ? "No market is mapped to this game." :
                "At current prices, no side clears costs and the cautious estimate. A likely winner can still be overpriced."}</span>
            </>
          )}
        </section>
        <section className="panel q" data-testid="q-why">
          <span className="qn">3 · Why, and what could change it</span>
          <span className="qs" style={{ fontSize: 13.5, color: "var(--text)" }}>
            {supporting ? `${supporting.label} adds ${supporting.delta_pp.toFixed(1)} pts in this model.` : "No single input dominates this estimate."}
            {contrary ? ` Against: ${contrary.label}${contrary.delta_pp != null ? ` (${contrary.delta_pp.toFixed(1)} pts)` : ""}.` : ""}
          </span>
          <span className="qs">Changes on: {ws.evidence?.invalidation?.[0]?.replace("A new event: ", "")}</span>
          <button className="small" style={{ alignSelf: "flex-start", marginTop: "auto" }} onClick={() => setDrawer(true)} data-testid="why-btn">Explain assessment</button>
        </section>
      </div>

      <div className="ws-grid">
        <div className="stack-lg" style={{ minWidth: 0 }}>
          <section className="panel">
            <div className="panel-head">
              <h3>Probability vs price · {selected?.name ?? ""}</h3>
              <div className="seg" role="group" aria-label="Time window">
                {WINDOWS.map(([k, l]) => <button key={k} aria-pressed={window_ === k || (window_ === "all" && k === "pregame_to_live")} onClick={() => setInterest({ window: k })}>{l}</button>)}
              </div>
            </div>
            <div className="panel-pad">
              {selected ? <ProbPriceChart hist={hist} contractId={selected.contract_id} participant={selected.participant} /> :
                <p className="muted">No market mapped: no price history.</p>}
            </div>
          </section>

          <section className="panel">
            <div className="tabs" role="tablist" style={{ padding: "0 8px" }}>
              {(["matchup", "market", "model", "history"] as const).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                  {{ matchup: "Matchup", market: "Market detail", model: "Model detail", history: "Decision history" }[t]}
                </button>
              ))}
            </div>
            <div className="panel-pad">
              {tab === "matchup" && <Matchup ws={ws} hist={hist} />}
              {tab === "market" && <MarketDetail ws={ws} />}
              {tab === "model" && <ModelDetail ws={ws} selected={selected} />}
              {tab === "history" && <DecisionHistory id={id} />}
            </div>
          </section>
        </div>
        <div className="sticky">
          <DecisionPanel ws={ws} selected={selected} onSelect={setSel} onExplain={() => setDrawer(true)} />
        </div>
      </div>
      {drawer && ws.evidence && <EvidenceDrawer ev={ws.evidence} subject={ws.evidence.subject?.name ?? "this game"} onClose={() => setDrawer(false)} />}
    </div>
  );
}

function Matchup({ ws, hist }: { ws: Workspace; hist: any }) {
  const anns = (hist?.annotations ?? []).slice().reverse().slice(0, 40);
  const f = ws.freshness;
  return (
    <div className="grid2">
      <div className="stack">
        <h4 className="upper muted">Game timeline (recorded)</h4>
        {anns.length === 0 && <p className="small muted">No events recorded yet.</p>}
        <ul className="timeline">{anns.map((a: any) => (
          <li key={a.id}><span className="num muted">{clock(a.t).slice(0, 8)}</span><span>{a.label}</span></li>
        ))}</ul>
      </div>
      <div className="stack">
        <h4 className="upper muted">Data freshness</h4>
        <dl className="dk decision" style={{ margin: 0, display: "grid", gridTemplateColumns: "1fr auto", gap: 6 }}>
          <dt className="dim">Game state age</dt><dd className="num">{f.game_state_age_s == null ? "—" : `${f.game_state_age_s}s`}</dd>
          <dt className="dim">Game feed last heard</dt><dd className="num">{f.game_feed_last_seen_s == null ? "—" : `${f.game_feed_last_seen_s}s ago`}</dd>
          <dt className="dim">Market feed last heard</dt><dd className="num">{f.market_feed_last_seen_s == null ? "—" : `${f.market_feed_last_seen_s}s ago`}</dd>
        </dl>
        <p className="tiny muted">{f.note}</p>
        {f.pending_reconciliation?.length > 0 && <Notice kind="caution">Feed reconciliation pending: {f.pending_reconciliation.join(", ")}. Paper entries are blocked until a full snapshot arrives.</Notice>}
        {ws.event.details && Object.keys(ws.event.details).length > 0 && (
          <>
            <h4 className="upper muted">Schedule facts</h4>
            <div className="small dim">{Object.entries(ws.event.details).map(([k, v]) => <div key={k}>{k.replace(/_/g, " ")}: <b>{Array.isArray(v) ? v.join(" / ") : String(v)}</b></div>)}</div>
          </>
        )}
      </div>
    </div>
  );
}

function MarketDetail({ ws }: { ws: Workspace }) {
  return (
    <div className="stack">
      <div className="table-wrap">
        <table data-testid="market-table">
          <thead><tr><th>Contract</th><th className="right">Bid</th><th className="right">Ask</th><th className="right">First observed</th><th className="right">Range</th>
            <th className="right">5 min</th><th className="right">15 min</th><th className="right">60 min</th></tr></thead>
          <tbody>{ws.contracts.map((c) => {
            const l = c.line ?? {};
            const ch = (k: string) => l.changes?.[k]?.status === "OK" ? `${l.changes[k].ask_change_cents >= 0 ? "+" : ""}${l.changes[k].ask_change_cents.toFixed(0)}¢` : "n/a";
            return (
              <tr key={c.contract_id}>
                <td><b>{c.name}</b><div className="tiny muted">{c.settlement_text}</div></td>
                <td className="right">{c.market.bid ? `${Math.round(Number(c.market.bid) * 100)}¢` : "—"}</td>
                <td className="right">{c.market.ask ? `${Math.round(Number(c.market.ask) * 100)}¢` : "—"}</td>
                <td className="right">{l.first_observed ? `${Math.round(l.first_observed.ask * 100)}¢ · ${clock(l.first_observed.t).slice(0, 5)}` : "—"}</td>
                <td className="right">{l.range ? `${Math.round(l.range.ask_min * 100)}–${Math.round(l.range.ask_max * 100)}¢` : "—"}</td>
                <td className="right">{ch("5m")}</td><td className="right">{ch("15m")}</td><td className="right">{ch("60m")}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      <ul className="small dim" style={{ margin: 0, paddingLeft: 18 }}>
        <li>“First observed” is the first price this application recorded. The provider's own opening price: {ws.contracts[0]?.line?.provider_open_note ?? "UNKNOWN"}</li>
        <li>Changes say n/a when the window starts before the first recorded price (insufficient history).</li>
        <li>Last trade: {ws.contracts[0]?.line?.last_trade_note ?? "UNKNOWN"}</li>
        <li>Implied probability of a price and the model's probability are different numbers; cents per contract, percentage-point edge and expected % return are also kept separate.</li>
      </ul>
    </div>
  );
}

function ModelDetail({ ws, selected }: { ws: Workspace; selected: ContractView | null }) {
  const card = ws.model_cards?.find((m: any) => m.sport === ws.event.sport);
  return (
    <div className="grid2">
      <div className="stack">
        <h4 className="upper muted">Model</h4>
        {card ? (
          <div className="small dim stack" style={{ gap: 4 }}>
            <div><b>{card.model_version}</b> · {card.kind}</div>
            <div>Status: <ModelTag status={card.status} /></div>
            <div>Trained on: {card.trained_on}</div>
            <div>Empirical reliability: <b>INSUFFICIENT EVIDENCE</b> (no out-of-sample or prospective evaluation for this sport)</div>
          </div>
        ) : <p className="small muted">No model loaded for this sport in this session.</p>}
        {selected?.model && <div className="small">Current estimate for {selected.name}: <b>{pct(selected.model.probability, 1)}</b> (band {pct(selected.model.low, 1)}–{pct(selected.model.high, 1)}) · valid until {clock(selected.model.valid_until)}</div>}
        <p className="tiny muted">Probability, data readiness and empirical reliability are separate measurements. There is no blended “confidence score”.</p>
      </div>
      <WhatIf ws={ws} />
    </div>
  );
}

const WHATIF_FIELDS: Record<string, [string, string][]> = {
  NHL: [["home_score", "Home goals"], ["away_score", "Away goals"], ["home_skaters", "Home skaters"], ["away_skaters", "Away skaters"]],
  NFL: [["home_score", "Home points"], ["away_score", "Away points"], ["yardline_100", "Yards to goal"], ["down", "Down"]],
  TENNIS: [["games_p1", "P1 games"], ["games_p2", "P2 games"], ["points_p1", "P1 points"], ["points_p2", "P2 points"]],
  MLB: [["home_runs", "Home runs"], ["away_runs", "Away runs"], ["outs", "Outs"], ["inning", "Inning"]],
};

function WhatIf({ ws }: { ws: Workspace }) {
  const me = useStore((s) => s.me);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const fields = WHATIF_FIELDS[ws.event.sport] ?? [];
  const run = async () => {
    setErr(null); setOut(null);
    const edits: Record<string, number> = {};
    for (const [k] of fields) if (vals[k] !== undefined && vals[k] !== "") edits[k] = Number(vals[k]);
    try { setOut(await command("POST", `/api/events/${encodeURIComponent(ws.event.game_id)}/whatif`, { edits })); }
    catch (x) { setErr(x instanceof ApiError ? x.detail : String(x)); }
  };
  return (
    <div className="stack">
      <h4 className="upper muted">What if… <span className="tag">simulation</span></h4>
      {!me?.signed_in ? <p className="small muted">Sign in to run hypothetical states through the model.</p> : (
        <>
          <div className="grid2">{fields.map(([k, l]) => (
            <label key={k} className="field">{l}<input type="number" min={0} value={vals[k] ?? ""} placeholder="current" onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></label>
          ))}</div>
          <button onClick={run} data-testid="whatif-run">Run simulation</button>
          {err && <div className="error">{err}</div>}
          {out && (
            <div className="notice" data-testid="whatif-out"><div>
              <b>SIMULATION</b> · {Object.entries(out.estimates).map(([p, e]: any) => `${ws.event.names[p] ?? p}: ${e.available ? pct(e.probability, 1) : e.reason}`).join(" · ")}
              <div className="tiny muted">{out.note}</div></div></div>
          )}
        </>
      )}
    </div>
  );
}

function DecisionHistory({ id }: { id: string }) {
  const ds = useStore((s) => s.decisionsByGame[id]) ?? [];
  const rows = ds.slice().reverse().slice(0, 80);
  return (
    <div className="table-wrap" style={{ maxHeight: 420 }}>
      <table><thead><tr><th>Time</th><th>Contract</th><th>Decision</th><th>Reasons</th></tr></thead>
        <tbody>{rows.map((d: any) => (
          <tr key={d.decision_id}><td className="num">{clock(d.decision_time)}</td><td className="small">{d.selection_team}</td>
            <td><span className="tag">{d.action}</span></td><td className="small dim">{d.explanation?.join(" ")}</td></tr>
        ))}</tbody></table>
      {rows.length === 0 && <p className="small muted">No decisions recorded yet.</p>}
    </div>
  );
}
