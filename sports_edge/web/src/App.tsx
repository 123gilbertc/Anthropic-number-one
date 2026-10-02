import { useEffect, useState } from "react";
import { ApiError, command, time } from "./api";
import { connectStream, refreshAll, useStore } from "./store";
import { Connections } from "./screens/Connections";
import { GameScreen, GamesList } from "./screens/Games";
import { Tracker } from "./screens/Tracker";
import { Evaluation } from "./screens/Evaluation";
import { Audit } from "./screens/Audit";

type Tab = "connections" | "games" | "game" | "tracker" | "evaluation" | "audit";
const TABS: [Tab, string][] = [
  ["connections", "Connections"], ["games", "Games"], ["game", "Game"],
  ["tracker", "Paper tracker"], ["evaluation", "Evaluation"], ["audit", "Audit"],
];

export function App() {
  const [tab, setTab] = useState<Tab>(() => (sessionStorage.getItem("se.tab") as Tab) || "game");
  useEffect(() => { connectStream(); }, []);
  useEffect(() => { sessionStorage.setItem("se.tab", tab); }, [tab]);
  const health = useStore((s) => s.health);
  const errors = useStore((s) => s.errors);

  return (
    <div className="app">
      <header>
        <div className="row between">
          <h1>Sports Edge <span className="muted small">research · paper only</span></h1>
          <StreamBadge />
        </div>
        <div className="banners" data-testid="banners">
          {health?.banners.map((b) => (
            <span key={b} className={`banner ${/MECHANICS|NOT PERSISTED/.test(b) ? "bad" : "warn"}`}>{b}</span>
          ))}
        </div>
        <div className="toolbar">
          <Login />
          <SessionControls />
        </div>
        {Object.keys(errors).length > 0 && (
          <div className="error" role="alert">
            {Object.entries(errors).map(([k, v]) => <div key={k}>{k}: {v}</div>)}
          </div>
        )}
        <nav>
          {TABS.map(([t, label]) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{label}</button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "connections" && <Connections />}
        {tab === "games" && <GamesList onOpen={() => setTab("game")} />}
        {tab === "game" && <GameScreen />}
        {tab === "tracker" && <Tracker />}
        {tab === "evaluation" && <Evaluation />}
        {tab === "audit" && <Audit />}
      </main>
    </div>
  );
}

function StreamBadge() {
  const stream = useStore((s) => s.stream);
  const last = useStore((s) => s.lastEventAt);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const age = last ? Math.round((Date.now() - last) / 1000) : null;
  const cls = stream === "CONNECTED" ? "ok" : stream === "RECONNECTING" ? "warn" : "bad";
  return (
    <span className={`banner ${cls}`} data-testid="stream-status" title="Backend event stream (not a data provider)">
      UPDATES: {stream}{age != null ? ` · last event ${age}s ago` : ""}
    </span>
  );
}

function Login() {
  const authed = useStore((s) => s.health?.authenticated ?? false);
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (authed) {
    return (
      <span className="row">
        <span className="banner ok">OPERATOR SIGNED IN</span>
        <button onClick={async () => { await command("POST", "/api/auth/logout"); refreshAll(); }}>Sign out</button>
      </span>
    );
  }
  return (
    <form className="row" onSubmit={async (e) => {
      e.preventDefault();
      setErr(null);
      try {
        await command("POST", "/api/auth/login", { token });
        setToken("");  // the token is exchanged for an HttpOnly cookie and not kept
        refreshAll();
      } catch (x) { setErr(x instanceof ApiError ? x.detail : String(x)); }
    }}>
      <input type="password" placeholder="Operator token" value={token} aria-label="Operator token"
        onChange={(e) => setToken(e.target.value)} autoComplete="off" />
      <button type="submit">Sign in</button>
      {err && <span className="bad-text small">{err}</span>}
    </form>
  );
}

function SessionControls() {
  const s = useStore((x) => x.health?.session);
  const authed = useStore((x) => x.health?.authenticated ?? false);
  const [speed, setSpeed] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!s) return null;
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await refreshAll(); } catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };
  return (
    <span className="row wrap">
      <span className="muted small" data-testid="session-info">
        {s.mode} · {s.data_label} · run {s.run_id} · {s.position}/{s.total} · as of {time(s.as_of)}
        {s.finished ? " · FINISHED" : s.running ? " · RUNNING" : " · PAUSED"}
      </span>
      {authed && (
        <>
          <button disabled={busy} onClick={() => run(() => command("POST", "/api/session", { mode: "honest" }))}>New replay (no model)</button>
          <button disabled={busy} onClick={() => run(() => command("POST", "/api/session", { mode: "mechanics" }))}>New mechanics demo</button>
          {s.running
            ? <button disabled={busy} onClick={() => run(() => command("POST", "/api/session/pause"))}>Pause</button>
            : <button disabled={busy || s.finished} onClick={() => run(() => command("POST", "/api/session/run", { speed }))}>Play</button>}
          <button disabled={busy || s.running || s.finished} onClick={() => run(() => command("POST", "/api/session/step", { n: 10 }))}>Step +10</button>
          <label className="small muted">speed
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {[10, 30, 60, 120, 300].map((v) => <option key={v} value={v}>{v}×</option>)}
            </select>
          </label>
        </>
      )}
      {err && <span className="bad-text small">{err}</span>}
    </span>
  );
}
