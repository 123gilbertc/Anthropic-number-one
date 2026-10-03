import { useEffect, useState } from "react";
import { get } from "../api";
import { Kpi, ModelTag, Notice } from "../components/Bits";
import { SPORT_LABEL, usd } from "../format";
import { useStore } from "../store";

// Forecasting quality and paper results are separate questions, shown separately.
export function Performance() {
  const ev = useStore((s) => s.evaluation);
  const me = useStore((s) => s.me);
  const [models, setModels] = useState<any>(null);
  useEffect(() => { get("/api/models").then(setModels).catch(() => setModels(null)); }, []);
  const ws = useStore((s) => Object.values(s.workspaces)[0]);
  const cards: any[] = (ws as any)?.model_cards ?? [];
  const wr = ev?.filled_win_rate;
  return (
    <div className="stack-lg" data-testid="performance">
      <div><h1>Performance</h1><span className="small muted">Forecast quality and paper results, never blended into one score</span></div>
      <section className="panel">
        <div className="panel-head"><h3>Forecasting evidence by sport</h3><span className="pill watch">Insufficient evidence</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th>Sport</th><th>Model</th><th>Status</th><th>Out-of-sample evidence</th><th>Prospective forecasts</th></tr></thead>
          <tbody>{(["NHL", "NFL", "TENNIS", "MLB"] as const).map((sp) => {
            const c = cards.find((x) => x.sport === sp);
            return (<tr key={sp}><td><b>{SPORT_LABEL[sp]}</b></td><td className="small">{c ? `${c.model_version} · ${c.kind}` : "—"}</td>
              <td><ModelTag status={c?.status} /></td><td className="small dim">None on real data. Synthetic test metrics only exercise the pipeline.</td>
              <td className="small dim">None recorded</td></tr>);
          })}</tbody></table></div>
        <div className="panel-pad"><Notice kind="caution">No accuracy, Brier score or hit rate on real games exists yet, so none is shown. Claims of predictive skill need licensed history, chronological holdouts, and prospectively timestamped forecasts over enough independent games.</Notice></div>
      </section>
      {models?.active?.test_metrics && (
        <section className="panel panel-pad stack">
          <h3>NHL pipeline check (synthetic test split)</h3>
          <div className="grid3">
            <Kpi label="Brier" value={models.active.test_metrics.brier.value?.toFixed?.(4) ?? "—"} sub="synthetic games" />
            <Kpi label="Log loss" value={models.active.test_metrics.log_loss.value?.toFixed?.(4) ?? "—"} sub="synthetic games" />
            <Kpi label="Test games" value={models.active.test_metrics.test_games} sub="invented" />
          </div>
          <p className="tiny muted">{models.note}</p>
        </section>
      )}
      <section className="panel panel-pad stack">
        <h3>Your paper results {me?.signed_in ? "" : "(sign in to see yours)"}</h3>
        {ev && (
          <div className="grid4">
            <Kpi label="Signals (system)" value={ev.signals} sub="all gates passed" />
            <Kpi label="Your orders" value={ev.orders} sub={`${ev.rejected_orders} rejected at fill`} />
            <Kpi label="Settled P&L" value={usd(ev.settled_pnl, true)} sub={`on ${usd(ev.total_spend_all_in)} spent`} />
            <Kpi label="Filled win rate" value={wr?.status === "UNKNOWN" || wr?.n === 0 ? "UNKNOWN" : `${Math.round((wr?.win_rate ?? 0) * 100)}%`} sub={wr?.n ? `of ${wr.n} settled` : "no settled orders"} />
          </div>
        )}
        <p className="tiny muted">{ev?.warning ?? ""} A higher win rate on easy favourites is not evidence of a better model, and results from filled entries do not transfer to every forecast.</p>
      </section>
    </div>
  );
}
