import { usd } from "../api";
import { useStore } from "../store";

export function Evaluation() {
  const e = useStore((s) => s.evaluation);
  const model = useStore((s) => s.health?.session.model) as any;
  if (!e) return <p className="muted">Loading…</p>;
  const wr = e.filled_win_rate;
  return (
    <>
      <section className="card">
        {e.warning && <p className="banner warn">{e.warning}</p>}
        <div className="grid">
          <div className="metric"><label>Signals produced</label><div className="big">{e.signals}</div></div>
          <div className="metric"><label>Orders (rejected)</label><div className="big">{e.orders} ({e.rejected_orders})</div></div>
          <div className="metric"><label>Filled / settled</label><div className="big">{e.filled_orders} / {e.settled_orders}</div></div>
          <div className="metric"><label>Settled P&amp;L</label><div className="big">{usd(e.settled_pnl)}</div>
            <div className="muted small">on {usd(e.total_spend_all_in)} spent · ROI {e.roi_on_spend_pct ?? "—"}%</div></div>
        </div>
        <p>
          Filled-position win rate:{" "}
          {wr.status === "UNKNOWN" ? <b>UNKNOWN (no settled filled positions)</b>
            : <b>{(wr.win_rate * 100).toFixed(0)}% of {wr.n} (95% CI {(wr.ci95[0] * 100).toFixed(0)}–{(wr.ci95[1] * 100).toFixed(0)}%)</b>}
        </p>
        <p className="muted small">Win rates and P&amp;L from one replay say nothing about real markets. Rates for subsets with no data show UNKNOWN rather than borrowing another group's rate.</p>
      </section>
      <section className="card">
        <h3>Model</h3>
        {model ? (
          <>
            <p><b>{model.model_version}</b> · status <span className="banner warn">{model.status}</span> · trained on {model.trained_on}</p>
            {model.test_metrics && (
              <p className="small">Test Brier {model.test_metrics.brier.value.toFixed(4)} (95% CI {model.test_metrics.brier.low.toFixed(4)}–{model.test_metrics.brier.high.toFixed(4)}, {model.test_metrics.brier.n_clusters} games). Lower is better; always guessing 50% scores 0.25.</p>
            )}
          </>
        ) : <p>No model loaded: no forecasts and no value signals.</p>}
      </section>
    </>
  );
}
