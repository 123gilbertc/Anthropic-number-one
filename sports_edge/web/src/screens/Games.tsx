import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError, ContractIntel, GameIntel, Preview, cents, command, orderResponseSchema, pct,
  previewSchema, time, usd,
} from "../api";
import { getState, refreshAll, select, useStore } from "../store";

export function GamesList({ onOpen }: { onOpen: () => void }) {
  const games = useStore((s) => s.games);
  const sel = useStore((s) => s.selection.gameId);
  if (!games.length) return <p className="muted">No games in this session.</p>;
  return (
    <section className="card">
      <div className="table-wrap">
        <table>
          <thead><tr><th>Game</th><th>State</th><th>Model favored</th><th>Value side</th><th /></tr></thead>
          <tbody>
            {games.map((g) => (
              <tr key={g.game.game_id} className={g.game.game_id === sel ? "selected" : ""}>
                <td>{g.game.away_team} @ {g.game.home_team}<div className="muted small">{g.game.game_id}</div></td>
                <td>{g.state ? `P${g.state.period} · ${g.state.away_score}–${g.state.home_score}${g.state.is_final ? " FINAL" : ""}` : "no state"}</td>
                <td>{g.model_favored_team ?? "UNKNOWN"}</td>
                <td>{g.value_side ?? "none"}</td>
                <td><button onClick={() => { select(g.game.game_id, g.contracts[0]?.contract_id ?? null); onOpen(); }}>Open</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function GameScreen() {
  const games = useStore((s) => s.games);
  const sel = useStore((s) => s.selection);
  const g = games.find((x) => x.game.game_id === sel.gameId) ?? games[0];
  if (!g) return <p className="muted">Loading game…</p>;
  const c = g.contracts.find((x) => x.contract_id === sel.contractId) ?? g.contracts[0];
  return (
    <>
      <GameHeader g={g} />
      <section className="card">
        <h3>Contracts <span className="muted small">select one to inspect</span></h3>
        <div className="contracts">
          {g.contracts.map((k) => (
            <ContractTile key={k.contract_id} k={k} active={k.contract_id === c?.contract_id}
              onClick={() => select(g.game.game_id, k.contract_id)} />
          ))}
        </div>
      </section>
      {c && <SignalPanel g={g} c={c} />}
    </>
  );
}

function GameHeader({ g }: { g: GameIntel }) {
  const s = g.state;
  const stale = g.state_age_seconds != null && g.state_age_seconds > 45;
  return (
    <section className="card">
      <div className="row between">
        <h2>{g.game.away_team} @ {g.game.home_team}</h2>
        <span className="muted small">{g.game.game_id}</span>
      </div>
      {s ? (
        <p className="state" data-testid="game-state">
          P{s.period} · {s.seconds_remaining_in_period ?? "?"}s left · {g.game.away_team} {s.away_score} – {s.home_score} {g.game.home_team}
          {" "}· skaters {s.away_skaters ?? "?"}v{s.home_skaters ?? "?"} · goalies {s.away_goalie ?? "UNKNOWN"} / {s.home_goalie ?? "UNKNOWN"}
          {s.is_final && <b> · FINAL ({s.final_decided_in})</b>}
        </p>
      ) : <p className="muted">No game state received yet.</p>}
      <div className="row wrap small">
        <span className={`banner ${stale ? "bad" : "ok"}`}>
          GAME STATE AGE {g.state_age_seconds == null ? "—" : `${Math.round(g.state_age_seconds)}s`} (replay clock {time(g.as_of)})
        </span>
        {s && s.pending_reconciliation.length > 0 && <span className="banner warn">PENDING: {s.pending_reconciliation.join(", ")}</span>}
        {s?.in_review && <span className="banner warn">VIDEO REVIEW</span>}
      </div>
      <div className="kv">
        <div><label>MODEL_FAVORED_TEAM</label><b data-testid="favored">{g.model_favored_team ?? "UNKNOWN (no prediction)"}</b></div>
        <div><label>VALUE_SIDE</label><b data-testid="value-side">{g.value_side ?? "none"}</b></div>
      </div>
      <p className="hint">The most likely winner and the value side are different questions: a likely winner can still be overpriced.</p>
    </section>
  );
}

function ContractTile({ k, active, onClick }: { k: ContractIntel; active: boolean; onClick: () => void }) {
  return (
    <button className={`tile ${active ? "active" : ""}`} onClick={onClick} data-testid={`tile-${k.contract_id}`}>
      <div className="row between"><b>{k.selection}</b><span className="muted small">{k.settlement_rule}</span></div>
      <div className="big">{pct(k.probability)}</div>
      <div className="muted small">est. win prob · band {pct(k.probability_low)}–{pct(k.probability_high)} · {k.model_status}</div>
      <div className="row between small">
        <span>ask {cents(k.best_ask)}</span>
        <span className={k.signal_eligible ? "ok-text" : ""}>{k.action ?? "—"}</span>
      </div>
      {!k.book_valid && <span className="banner bad">BOOK INVALID</span>}
    </button>
  );
}

function SignalPanel({ g, c }: { g: GameIntel; c: ContractIntel }) {
  const authed = useStore((s) => s.health?.authenticated ?? false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [qty, setQty] = useState<number | "">("");
  const keyRef = useRef<string | null>(null);  // one idempotency key per previewed signal
  const ledger = useStore((s) => s.ledger);
  const order = useMemo(
    () => ledger?.orders.find((o) => o.decision_id === preview?.decision.decision_id) ?? null,
    [ledger, preview],
  );

  useEffect(() => { setPreview(null); setErr(null); keyRef.current = null; }, [c.contract_id, g.game.game_id]);

  const doPreview = async () => {
    setBusy(true); setErr(null);
    try {
      const p = await command<Preview>("POST", "/api/paper/preview", { game_id: g.game.game_id, contract_id: c.contract_id }, previewSchema);
      setPreview(p); setQty(p.max_quantity || "");
      keyRef.current = crypto.randomUUID();
    } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };

  const doOrder = async () => {
    if (!preview || !keyRef.current) return;
    setBusy(true); setErr(null);
    try {
      await command("POST", "/api/paper/orders", {
        decision_id: preview.decision.decision_id, idempotency_key: keyRef.current,
        quantity: qty === "" ? null : qty,
      }, orderResponseSchema);
      await refreshAll();  // the tracker updates from backend ledger events
    } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };

  const ev = c.ev as any;
  const asOf = getState().health?.session.as_of;
  const expired = preview && asOf ? new Date(asOf) > new Date(preview.decision.expires_at) : false;

  return (
    <section className="card" data-testid="signal-panel">
      <h3>{c.selection} · estimate vs executable price</h3>
      <div className="grid">
        <Metric label="Estimated win probability" value={pct(c.probability)} sub={`cautious ${pct(c.probability_low)} · reliability ${c.reliability}`} />
        <Metric label="Best executable ask" value={cents(c.best_ask)} sub={`bid ${cents(c.best_bid)} · depth ${c.ask_depth ?? "—"}`} />
        <Metric label="Cautious net EV" value={ev ? usd(ev.ev_conservative) : "—"} sub={ev ? `${ev.quantity} contracts · fees ${usd(ev.expected_fees)}` : "no value estimate"} />
        <Metric label="Paper room left" value={usd(c.remaining_paper_exposure)} sub="per-team cap incl. fees" />
      </div>
      <div className={`decision ${c.signal_eligible ? "eligible" : "blocked"}`} data-testid="decision">
        <b>{c.action ?? "NO DECISION"}</b> · {c.signal_eligible ? "eligible paper signal" : "not eligible"}
        <ul className="small">{c.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        {c.abstention && <div className="muted small">model abstained: {c.abstention}</div>}
        <div className="muted small">{c.invalidation} Expires {time(c.decision_expires_at)}.</div>
      </div>

      <h3>Paper entry</h3>
      {!authed && <p className="muted">Sign in as operator to preview or place paper orders.</p>}
      {authed && (
        <div className="row wrap">
          <button disabled={busy} onClick={doPreview} data-testid="preview-btn">Preview paper entry</button>
        </div>
      )}
      {preview && (
        <div className="preview" data-testid="preview">
          <div><b>{preview.eligible ? "ELIGIBLE" : "BLOCKED"}</b> · decision {preview.decision.decision_id} · expires {time(preview.decision.expires_at)}
            {expired && <span className="banner bad">EXPIRED</span>}</div>
          <ul className="small">{preview.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
          {preview.eligible && (
            <div className="row wrap">
              <span>max {preview.max_quantity} contracts · est. cost {usd(preview.estimated_cost)} · fees {usd(preview.estimated_fees)}</span>
              <label className="small">qty <input type="number" min={1} max={preview.max_quantity} value={qty}
                onChange={(e) => setQty(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <button disabled={busy || !!order || !!expired} onClick={doOrder} data-testid="order-btn">
                {order ? "Order submitted" : "Create paper order"}
              </button>
            </div>
          )}
          <p className="muted small">A click only submits a request. The backend fills it only if every check still passes after the decision delay, at real order-book depth.</p>
        </div>
      )}
      {order && (
        <div className="order" data-testid="order-status">
          Order {order.order_id}: <b>{order.status}</b>
          {order.fill && <> · {order.fill.filled_quantity} @ {usd(Number(order.fill.cost) / order.fill.filled_quantity)} + fees {usd(order.fill.fees)}</>}
          {order.reasons.length > 0 && <span className="muted small"> · {order.reasons.join(", ")}</span>}
          {order.outcome && <> · outcome <b>{order.outcome}</b> P&amp;L {usd(order.settled_pnl)}</>}
        </div>
      )}
      {err && <div className="error" role="alert" data-testid="order-error">{err}</div>}
    </section>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="metric">
      <label>{label}</label>
      <div className="big">{value}</div>
      <div className="muted small">{sub}</div>
    </div>
  );
}
