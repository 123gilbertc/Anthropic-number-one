// Internal Research / Replay workspace (the former dashboard lives here now) and the
// operator Admin area. Customers see read-only research views; session control,
// provider credentials and discovery runs are operator-only on the server.
import { useEffect, useState } from "react";
import { ApiError, command, time } from "../api";
import { Notice } from "../components/Bits";
import { IconPause, IconPlay, IconStep } from "../components/Icons";
import { href } from "../router";
import { identityChanged, loaders, refreshAll, setMode, useStore } from "../store";
import { Audit } from "./Audit";
import { Connections } from "./Connections";
import { Evaluation } from "./Evaluation";
import { GameScreen, GamesList } from "./Games";
import { Tracker } from "./Tracker";

const NO_BANNERS: string[] = [];  // stable reference: a fresh [] per read would loop forever

const RTABS: [string, string][] = [["replay", "Replay control"], ["inspector", "Replay inspector"], ["tracker", "Run ledger"],
  ["evaluation", "Run evaluation"], ["audit", "Audit"], ["coverage", "Coverage registry"]];

export function Research({ tab = "replay" }: { tab?: string }) {
  const t = RTABS.some(([k]) => k === tab) ? tab : "replay";
  return (
    <div className="stack-lg" data-testid="research">
      <div><h1>Research</h1><span className="small muted">Replay workspace, audit trail and coverage. Everything here is labelled with its data source.</span></div>
      <div className="tabs" role="tablist">
        {RTABS.map(([k, l]) => <a key={k} href={href(`/research/${k}`)} role="tab" aria-selected={t === k}
          className="btn ghost" style={{ borderRadius: 0, borderBottom: t === k ? "2px solid var(--accent)" : "2px solid transparent", color: t === k ? "var(--text)" : "var(--muted)" }}>{l}</a>)}
      </div>
      {t === "replay" && <ReplayControl />}
      {t === "inspector" && <><GamesList onOpen={() => { /* same screen */ }} /><GameScreen /></>}
      {t === "tracker" && <Tracker />}
      {t === "evaluation" && <Evaluation />}
      {t === "audit" && <Audit />}
      {t === "coverage" && <CoverageRegistry />}
    </div>
  );
}

function ReplayControl() {
  const s = useStore((x) => x.health?.session);
  const banners = useStore((x) => x.health?.banners) ?? NO_BANNERS;
  const me = useStore((x) => x.me);
  const op = me?.role === "operator";
  const [speed, setSpeed] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!s) return <p className="muted">Loading…</p>;
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await refreshAll(); } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };
  return (
    <section className="panel panel-pad stack" data-testid="replay-control">
      <div className="row wrap between">
        <div className="small" data-testid="session-info">{s.mode} · {s.data_label} · run {s.run_id} · {s.position}/{s.total} · as of {time(s.as_of)}
          {s.finished ? " · FINISHED" : s.running ? " · RUNNING" : " · PAUSED"}</div>
        <button className="small ghost" onClick={() => setMode("demo")}>Show on Live Board (demo mode)</button>
      </div>
      <div className="row wrap" data-testid="banners">{banners.map((b) => <span key={b} className={`banner ${/MECHANICS|NOT PERSISTED/.test(b) ? "bad" : "warn"}`}>{b}</span>)}</div>
      {!op && <Notice>Replay control is for operators. <a href={href("/admin")}>Operator sign-in</a></Notice>}
      {op && (
        <div className="row wrap">
          <button disabled={busy} onClick={() => run(() => command("POST", "/api/session", { fixture: "slate_synthetic.jsonl", mode: "mechanics" }))}>Load multi-sport demo slate</button>
          <button disabled={busy} onClick={() => run(() => command("POST", "/api/session", { fixture: "nhl_synthetic_dip.jsonl", mode: "honest" }))}>New NHL replay (no model)</button>
          <button disabled={busy} onClick={() => run(() => command("POST", "/api/session", { fixture: "nhl_synthetic_dip.jsonl", mode: "mechanics" }))}>New NHL mechanics demo</button>
          {s.running
            ? <button disabled={busy} onClick={() => run(() => command("POST", "/api/session/pause"))}><IconPause /> Pause</button>
            : <button className="primary" disabled={busy || s.finished} onClick={() => run(() => command("POST", "/api/session/run", { speed }))} data-testid="play"><IconPlay /> Play</button>}
          <button disabled={busy || s.running || s.finished} onClick={() => run(() => command("POST", "/api/session/step", { n: 50 }))} data-testid="step"><IconStep /> Step +50</button>
          <label className="small muted row">speed
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[10, 30, 60, 120, 300, 600].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
        </div>
      )}
      {err && <div className="error" role="alert">{err}</div>}
      <p className="tiny muted">A replay runs on its own clock and only shows what had been received by that time. Replay and demo records never mix with live paper records.</p>
    </section>
  );
}

function CoverageRegistry() {
  const cov = useStore((s) => s.coverage);
  useEffect(() => { loaders.coverage(); }, []);
  if (!cov) return <p className="muted">Loading…</p>;
  const dims = ["schedule", "live_score", "play_level", "history", "market", "licensing", "model"];
  const cls = (st: string) => ({ AVAILABLE: "ok", FIXTURE_ONLY: "watch", NOT_CONFIGURED: "unavailable", BLOCKED: "bad", UNKNOWN: "none", NOT_MODELLED: "none" }[st] ?? "none");
  return (
    <section className="panel" data-testid="coverage-registry">
      <div className="table-wrap"><table>
        <thead><tr><th>Competition</th>{dims.map((d) => <th key={d}>{d.replace("_", " ")}</th>)}</tr></thead>
        <tbody>{cov.competitions.map((c: any) => (
          <tr key={c.competition}><td><b>{c.name}</b><div className="tiny muted">{c.competition}</div></td>
            {dims.map((d) => <td key={d} title={`${c.cells[d].note} [${c.cells[d].label}]`}><span className={`pill ${cls(c.cells[d].status)}`}>{c.cells[d].status.replace("_", " ").toLowerCase()}</span>
              <div className="tiny faint" style={{ maxWidth: 160 }}>{c.cells[d].label}</div></td>)}</tr>
        ))}</tbody></table></div>
      <div className="panel-pad tiny muted">{cov.note} Hover a cell for the note behind it.</div>
    </section>
  );
}

export function Admin({ tab }: { tab?: string }) {
  const me = useStore((s) => s.me);
  const cov = useStore((s) => s.coverage);
  const [busy, setBusy] = useState(false);
  useEffect(() => { loaders.coverage(); }, []);
  if (me?.role !== "operator") return <OperatorLogin />;
  return (
    <div className="stack-lg" data-testid="admin">
      <div className="row between wrap"><div><h1>Admin</h1><span className="small muted">Operator: shared licensed feeds, credentials and diagnostics. Customers never enter provider keys.</span></div>
        <button onClick={async () => { await command("POST", "/api/auth/logout"); await identityChanged(); }}>Sign out operator</button></div>
      <section className="panel">
        <div className="panel-head"><h3>Discovery sources</h3>
          <button className="small" disabled={busy} onClick={async () => { setBusy(true); try { await command("POST", "/api/discovery/run"); await loaders.coverage(); } finally { setBusy(false); } }} data-testid="run-discovery">Run discovery now</button></div>
        <div className="table-wrap"><table data-testid="discovery-table">
          <thead><tr><th>Source</th><th>Sport</th><th>Kind</th><th>Status</th><th>Checked</th><th>Detail</th></tr></thead>
          <tbody>{(cov?.discovery ?? []).map((r: any) => (
            <tr key={r.source}><td className="small">{r.source}</td><td>{r.sport}</td><td className="small">{r.kind}</td>
              <td><span className={`pill ${r.status === "OK" ? "ok" : r.status === "FAILED" ? "bad" : "unavailable"}`}>{r.status}</span></td>
              <td className="num small">{time(r.checked_at)}</td><td className="small dim">{r.error ?? JSON.stringify(r.detail)}</td></tr>
          ))}</tbody></table></div>
        {(cov?.discovery ?? []).length === 0 && <div className="panel-pad small muted">Discovery has not run yet in this server process. Run it now to see real statuses.</div>}
      </section>
      {(tab === undefined || tab === "connections") && <Connections />}
    </div>
  );
}

function OperatorLogin() {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <section className="panel panel-pad authcard stack">
      <h1>Operator sign-in</h1>
      <p className="small muted">Operators configure shared provider feeds and run the replay workspace. Customers do not need this.</p>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try { await command("POST", "/api/auth/login", { token }); setToken(""); await identityChanged(); }
        catch (x) { setErr(x instanceof ApiError ? x.detail : String(x)); }
      }}>
        <label className="field">Operator token<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} aria-label="Operator token" /></label>
        <button className="primary" type="submit">Sign in</button>
        {err && <div className="error">{err}</div>}
      </form>
    </section>
  );
}
