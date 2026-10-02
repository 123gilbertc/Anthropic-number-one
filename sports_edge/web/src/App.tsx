import { useCallback, useEffect, useState } from "react";
import { Json, cents, get, pct, post, time, usd } from "./api";

type Tab = "monitor" | "alerts" | "positions" | "sources" | "audit" | "models";

export function App() {
  const [tab, setTab] = useState<Tab>("monitor");
  const [health, setHealth] = useState<Json | null>(null);
  const [games, setGames] = useState<Json[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHealth(await get("/api/health"));
      setGames(await get("/api/games"));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const replay = async (mode: "honest" | "mechanics") => {
    setBusy(true);
    try {
      await post("/api/replay", { mode });
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>Sports Edge · research &amp; paper only</h1>
        <div className="banners">
          {err && <span className="banner bad">API NOT CONNECTED</span>}
          {health?.banners.map((b: string) => (
            <span key={b} className={`banner ${b.includes("MECHANICS") ? "bad" : "warn"}`}>
              {b}
            </span>
          ))}
        </div>
        <div className="controls">
          <span className="muted">Replay:</span>
          <button disabled={busy} onClick={() => replay("honest")}>Honest (no model)</button>
          <button disabled={busy} onClick={() => replay("mechanics")}>Mechanics demo</button>
          {health && <span className="muted">as of {time(health.as_of)} (replay clock)</span>}
        </div>
        <nav>
          {(["monitor", "alerts", "positions", "sources", "audit", "models"] as Tab[]).map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "monitor" && games.map((g) => <GameCard key={g.game.game_id} g={g} />)}
        {tab === "alerts" && <Alerts />}
        {tab === "positions" && <Positions />}
        {tab === "sources" && <Sources />}
        {tab === "audit" && <Audit />}
        {tab === "models" && <Models />}
      </main>
    </div>
  );
}

function GameCard({ g }: { g: Json }) {
  const s = g.state;
  return (
    <section className="card">
      <div className="row between">
        <h2>
          {g.game.away_team} @ {g.game.home_team}
        </h2>
        <span className="muted">{g.game.game_id}</span>
      </div>
      {s ? (
        <p className="state">
          P{s.period} · {s.seconds_remaining_in_period ?? "?"}s left · {g.game.away_team}{" "}
          {s.away_score} – {s.home_score} {g.game.home_team} · skaters {s.away_skaters ?? "?"}v
          {s.home_skaters ?? "?"} · goalies {s.away_goalie ?? "UNKNOWN"} / {s.home_goalie ?? "UNKNOWN"}
          {s.is_final && <b> · FINAL ({s.final_decided_in})</b>}
          {s.pending_reconciliation.length > 0 && (
            <span className="banner warn">PENDING: {s.pending_reconciliation.join(", ")}</span>
          )}
        </p>
      ) : (
        <p className="muted">No game state received.</p>
      )}
      <div className="kv">
        <div>
          <label>MODEL_FAVORED_TEAM</label>
          <b>{g.model_favored_team ?? "UNKNOWN (no prediction)"}</b>
        </div>
        <div>
          <label>VALUE_SIDE</label>
          <b>{g.value_side ?? "none"}</b>
        </div>
      </div>
      <p className="hint">
        The most likely winner and the value side are different questions: a likely winner can
        still be overpriced.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Contract</th>
              <th>Est. win prob (band)</th>
              <th>Reliability</th>
              <th>Ask / bid</th>
              <th>Depth</th>
              <th>Ref age</th>
              <th>Cons. EV</th>
              <th>Action</th>
              <th>Room left</th>
            </tr>
          </thead>
          <tbody>
            {g.contracts.map((c: Json) => (
              <tr key={c.contract_id}>
                <td>
                  {c.selection}
                  <div className="muted small">{c.settlement_rule}</div>
                </td>
                <td>
                  {pct(c.probability)}
                  <div className="muted small">
                    {pct(c.probability_low)}–{pct(c.probability_high)} · {c.model_status}
                  </div>
                  {c.abstention && <div className="muted small">abstain: {c.abstention}</div>}
                </td>
                <td>{c.reliability}</td>
                <td>
                  {cents(c.best_ask)} / {cents(c.best_bid)}
                  {!c.book_valid && <div className="banner bad">BOOK INVALID</div>}
                </td>
                <td>{c.ask_depth ?? "—"}</td>
                <td>{c.reference_age_seconds == null ? "none" : `${c.reference_age_seconds.toFixed(0)}s`}</td>
                <td>{c.ev ? usd(c.ev.ev_conservative) : "—"}</td>
                <td>
                  <b>{c.action ?? "—"}</b>
                  <div className="muted small">{c.reasons.join(", ")}</div>
                </td>
                <td>{usd(c.remaining_paper_exposure)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Invalidation conditions &amp; notes</summary>
        {g.contracts.map((c: Json) => (
          <p key={c.contract_id} className="small">
            <b>{c.selection}:</b> {c.invalidation} Expires {time(c.decision_expires_at)}.{" "}
            {c.notes.join(" · ")}
          </p>
        ))}
      </details>
    </section>
  );
}

function useData<T = Json>(path: string) {
  const [d, setD] = useState<T | null>(null);
  useEffect(() => {
    get<T>(path).then(setD).catch(() => setD(null));
  }, [path]);
  return d;
}

function Alerts() {
  const a = useData<Json[]>("/api/alerts");
  if (!a) return <p className="muted">Loading…</p>;
  if (!a.length) return <p className="muted">No alerts. None are manufactured without a validated model and an authorized feed.</p>;
  return (
    <section className="card">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>As of</th><th>Expires</th><th>Contract</th><th>Action</th><th>Qty</th>
              <th>Cons. EV</th><th>Max eligible add</th><th>State ID</th><th>Fill</th>
            </tr>
          </thead>
          <tbody>
            {a.map(({ decision: d, fill, fill_outcome }) => (
              <tr key={d.decision_id}>
                <td>{time(d.decision_time)}</td>
                <td>{time(d.expires_at)}</td>
                <td>{d.contract_id}</td>
                <td>{d.action}<div className="muted small">{d.reasons.join(", ")}</div></td>
                <td>{d.planned_quantity}</td>
                <td>{d.ev ? usd(d.ev.ev_conservative) : "—"}</td>
                <td>{usd(d.max_eligible_addition)}</td>
                <td className="small mono">{d.snapshot_id}</td>
                <td>
                  {fill ? `${fill.filled_quantity} @ ${usd(Number(fill.cost) / fill.filled_quantity)}` : "no fill"}
                  <div className="muted small">{fill_outcome.join(", ")}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Positions() {
  const p = useData("/api/positions");
  if (!p) return <p className="muted">Loading…</p>;
  return (
    <section className="card">
      <p>Free cash {usd(p.cash)} · open cost {usd(p.open_cost)} · limits: {p.limits.label}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Contract</th><th>Contracts</th><th>Total cost</th><th>Fees</th><th>Avg entry</th><th>Avg all-in</th></tr>
          </thead>
          <tbody>
            {p.positions.map((x: Json) => (
              <tr key={x.contract_id}>
                <td>{x.contract_id}</td><td>{x.contracts}</td><td>{usd(x.total_cost)}</td>
                <td>{usd(x.total_fees)}</td><td>{cents(x.average_entry)}</td><td>{cents(x.average_entry_all_in)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Settlements</h3>
      {p.settlements.map((s: Json) => (
        <p key={s.settlement_id} className="small">{s.contract_id}: <b>{s.outcome}</b> — {s.detail}</p>
      ))}
    </section>
  );
}

function Sources() {
  const s = useData("/api/sources");
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <section className="card">
      <h3>Provider registry (docs checked {s.checked})</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Source</th><th>Role</th><th>Docs</th><th>Entitlement</th><th>Live use</th></tr></thead>
          <tbody>
            {s.registry.map((r: Json) => (
              <tr key={r.name}>
                <td>{r.name}<div className="muted small">{r.notes}</div></td>
                <td>{r.role}</td><td>{r.doc_status}</td><td>{r.entitlement}</td>
                <td><span className={`banner ${r.live_use === "BLOCKED" ? "bad" : "warn"}`}>{r.live_use}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Runtime health (this replay)</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Source</th><th>Status</th><th>Msgs</th><th>Gaps</th><th>Dups</th><th>No provider time</th><th>Latency p50/p95</th></tr></thead>
          <tbody>
            {s.runtime.map((h: Json) => (
              <tr key={h.name}>
                <td>{h.name}</td><td>{h.status}</td><td>{h.messages}</td><td>{h.gaps}</td><td>{h.duplicates}</td>
                <td>{h.missing_provider_time}</td>
                <td>{h.latency_ms_p50 == null ? "—" : `${h.latency_ms_p50.toFixed(0)} / ${h.latency_ms_p95.toFixed(0)} ms`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Audit() {
  const d = useData<Json[]>("/api/decisions");
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <section className="card">
      <p className="muted">Every change of action and every approval, taken or rejected.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Contract</th><th>Action</th><th>Reasons</th><th>Model</th></tr></thead>
          <tbody>
            {d.map((x) => (
              <tr key={x.decision_id}>
                <td>{time(x.decision_time)}</td><td>{x.contract_id}</td><td>{x.action}</td>
                <td className="small">{x.reasons.join(", ")}</td><td className="small">{x.model_version ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Models() {
  const m = useData("/api/models");
  if (!m) return <p className="muted">Loading…</p>;
  return (
    <section className="card">
      <p className="muted">{m.note}</p>
      {m.active ? (
        <>
          <p>
            <b>{m.active.model_version}</b> · status <span className="banner warn">{m.active.status}</span> ·
            trained on {m.active.trained_on}
          </p>
          <p className="small">
            Test Brier {m.test_metrics.brier.value.toFixed(4)} (95% CI {m.test_metrics.brier.low.toFixed(4)}–
            {m.test_metrics.brier.high.toFixed(4)}, {m.test_metrics.brier.n_clusters} games) · log loss{" "}
            {m.test_metrics.log_loss.value.toFixed(4)}. Lower is better; always guessing 50% scores 0.25 / 0.693.
          </p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Phase</th><th>Rows</th><th>Games</th><th>Brier</th><th>Log loss</th><th>Accuracy</th></tr></thead>
              <tbody>
                {m.test_metrics.by_phase.map((r: Json) => (
                  <tr key={r.phase}>
                    <td>{r.phase}</td><td>{r.n}</td><td>{r.games}</td><td>{r.brier.toFixed(4)}</td>
                    <td>{r.log_loss.toFixed(4)}</td><td>{pct(r.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p>No model loaded. No forecasts, no value alerts.</p>
      )}
    </section>
  );
}
