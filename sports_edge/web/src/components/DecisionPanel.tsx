// The decision panel: exact contract, model probability, size-aware executable price,
// fees, net EV for the stated paper amount, readiness and last valid update.
// Primary actions: Follow, Explain, Set alert, Preview paper entry. There is no Buy button.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, command, orderResponseSchema, previewSchema, type Preview } from "../api";
import { clock, pct, priceAs, probAs, usd, type OddsFormat } from "../format";
import { href } from "../router";
import { getState, loaders, refreshAll, useStore } from "../store";
import type { ContractView, Workspace } from "../types";
import { Check, ModelTag, StatePill } from "./Bits";
import { IconBell, IconEye, IconExplain, IconStar } from "./Icons";

export function DecisionPanel({ ws, selected, onSelect, onExplain }: {
  ws: Workspace; selected: ContractView | null; onSelect: (cid: string) => void; onExplain: () => void;
}) {
  const me = useStore((s) => s.me);
  const fmt: OddsFormat = me?.preferences?.odds_format ?? "probability";
  const [alertOpen, setAlertOpen] = useState(false);
  const watching = (me?.watchlist ?? []).some((w: any) => w.kind === "game" && w.id === ws.event.game_id);
  const c = selected;
  return (
    <section className="panel decision" data-testid="decision-panel">
      <div className="panel-head"><h3>Decision</h3>{c && <StatePill s={c.state} />}</div>
      <div className="panel-pad stack">
        <div className="sidepick" role="group" aria-label="Contract">
          {ws.contracts.map((x) => (
            <button key={x.contract_id} aria-pressed={x.contract_id === c?.contract_id} onClick={() => onSelect(x.contract_id)}
              data-testid={`side-${x.participant}`}>
              <span className="small" style={{ fontWeight: 650 }}>{x.name}</span>
              <span className="tiny muted">{x.market.ask ? `ask ${Math.round(Number(x.market.ask) * 100)}¢` : "no price"} · {x.model ? pct(x.model.probability) : "no estimate"}</span>
            </button>
          ))}
        </div>
        {!c && <p className="muted small">No market is mapped to this game, so there is nothing to price.</p>}
        {c && (
          <>
            <div className="tiny muted">{c.settlement_text} <span className="faint">({c.venue} · {c.contract_id})</span></div>
            <dl className="dk" style={{ margin: 0 }}>
              <dt>Model probability</dt>
              <dd data-testid="dp-model">{c.model ? <>{probAs(c.model.probability, fmt, c.model.fair_odds)} <span className="muted tiny">cautious {pct(c.model.low)}</span></> : "unavailable"}</dd>
              {c.model && c.model.void_probability > 0 && <><dt>Refund (tie) probability</dt><dd>{pct(c.model.void_probability, 1)}</dd></>}
              <dt>Best ask now</dt>
              <dd>{priceAs(c.market.ask, fmt, c.market.ask_formats)} <span className="muted tiny">{c.market.ask_size ?? "—"} available</span></dd>
              <dt>Entry price for ${c.entry?.stake ?? "25"}</dt>
              <dd data-testid="dp-entry">{c.entry ? <>{(Number(c.entry.average_price) * 100).toFixed(1)}¢ <span className="muted tiny">× {c.entry.quantity}</span></> : "—"}</dd>
              <dt>Fees (expected)</dt><dd>{c.ev ? usd(c.ev.expected_fees) : c.entry ? usd(c.entry.entry_fees) : "—"}</dd>
              <dt>Net expected value</dt>
              <dd data-testid="dp-ev">{c.ev ? <span className={Number(c.ev.ev_point) >= 0 ? "pos" : "neg"}>{usd(c.ev.ev_point, true)}</span> : "—"}</dd>
              <dt>…with cautious estimate</dt>
              <dd>{c.ev ? <span className={Number(c.ev.ev_conservative) >= 0 ? "pos" : "neg"}>{usd(c.ev.ev_conservative, true)}</span> : "—"}</dd>
              <dt>Edge vs all-in price</dt><dd>{c.ev ? `${c.ev.edge_probability_points >= 0 ? "+" : "−"}${Math.abs(c.ev.edge_probability_points).toFixed(1)} pts` : "—"}</dd>
            </dl>
            {c.ev && <p className="small dim" style={{ margin: 0 }} data-testid="ev-plain">{c.ev.plain_english}</p>}
            {!c.ev && c.abstention && <p className="small muted" style={{ margin: 0 }}>Model abstained: {c.abstention}</p>}
            <div className="row wrap" style={{ gap: 6 }}><ModelTag status={c.model?.status} />
              {c.break_even_ask && <span className="tag" title="Highest ask at which the cautious estimate still clears costs and the margin">Break-even {Math.round(Number(c.break_even_ask) * 100)}¢</span>}</div>
            <details>
              <summary className="small" style={{ cursor: "pointer" }}>Readiness checks · {c.readiness.filter((r) => r.ok).length}/{c.readiness.length} pass</summary>
              <ul className="checklist" style={{ marginTop: 8 }} data-testid="readiness">
                {c.readiness.map((r) => <li key={r.check}><Check ok={r.ok} /><span><b style={{ fontWeight: 550 }}>{r.check}</b> <span className="muted">· {r.detail}</span></span></li>)}
              </ul>
            </details>
            {c.state === "WATCH" && c.reasons.length === 0 && (
              <p className="small dim" style={{ margin: 0 }}>Watching: no price dip or other trigger right now, so no value test is running.</p>
            )}
            {c.reasons.length > 0 && (
              <ul className="checklist small">
                {c.reasons.map((r) => <li key={r.code}><span className="tag">{r.code.replace(/_/g, " ").toLowerCase()}</span><span className="dim">{r.text}</span></li>)}
              </ul>
            )}
            <div className="tiny muted">Last valid update {clock(c.decision_time)} · {c.expires_at ? `expires ${clock(c.expires_at)}` : "no decision"}</div>
          </>
        )}
        <div className="actions">
          <FollowButton gameId={ws.event.game_id} watching={watching} />
          <button onClick={onExplain} data-testid="explain-btn"><IconExplain /> Explain</button>
          <button onClick={() => setAlertOpen(true)} data-testid="alert-btn"><IconBell /> Set alert</button>
          {c && <PaperEntry ws={ws} c={c} />}
        </div>
        <p className="tiny muted" style={{ margin: 0 }}>Paper only. Nothing here places a real-money order; a preview is never a fill.</p>
      </div>
      {alertOpen && <AlertDialog ws={ws} onClose={() => setAlertOpen(false)} />}
    </section>
  );
}

function FollowButton({ gameId, watching }: { gameId: string; watching: boolean }) {
  const me = useStore((s) => s.me);
  const [busy, setBusy] = useState(false);
  if (!me?.signed_in) return <a className="btn" href={href("/login")}><IconStar /> Follow</a>;
  const toggle = async () => {
    setBusy(true);
    const items = (me.watchlist ?? []).filter((w: any) => !(w.kind === "game" && w.id === gameId));
    if (!watching) items.push({ kind: "game", id: gameId });
    try { await command("PUT", "/api/account/watchlist", { items }); await loaders.me(); } finally { setBusy(false); }
  };
  return <button aria-pressed={watching} disabled={busy} onClick={toggle} data-testid="follow-btn"><IconStar filled={watching} /> {watching ? "Following" : "Follow"}</button>;
}

function PaperEntry({ ws, c }: { ws: Workspace; c: ContractView }) {
  const me = useStore((s) => s.me);
  const portfolio = useStore((s) => s.portfolio);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const keyRef = useRef<string | null>(null);  // one idempotency key per previewed signal
  const current = useRef(c.contract_id);
  current.current = c.contract_id;
  const order = useMemo(() => portfolio?.orders?.find((o: any) => o.decision_id === preview?.decision.decision_id) ?? null,
    [portfolio, preview]);
  useEffect(() => { setPreview(null); setErr(null); setOpen(false); keyRef.current = null; }, [c.contract_id, ws.event.game_id]);

  if (!me?.signed_in) return <a className="btn primary" href={href("/login")} data-testid="preview-btn"><IconEye /> Preview paper entry</a>;

  const doPreview = async () => {
    const asked = { gameId: ws.event.game_id, contractId: c.contract_id };
    setBusy(true); setErr(null); setOpen(true);
    try {
      const p = await command<Preview>("POST", "/api/paper/preview", { game_id: asked.gameId, contract_id: asked.contractId }, previewSchema);
      // a slow response for a game or contract the user has since left is discarded
      const i = getState().interest;
      if (i.gameId !== asked.gameId || current.current !== asked.contractId
          || p.decision.contract_id !== asked.contractId) return;
      setPreview(p);
      keyRef.current = crypto.randomUUID();
    } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };
  const doOrder = async () => {
    if (!preview || !keyRef.current || busy) return;
    setBusy(true); setErr(null);
    try {
      await command("POST", "/api/paper/orders", {
        decision_id: preview.decision.decision_id, idempotency_key: keyRef.current,
        expected_contract_id: c.contract_id, quantity: null,
      }, orderResponseSchema);
      await refreshAll();
    } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };
  const asOf = getState().health?.session.as_of;
  const expired = preview && asOf ? new Date(asOf) > new Date(preview.decision.expires_at) : false;
  return (
    <>
      <button className="primary" disabled={busy} onClick={doPreview} data-testid="preview-btn"><IconEye /> Preview paper entry</button>
      {open && (
        <div style={{ gridColumn: "1 / -1" }} className="notice" data-testid="preview">
          <div className="grow stack" style={{ gap: 8 }}>
            {!preview && !err && <span className="muted small">Re-checking state, price, model, fees, cash and exposure…</span>}
            {preview && (
              <>
                <div className="row between"><b>{preview.eligible ? "Eligible for a paper entry" : "Not eligible right now"}</b>
                  {expired && <span className="pill bad">Expired</span>}</div>
                <ul className="checklist small">{preview.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
                {preview.eligible && (
                  <div className="small">{preview.max_quantity} contracts · est. cost {usd(preview.estimated_cost)} · fees {usd(preview.estimated_fees)} · expires {clock(preview.decision.expires_at)}</div>
                )}
                {preview.eligible && (
                  <button className="primary" disabled={busy || !!order || !!expired} onClick={doOrder} data-testid="order-btn">
                    {order ? "Paper order submitted" : "Create paper order"}
                  </button>
                )}
                <span className="tiny muted">The server fills a paper order only if every check still passes after the decision delay, at recorded depth.</span>
              </>
            )}
            {order && (
              <div className="small" data-testid="order-status">Order {order.order_id}: <b>{order.status}</b>
                {order.fill && <> · {order.fill.filled_quantity} @ {(Number(order.fill.cost) / order.fill.filled_quantity * 100).toFixed(1)}¢ + fees {usd(order.fill.fees)}</>}
                {order.outcome && <> · {order.outcome} {usd(order.settled_pnl, true)}</>}</div>
            )}
            {err && <div className="error" role="alert" data-testid="order-error">{err}</div>}
          </div>
        </div>
      )}
    </>
  );
}

function AlertDialog({ ws, onClose }: { ws: Workspace; onClose: () => void }) {
  const me = useStore((s) => s.me);
  const [kind, setKind] = useState("value_candidate");
  const [th, setTh] = useState(5);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const entitled = (me?.entitlements ?? []).includes("alerts");
  const save = async () => {
    setErr(null);
    try {
      await command("POST", "/api/alerts/rules", { rule: { kind, scope: { game_id: ws.event.game_id }, ...(kind === "price_move" ? { threshold_cents: th } : {}) } });
      setMsg("Alert saved. You will see it in your inbox when it triggers.");
      loaders.alerts();
    } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
  };
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Set alert" data-testid="alert-dialog">
        <div className="drawer-head"><h3>Set an alert for this game</h3><button className="ghost small" onClick={onClose}>Close</button></div>
        <div className="drawer-body" style={{ gap: 12 }}>
          {!me?.signed_in && <p className="small">Sign in to create alerts. <a href={href("/login")}>Sign in</a></p>}
          {me?.signed_in && !entitled && <p className="small">Alerts are part of the Pro plan. Every plan gets the same forecasts and safety checks.</p>}
          <label className="field">Notify me when
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="value_candidate">a paper signal passes every check</option>
              <option value="price_move">the best ask moves by at least…</option>
              <option value="game_start">game data starts arriving</option>
              <option value="final">the final result arrives</option>
            </select>
          </label>
          {kind === "price_move" && <label className="field">Move threshold (cents)
            <input type="number" min={2} max={50} value={th} onChange={(e) => setTh(Number(e.target.value))} /></label>}
          <p className="tiny muted">Alerts explain what changed and why it may be worth a look. They never announce a winner or place an order. Delivery: in-app inbox (email and push are not configured).</p>
          <button className="primary" disabled={!me?.signed_in || !entitled} onClick={save}>Save alert</button>
          {msg && <div className="small ok-text">{msg}</div>}
          {err && <div className="error">{err}</div>}
        </div>
      </div>
    </>
  );
}
