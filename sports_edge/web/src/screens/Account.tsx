import { useEffect, useState } from "react";
import { ApiError, command } from "../api";
import { Notice } from "../components/Bits";
import { signOut } from "../components/Shell";
import { clock } from "../format";
import { go, href } from "../router";
import { identityChanged, loaders, refreshAll, useStore } from "../store";

export function AuthScreen({ mode }: { mode: "login" | "signup" }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try {
      await command("POST", mode === "login" ? "/api/account/login" : "/api/account/signup", { email, password: pw });
      setPw("");
      go("/board");  // navigate first: a later navigation by the user must win
      await identityChanged();
    } catch (x) { setErr(x instanceof ApiError ? x.detail : String(x)); }
    finally { setBusy(false); }
  };
  return (
    <section className="panel panel-pad authcard stack" data-testid={`auth-${mode}`}>
      <h1>{mode === "login" ? "Sign in" : "Create your account"}</h1>
      <p className="small muted">Analytics, explanations, alerts and paper tracking. No wagers and no real-money trading.</p>
      <form className="stack" onSubmit={submit}>
        <label className="field">Email<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
        <label className="field">Password<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "signup" ? 10 : undefined}
          value={pw} onChange={(e) => setPw(e.target.value)} required />
          {mode === "signup" && <span className="tiny muted">At least 10 characters.</span>}</label>
        <button className="primary" disabled={busy} type="submit">{mode === "login" ? "Sign in" : "Create account"}</button>
        {err && <div className="error" role="alert">{err}</div>}
      </form>
      <div className="small">{mode === "login" ? <>New here? <a href={href("/signup")}>Create an account</a></> : <>Have an account? <a href={href("/login")}>Sign in</a></>}</div>
      <div className="tiny muted">Operators sign in with the server token under <a href={href("/admin")}>Admin</a>.</div>
    </section>
  );
}

export function Settings() {
  const me = useStore((s) => s.me);
  const alerts = useStore((s) => s.alerts);
  const billing = useStore((s) => s.billing);
  useEffect(() => { loaders.billing(); loaders.alerts(); }, []);
  if (!me?.signed_in) return <AuthScreen mode="login" />;
  const prefs = me.preferences;
  const save = async (patch: any) => { await command("PUT", "/api/account/preferences", { patch }); await loaders.me(); };
  return (
    <div className="stack-lg" data-testid="settings">
      <div className="row between wrap"><div><h1>Settings</h1><span className="small muted">{me.role === "operator" ? "Operator session" : me.user?.email}</span></div>
        <button onClick={signOut}>Sign out</button></div>
      {!me.accounts_persisted && <Notice kind="caution">Accounts are not persisted on this server (database unavailable): changes last until restart.</Notice>}
      <div className="grid2">
        <section className="panel panel-pad stack">
          <h3>Preferences</h3>
          <label className="field">Sports shown
            <div className="row wrap">{["NHL", "NFL", "TENNIS", "MLB"].map((s) => (
              <button key={s} className="chip" aria-pressed={prefs.sports.includes(s)} onClick={() => save({ sports: prefs.sports.includes(s) ? prefs.sports.filter((x: string) => x !== s) : [...prefs.sports, s] })}>{s}</button>
            ))}</div></label>
          <label className="field">Timezone<input defaultValue={prefs.timezone} onBlur={(e) => e.target.value !== prefs.timezone && save({ timezone: e.target.value })} data-testid="pref-tz" /></label>
          <label className="field">Display format
            <select value={prefs.odds_format} onChange={(e) => save({ odds_format: e.target.value })} data-testid="pref-odds">
              <option value="probability">Probability (%)</option><option value="american">American odds</option>
              <option value="decimal">Decimal odds</option><option value="cents">Cents per contract</option>
            </select></label>
          <label className="row small"><input type="checkbox" checked={prefs.alerts_paused} onChange={(e) => save({ alerts_paused: e.target.checked })} /> Pause alert delivery (monitoring and settlement continue)</label>
          <label className="field">Quiet hours (your timezone)
            <div className="row">
              <input type="time" defaultValue={prefs.quiet_hours?.start ?? ""} id="qh-start" />
              <span className="muted">to</span>
              <input type="time" defaultValue={prefs.quiet_hours?.end ?? ""} id="qh-end" />
              <button className="small" onClick={() => {
                const a = (document.getElementById("qh-start") as HTMLInputElement).value, b = (document.getElementById("qh-end") as HTMLInputElement).value;
                save({ quiet_hours: a && b ? { start: a, end: b } : null });
              }}>Save</button>
            </div></label>
        </section>
        <section className="panel panel-pad stack">
          <h3>Plan</h3>
          <div className="small">Current plan: <b>{me.subscription?.plan ?? (me.role === "operator" ? "operator" : "free")}</b> · status {me.subscription?.status ?? "—"}</div>
          <div className="small dim">Included: {me.entitlements.join(", ").replace(/_/g, " ")}</div>
          <Notice>{me.plans?.note}</Notice>
          {billing && <div className="small dim">Billing: {billing.mode} · {billing.checkout} · {billing.live_charges}</div>}
          <h3 style={{ marginTop: 8 }}>Your data</h3>
          <div className="row wrap">
            <a className="btn" href="/api/account/export" download="true-edge-export.json">Export my data</a>
            {me.role === "customer" && <DeleteAccount />}
          </div>
        </section>
      </div>
      <section className="panel">
        <div className="panel-head"><h3>Alerts</h3><span className="tiny muted">Inbox delivery · email and push not configured</span></div>
        <div className="panel-pad stack">
          {!alerts?.entitled && <p className="small muted">Alert rules are part of the Pro plan. Every plan gets the same forecasts and safety checks.</p>}
          {alerts?.rules?.map((r: any) => (
            <div key={r.rule_id} className="row between small"><span><span className="tag">{r.kind.replace("_", " ")}</span> {r.scope.game_id ?? r.scope.sport ?? "all games in your sports"}{r.threshold_cents ? ` · ±${r.threshold_cents}¢` : ""}</span>
              <button className="small ghost" onClick={async () => { await command("DELETE", `/api/alerts/rules/${r.rule_id}`); loaders.alerts(); }}>Remove</button></div>
          ))}
          <h4 className="upper muted">Inbox</h4>
          {(alerts?.inbox ?? []).length === 0 && <p className="small muted">No alerts yet.</p>}
          {(alerts?.inbox ?? []).slice(0, 30).map((a: any) => (
            <div key={a.alert_id} className="ev-item" data-testid="alert-item">
              <div className="grow small"><b>{a.title}</b> <span className="muted">· {a.event}</span>
                <div className="dim">{a.reason_to_review}</div>
                <div className="tiny muted">{clock(a.created_at)} · {a.market?.ask ? `ask ${Math.round(Number(a.market.ask) * 100)}¢ · ` : ""}{a.evidence_age_s != null ? `evidence ${a.evidence_age_s}s old · ` : ""}{a.expired ? "expired" : `expires ${clock(a.expires_at)}`}
                  {!a.delivered && ` · not delivered (${a.suppressed_reason})`}</div></div>
              <span className="tag syn">{a.data_label}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function DeleteAccount() {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <button className="ghost" onClick={() => setOpen(true)}>Delete account…</button>;
  return (
    <div className="stack" style={{ width: "100%" }}>
      <label className="field">Confirm password to delete your account and its documents
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
      <div className="row"><button onClick={async () => {
        try { await command("DELETE", "/api/account", { password: pw }); await identityChanged(); go("/board"); }
        catch (x) { setErr(x instanceof ApiError ? x.detail : String(x)); }
      }} style={{ borderColor: "var(--neg)", color: "var(--neg)" }}>Delete permanently</button><button className="ghost" onClick={() => setOpen(false)}>Cancel</button></div>
      {err && <div className="error">{err}</div>}
    </div>
  );
}
