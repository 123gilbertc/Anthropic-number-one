import type { ReactNode } from "react";
import type { BoardLabel, ContractState } from "../types";
import { IconAlert, IconCheck, IconInfo, IconX } from "./Icons";

const LABEL_CLASS: Record<string, string> = {
  "VALUE CANDIDATE": "value", "PAPER ENTRY ELIGIBLE": "eligible", WATCH: "watch",
  "NO CURRENT EDGE": "none", "NO ADD": "none", HOLD: "none", UNAVAILABLE: "unavailable",
  "DATA UNAVAILABLE": "unavailable", FINAL: "final",
};
const LABEL_TEXT: Record<string, string> = {
  "VALUE CANDIDATE": "Value candidate", "PAPER ENTRY ELIGIBLE": "Paper entry eligible", WATCH: "Watch",
  "NO CURRENT EDGE": "No current edge", "NO ADD": "No add", HOLD: "Hold", UNAVAILABLE: "Unavailable",
  "DATA UNAVAILABLE": "Data unavailable", FINAL: "Final",
};

export function StatePill({ s }: { s: BoardLabel | ContractState | string }) {
  return <span className={`pill ${LABEL_CLASS[s] ?? "none"}`} data-testid="state-pill">{LABEL_TEXT[s] ?? s}</span>;
}

export function ModelTag({ status }: { status?: string | null }) {
  if (!status || status === "NONE") return <span className="tag">No model</span>;
  if (status === "VALIDATED") return <span className="tag" style={{ color: "var(--pos)", borderColor: "rgba(73,185,133,.4)" }}>Validated</span>;
  if (status === "SYNTHETIC_ONLY") return <span className="tag syn" title="Fitted or parameterised on synthetic data only">Synthetic-only model</span>;
  if (status === "UNVALIDATED") return <span className="tag syn">Experimental model</span>;
  return <span className="tag">{status.replace(/_/g, " ").toLowerCase()}</span>;
}

export function LivePill({ status }: { status: string }) {
  if (status === "LIVE") return <span className="pill live"><span className="dot" />Live</span>;
  if (status === "FINAL") return <span className="pill final">Final</span>;
  if (status === "UPCOMING" || status === "SCHEDULED") return <span className="pill info">Upcoming</span>;
  if (status === "AWAITING DATA") return <span className="pill watch">Awaiting data</span>;
  return <span className="pill none">{status}</span>;
}

export function Notice({ kind = "info", children }: { kind?: "info" | "caution"; children: ReactNode }) {
  return <div className={`notice ${kind === "caution" ? "caution" : ""}`}>{kind === "caution" ? <IconAlert /> : <IconInfo />}<div>{children}</div></div>;
}

export function Check({ ok }: { ok: boolean | null }) {
  if (ok == null) return <span style={{ color: "var(--muted)" }}><IconInfo /></span>;
  return ok ? <span style={{ color: "var(--pos)" }}><IconCheck /></span> : <span style={{ color: "var(--neg)" }}><IconX /></span>;
}

export function Avatar({ name, large }: { name: string; large?: boolean }) {
  // Text-based fallback: no team or player imagery is used without rights.
  const parts = name.replace(/[^A-Za-z .\/-]/g, "").split(/[\s/]+/).filter(Boolean);
  const ini = parts.length >= 2 ? (parts[0][0] + parts[parts.length - 1][0]) : name.slice(0, 2);
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return (
    <span className={`avatar ${large ? "lg" : ""}`} aria-hidden
      style={{ background: `hsl(${h} 22% 18%)`, borderColor: `hsl(${h} 25% 28%)`, color: `hsl(${h} 35% 78%)` }}>
      {ini.toUpperCase()}
    </span>
  );
}

export function Sparkline({ values, width = 120, height = 30, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length < 2) return <span className="tiny faint">No recorded prices</span>;
  const lo = Math.min(...values), hi = Math.max(...values);
  const pad = 2;
  const y = (v: number) => (hi === lo ? height / 2 : pad + (1 - (v - lo) / (hi - lo)) * (height - pad * 2));
  const x = (i: number) => (i / (values.length - 1)) * (width - 2) + 1;
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg className="spark" width={width} height={height} role="img"
      aria-label={`${label}: recorded best ask from ${Math.round(values[0] * 100)}¢ to ${Math.round(values[values.length - 1] * 100)}¢`}>
      <path d={d} fill="none" stroke={up ? "var(--accent-2)" : "var(--text-2)"} strokeWidth={1.5} />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={2.2} fill={up ? "var(--accent-2)" : "var(--text-2)"} />
    </svg>
  );
}

export function Kpi({ label, value, sub, testid }: { label: string; value: ReactNode; sub?: ReactNode; testid?: string }) {
  return (
    <div className="kpi" data-testid={testid}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return <div className="empty">{icon}<b>{title}</b>{children && <div className="small muted" style={{ maxWidth: 520 }}>{children}</div>}</div>;
}
