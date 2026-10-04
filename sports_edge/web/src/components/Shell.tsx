import { useEffect, useState, type ReactNode } from "react";
import { command } from "../api";
import { clock } from "../format";
import { href, type Route } from "../router";
import { identityChanged, setMode, useStore } from "../store";
import {
  IconBoard, IconChart, IconFlask, IconGear, IconShield, IconStar, IconUser, IconWallet, Mark,
} from "./Icons";

const NAV: { key: Route["name"]; label: string; path: string; icon: () => JSX.Element }[] = [
  { key: "board", label: "Live Board", path: "/board", icon: IconBoard },
  { key: "watchlist", label: "Watchlist", path: "/watchlist", icon: () => <IconStar /> },
  { key: "portfolio", label: "Paper Portfolio", path: "/portfolio", icon: IconWallet },
  { key: "performance", label: "Performance", path: "/performance", icon: IconChart },
  { key: "research", label: "Research", path: "/research", icon: IconFlask },
];

function active(route: Route, key: string) {
  return route.name === key || (key === "board" && route.name === "game");
}

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const me = useStore((s) => s.me);
  const isOperator = me?.role === "operator";
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href={href("/board")} aria-label="True Edge home">
            <Mark />
            <span>True Edge <small className="hide-sm">BY ODDS BRAIN</small></span>
          </a>
          <nav className="mainnav" aria-label="Main">
            {NAV.map((n) => (
              <a key={n.key} href={href(n.path)} aria-current={active(route, n.key) ? "page" : undefined}>{n.label}</a>
            ))}
          </nav>
          <div className="topbar-right">
            <StreamDot />
            {isOperator && (
              <a className="btn ghost hide-sm" href={href("/admin")} aria-current={route.name === "admin" ? "page" : undefined}>
                <IconShield /> Admin
              </a>
            )}
            <AccountButton route={route} />
          </div>
        </div>
      </header>
      <ModeRibbon />
      <main className="content" id="main">{children}</main>
      <nav className="bottomnav" aria-label="Main">
        {NAV.map((n) => (
          <a key={n.key} href={href(n.path)} aria-current={active(route, n.key) ? "page" : undefined}>
            <n.icon /> <span>{n.label.replace("Paper ", "")}</span>
          </a>
        ))}
      </nav>
    </div>
  );
}

function AccountButton({ route }: { route: Route }) {
  const me = useStore((s) => s.me);
  if (!me?.signed_in) {
    return <a className="btn" href={href("/login")} data-testid="signin-link"><IconUser /> <span className="hide-sm">Sign in</span></a>;
  }
  return (
    <a className="btn ghost" href={href("/settings")} aria-current={route.name === "settings" ? "page" : undefined}
      data-testid="account-link" title="Account and settings">
      <IconGear /> <span className="hide-sm truncate" style={{ maxWidth: 180, minWidth: 0 }}>
        {me.role === "operator" ? "Operator" : me.user?.email}
      </span>
    </a>
  );
}

function StreamDot() {
  const stream = useStore((s) => s.stream);
  const last = useStore((s) => s.lastEventAt);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const age = last ? Math.round((Date.now() - last) / 1000) : null;
  const color = stream === "CONNECTED" ? "var(--pos)" : stream === "RECONNECTING" ? "var(--warn)" : "var(--neg)";
  return (
    <span className="row tiny muted" data-testid="stream-status"
      title="Connection from this page to the True Edge server. It is not a data provider status.">
      <span style={{ width: 7, height: 7, borderRadius: 7, background: color, display: "inline-block" }} />
      <span className="hide-sm">Updates {stream.toLowerCase()}{age != null && stream === "CONNECTED" ? ` · ${age}s` : ""}</span>
    </span>
  );
}

/** Persistent, unmistakable label for the data the page is showing. */
export function ModeRibbon() {
  const mode = useStore((s) => s.mode);
  const health = useStore((s) => s.health);
  const banners = health?.banners ?? [];
  const label = health?.session?.data_label;
  if (mode === "live") {
    return (
      <div className="ribbon live" data-testid="mode-ribbon">
        <div className="ribbon-inner">
          <b>LIVE DATA MODE</b><span className="sep">·</span>
          <span>Only configured live sources are shown. Nothing is filled in from demos.</span>
          <span className="sep">·</span><span>Paper only: no real-money execution exists</span>
          <span style={{ marginLeft: "auto" }}><button className="small ghost" onClick={() => setMode("demo")}>Open demo slate</button></span>
        </div>
      </div>
    );
  }
  return (
    <div className="ribbon" data-testid="mode-ribbon" role="status">
      <div className="ribbon-inner">
        <b>DEMO · {label === "SYNTHETIC" ? "SYNTHETIC DATA" : `${label ?? ""} REPLAY`}</b>
        <span className="sep">·</span>
        <span>Replay clock {clock(health?.session?.as_of)}</span>
        <span className="sep">·</span>
        <span>{banners.some((b) => b.startsWith("MECHANICS")) ? "Mechanics demo: signals are not evidence of value" :
          banners.find((b) => b.startsWith("UNVALIDATED") || b === "NO MODEL LOADED") ?? "Experimental"}</span>
        <span className="sep hide-sm">·</span><span className="hide-sm">Fictional teams and players</span>
        <span style={{ marginLeft: "auto" }}><button className="small ghost" onClick={() => setMode("live")} data-testid="go-live">Switch to live data</button></span>
      </div>
    </div>
  );
}

export async function signOut() {
  try { await command("POST", "/api/account/logout"); } catch { /* ignore */ }
  try { await command("POST", "/api/auth/logout"); } catch { /* ignore */ }
  await identityChanged();
}
