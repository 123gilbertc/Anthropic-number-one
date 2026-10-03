import { Empty, Kpi, Notice } from "../components/Bits";
import { IconWallet } from "../components/Icons";
import { clock, usd } from "../format";
import { href } from "../router";
import { useEffect } from "react";
import { loaders, useStore } from "../store";

export function Portfolio() {
  const p = useStore((s) => s.portfolio);
  const me = useStore((s) => s.me);
  useEffect(() => { loaders.portfolio(); }, []);
  if (!me?.signed_in) {
    return <div className="stack-lg"><h1>Paper Portfolio</h1>
      <section className="panel"><Empty icon={<IconWallet />} title="Sign in to keep a paper portfolio">
        Each account has its own paper cash, exposure and results. <a href={href("/login")}>Sign in</a> or <a href={href("/signup")}>create an account</a>.
      </Empty></section></div>;
  }
  if (!p || !p.signed_in) return <p className="muted">Loading…</p>;
  const realized = Number(p.realized), unreal = Number(p.unrealized);
  return (
    <div className="stack-lg" data-testid="portfolio">
      <div className="row between wrap"><div><h1>Paper Portfolio</h1>
        <span className="small muted">{p.mode} · {p.data_label} · simulated fills only</span></div>
        <span className="tag syn">{p.data_label === "SYNTHETIC" ? "Demo records: kept apart from live paper records" : p.mode}</span></div>
      <section className="panel panel-pad">
        <div className="grid4">
          <Kpi label="Paper cash" value={usd(p.cash)} sub={`bankroll ${usd(p.limits.bankroll)}`} testid="kpi-cash" />
          <Kpi label="Reserved for pending" value={usd(p.reserved_pending)} sub={`${p.pending.length} pending order(s)`} />
          <Kpi label="Open exposure" value={usd(p.open_cost)} sub={`fees paid ${usd(p.fees_paid)}`} />
          <Kpi label="Results" value={<span className={realized >= 0 ? "pos" : "neg"}>{usd(realized, true)}</span>}
            sub={<>realized · <span className={unreal >= 0 ? "pos" : "neg"}>{usd(unreal, true)}</span> unrealized</>} />
        </div>
      </section>
      <section className="panel">
        <div className="panel-head"><h3>Open positions</h3><span className="tiny muted">{p.unrealized_note}</span></div>
        {p.positions.length === 0 ? <Empty title="No open paper positions" /> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Contract</th><th className="right">Contracts</th><th className="right">Avg entry</th><th className="right">All-in</th><th className="right">Cost + fees</th><th className="right">Bid now</th><th className="right">Unrealized</th></tr></thead>
            <tbody>{p.positions.map((x: any) => (
              <tr key={x.contract_id}><td><a href={href(`/game/${encodeURIComponent(x.game_id)}`)}>{x.name}</a><div className="tiny muted">{x.sport} · {x.contract_id}</div></td>
                <td className="right">{x.contracts}</td><td className="right">{(Number(x.average_entry) * 100).toFixed(1)}¢</td>
                <td className="right">{(Number(x.average_entry_all_in) * 100).toFixed(1)}¢</td>
                <td className="right">{usd(Number(x.cost) + Number(x.fees))}</td>
                <td className="right">{x.mark_bid ? `${Math.round(Number(x.mark_bid) * 100)}¢` : "—"}</td>
                <td className={`right ${Number(x.unrealized) >= 0 ? "pos" : "neg"}`}>{x.unrealized == null ? "—" : usd(x.unrealized, true)}</td></tr>
            ))}</tbody></table></div>
        )}
      </section>
      <section className="panel">
        <div className="panel-head"><h3>Paper orders</h3></div>
        {p.orders.length === 0 ? <Empty title="No paper orders yet">Preview a paper entry from a game workspace to create one.</Empty> : (
          <div className="table-wrap"><table data-testid="orders-table">
            <thead><tr><th>Created</th><th>Contract</th><th>Status</th><th className="right">Filled</th><th className="right">Cost</th><th className="right">Fees</th><th>Outcome</th><th className="right">P&amp;L</th></tr></thead>
            <tbody>{p.orders.slice().reverse().map((o: any) => (
              <tr key={o.order_id}><td className="num">{clock(o.created_time)}</td><td>{o.selection_team}<div className="tiny muted">{o.contract_id}</div></td>
                <td><span className="tag">{o.status}</span>{o.reasons?.length > 0 && <div className="tiny muted">{o.reasons.join(", ")}</div>}</td>
                <td className="right">{o.fill ? `${o.fill.filled_quantity}/${o.requested_quantity}` : `0/${o.requested_quantity}`}</td>
                <td className="right">{o.fill ? usd(o.fill.cost) : "—"}</td><td className="right">{o.fill ? usd(o.fill.fees) : "—"}</td>
                <td>{o.outcome ?? "—"}</td><td className={`right ${Number(o.settled_pnl) >= 0 ? "pos" : "neg"}`}>{o.settled_pnl != null ? usd(o.settled_pnl, true) : "—"}</td></tr>
            ))}</tbody></table></div>
        )}
      </section>
      <Notice>{p.rules.join(" ")} Pausing alerts never pauses monitoring or settlement.</Notice>
    </div>
  );
}
