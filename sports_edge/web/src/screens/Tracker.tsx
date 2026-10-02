import { cents, time, usd } from "../api";
import { useStore } from "../store";

export function Tracker() {
  const l = useStore((s) => s.ledger);
  const session = useStore((s) => s.health?.session);
  if (!l) return <p className="muted">Loading…</p>;
  const positions = new Map<string, { qty: number; cost: number; fees: number }>();
  for (const f of l.fills) {
    const p = positions.get(f.contract_id) ?? { qty: 0, cost: 0, fees: 0 };
    p.qty += f.filled_quantity; p.cost += Number(f.cost); p.fees += Number(f.fees);
    positions.set(f.contract_id, p);
  }
  return (
    <>
      <section className="card">
        <p>
          <span className="banner warn">{session?.mode} · {session?.data_label} · run {session?.run_id}</span>{" "}
          Records from this run are isolated from any live paper ledger.
        </p>
        <p>Free cash {usd(l.cash)} · open cost {usd(l.open_cost)} · limits: {l.limits.label}</p>
        <h3>Positions</h3>
        {positions.size === 0 ? <p className="muted">No filled paper positions.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Contract</th><th>Contracts</th><th>Avg entry (qty-weighted)</th><th>All-in avg</th><th>Fees</th></tr></thead>
            <tbody>
              {[...positions].map(([cid, p]) => (
                <tr key={cid}><td>{cid}</td><td>{p.qty}</td><td>{cents(String(p.cost / p.qty))}</td>
                  <td>{cents(String((p.cost + p.fees) / p.qty))}</td><td>{usd(p.fees)}</td></tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
      <section className="card">
        <h3>Orders</h3>
        {l.orders.length === 0 ? <p className="muted">No paper orders yet.</p> : (
          <div className="table-wrap"><table data-testid="orders-table">
            <thead><tr><th>Created</th><th>Order</th><th>Contract</th><th>Req.</th><th>Status</th><th>Fill</th><th>Outcome</th></tr></thead>
            <tbody>
              {l.orders.map((o) => (
                <tr key={o.order_id}>
                  <td>{time(o.created_time)}</td><td className="mono small">{o.order_id}</td><td>{o.contract_id}</td>
                  <td>{o.requested_quantity}</td><td><b>{o.status}</b><div className="muted small">{o.reasons.join(", ")}</div></td>
                  <td>{o.fill ? `${o.fill.filled_quantity} · ${usd(o.fill.cost)} + ${usd(o.fill.fees)}` : "—"}</td>
                  <td>{o.outcome ?? "open"} {o.settled_pnl != null && `(${usd(o.settled_pnl)})`}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>
      <section className="card">
        <h3>Ledger events</h3>
        <ol className="small" data-testid="ledger-events">
          {l.events.map((e) => (
            <li key={e.ledger_seq}>{time(e.time)} <b>{e.kind}</b> {e.order_id} {e.contract_id} <span className="muted">{JSON.stringify(e.detail)}</span></li>
          ))}
        </ol>
      </section>
    </>
  );
}
