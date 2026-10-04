import { useState } from "react";
import { ApiError, command, time } from "../api";
import { refreshAll, useStore } from "../store";

const CLS: Record<string, string> = {
  CONNECTED: "ok", CONFIGURED_UNTESTED: "warn", TEST_STALE: "warn", NOT_CONFIGURED: "warn",
  FAILED: "bad", BLOCKED: "bad", DISCONNECTED: "warn",
};

export function Connections() {
  const c = useStore((s) => s.connections);
  const authed = useStore((s) => s.health?.authenticated ?? false);
  if (!c) return <p className="muted">Loading…</p>;
  return (
    <>
      <section className="card">
        <h3>Readiness</h3>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Capability</th><th>Status</th><th>Blocked by</th></tr></thead>
            <tbody>
              {c.readiness.map((r: any) => (
                <tr key={r.capability} data-testid={`ready-${r.capability}`}>
                  <td>{r.capability}<div className="muted small">{r.note}</div></td>
                  <td><span className={`banner ${r.ready ? "ok" : "bad"}`}>{r.ready ? "READY" : "BLOCKED"}</span></td>
                  <td className="small">{r.blocked_by.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {c.providers.map((p: any) => <ProviderCard key={p.id} p={p} authed={authed} />)}
      <p className="muted small">
        Provider documentation last checked {c.docs_checked}. A saved key is not a working connection:
        status becomes CONNECTED only after a real test succeeds, and expires after 15 minutes.
        Secrets stay on the server and are never sent back to the browser.
      </p>
    </>
  );
}

function ProviderCard({ p, authed }: { p: any; authed: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await refreshAll(); }
    catch (x) { setErr(x instanceof ApiError ? `${x.code}: ${x.detail}` : String(x)); }
    finally { setBusy(false); }
  };
  const lt = p.last_test;
  return (
    <section className="card" data-testid={`provider-${p.id}`}>
      <div className="row between">
        <h3>{p.name} {p.optional && <span className="muted small">(optional)</span>}</h3>
        <span className={`banner ${CLS[p.status] ?? "warn"}`} data-testid={`status-${p.id}`}>{p.status}</span>
      </div>
      <p className="muted small">{p.role}</p>
      {p.blocked_reason && <p className="bad-text small">{p.blocked_reason}</p>}
      {p.secrets.length > 0 && (
        <div className="small">
          {p.secrets.map((s: any) => (
            <div key={s.name} className="row wrap">
              <code>{s.name}</code>
              <span className="muted">{s.configured ? `configured (${s.source}${s.hint ? `, ${s.hint}` : ""})` : "not set"}</span>
              {authed && (
                <>
                  <input type="password" autoComplete="off" placeholder="new value" aria-label={`${s.name} value`}
                    value={vals[s.name] ?? ""} onChange={(e) => setVals({ ...vals, [s.name]: e.target.value })} />
                  <button disabled={busy || !vals[s.name]} onClick={() => run(async () => {
                    await command("PUT", `/api/connections/${p.id}/secret`, { name: s.name, value: vals[s.name] });
                    setVals({ ...vals, [s.name]: "" });
                  })}>Save</button>
                  {s.configured && s.source !== "environment" && (
                    <button disabled={busy} onClick={() => run(() => command("DELETE", `/api/connections/${p.id}/secret/${s.name}`))}>Remove</button>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {lt && (
        <div className="small" data-testid={`test-${p.id}`}>
          Last test {time(lt.time)} ({Math.round(lt.age_seconds)}s ago, {lt.latency_ms} ms): <b className={lt.ok ? "ok-text" : "bad-text"}>{lt.ok ? "OK" : "FAILED"}</b> — {lt.detail}
          {Object.keys(lt.capabilities || {}).length > 0 && <pre className="caps">{JSON.stringify(lt.capabilities, null, 1)}</pre>}
        </div>
      )}
      {authed && !p.blocked_reason && (
        <div className="row">
          <button disabled={busy || p.status === "NOT_CONFIGURED"} onClick={() => run(() => command("POST", `/api/connections/${p.id}/test`))}
            data-testid={`test-btn-${p.id}`}>{busy ? "Testing…" : "Test connection"}</button>
          <button disabled={busy || p.status === "DISCONNECTED"} onClick={() => run(() => command("POST", `/api/connections/${p.id}/disconnect`))}>Disconnect</button>
        </div>
      )}
      {err && <div className="error" role="alert">{err}</div>}
    </section>
  );
}
