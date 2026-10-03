// Probability vs price over time, drawn only from recorded observations.
// Best ask, bid, midpoint and the size-aware entry price are separate series and are
// never interpolated between observations (step lines). Gaps are shaded, not bridged.
import { useEffect, useMemo, useRef, useState } from "react";
import { clock, pct } from "../format";

type Pt = { t: string; ask: number | null; bid: number | null; mid: number | null; entry: number | null; valid: boolean };
type M = { t: string; p: number; lo: number; hi: number; model_version: string; status: string };

const SERIES = [
  { key: "model", label: "Model estimate", color: "var(--accent-2)" },
  { key: "ask", label: "Best ask", color: "#e7edf2" },
  { key: "entry", label: "Entry price for stake", color: "#d8a748" },
  { key: "bid", label: "Best bid", color: "#7d8a97" },
  { key: "mid", label: "Midpoint", color: "#b3bfca" },
  { key: "ref", label: "Sportsbook (no-vig)", color: "#79a7f0" },
] as const;
type Key = typeof SERIES[number]["key"];

const H = 360, ML = 44, MR = 16, MT = 14, MB = 46;

export function ProbPriceChart({ hist, contractId, participant }: { hist: any; contractId: string; participant: string }) {
  const c = hist?.contracts?.find((x: any) => x.contract_id === contractId);
  const [on, setOn] = useState<Record<Key, boolean>>({ model: true, ask: true, entry: true, bid: false, mid: false, ref: true });
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const [ann, setAnn] = useState<any | null>(null);
  const [table, setTable] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // the drawing width follows the container so labels stay readable on phones
  const [W, setW] = useState(960);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(340, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [c != null]);

  const data = useMemo(() => {
    const pts: (Pt & { ts: number })[] = (c?.points ?? []).map((p: Pt) => ({ ...p, ts: Date.parse(p.t) }));
    const model: (M & { ts: number })[] = (c?.model ?? []).map((m: M) => ({ ...m, ts: Date.parse(m.t) }));
    const refs = (hist?.references ?? []).map((r: any) => ({ ...r, ts: Date.parse(r.t), v: r.fair?.[participant] }))
      .filter((r: any) => r.v != null);
    const anns = (hist?.annotations ?? []).map((a: any) => ({ ...a, ts: Date.parse(a.t) }));
    const gaps = (c?.gaps ?? []).map((g: any) => ({ ...g, a: Date.parse(g.start), b: g.end ? Date.parse(g.end) : Date.parse(hist.as_of) }));
    return { pts, model, refs, anns, gaps };
  }, [c, hist, participant]);

  if (!c) return <div className="empty"><b>No recorded prices for this contract yet</b></div>;
  const asOf = Date.parse(hist.as_of);
  const all = [...data.pts.map((p) => p.ts), ...data.model.map((m) => m.ts)];
  const t0 = zoom?.[0] ?? (all.length ? Math.min(...all) : asOf - 3600e3);
  const t1 = zoom?.[1] ?? asOf;
  const span = Math.max(1, t1 - t0);
  const x = (t: number) => ML + ((t - t0) / span) * (W - ML - MR);
  const inv = (px: number) => t0 + ((px - ML) / (W - ML - MR)) * span;
  const vis = <T extends { ts: number }>(arr: T[]) => arr.filter((p) => p.ts >= t0 && p.ts <= t1);
  const ys: number[] = [];
  const pick = (k: Key) => on[k];
  vis(data.pts).forEach((p) => { if (pick("ask") && p.ask != null) ys.push(p.ask); if (pick("bid") && p.bid != null) ys.push(p.bid); if (pick("entry") && p.entry != null) ys.push(p.entry); });
  if (pick("model")) vis(data.model).forEach((m) => ys.push(m.lo, m.hi));
  if (pick("ref")) vis(data.refs).forEach((r: any) => ys.push(r.v));
  let lo = ys.length ? Math.min(...ys) : 0.3, hi = ys.length ? Math.max(...ys) : 0.7;
  lo = Math.max(0, Math.floor((lo - 0.05) * 20) / 20); hi = Math.min(1, Math.ceil((hi + 0.05) * 20) / 20);
  if (hi - lo < 0.2) { const m = (hi + lo) / 2; lo = Math.max(0, m - 0.1); hi = Math.min(1, m + 0.1); }
  const y = (v: number) => MT + (1 - (v - lo) / (hi - lo)) * (H - MT - MB);

  const step = (rows: { ts: number; v: number | null }[]) => {
    let d = "", prev: number | null = null;
    rows.forEach((r, i) => {
      if (r.v == null) { prev = null; return; }
      const X = x(Math.max(r.ts, t0)).toFixed(1), Y = y(r.v).toFixed(1);
      if (prev == null) d += `M${X},${Y}`; else d += `H${X}V${Y}`;
      prev = r.v;
      if (i === rows.length - 1) d += `H${x(t1).toFixed(1)}`;
    });
    return d;
  };
  const before = <T extends { ts: number }>(arr: T[]) => {
    const inside = vis(arr);
    const prior = arr.filter((p) => p.ts < t0).slice(-1);
    return [...prior, ...inside];
  };
  const P = before(data.pts), MDL = before(data.model);
  const band = MDL.length ? (() => {
    let up = "", dn = "";
    MDL.forEach((m, i) => {
      const X0 = x(Math.max(m.ts, t0)), X1 = i < MDL.length - 1 ? x(MDL[i + 1].ts) : x(t1);
      up += `${i ? "L" : "M"}${X0.toFixed(1)},${y(m.hi).toFixed(1)}L${X1.toFixed(1)},${y(m.hi).toFixed(1)}`;
      dn = `L${X1.toFixed(1)},${y(m.lo).toFixed(1)}L${X0.toFixed(1)},${y(m.lo).toFixed(1)}` + dn;
    });
    return up + dn + "Z";
  })() : "";

  const ticks = W < 600 ? 4 : 6;
  const xt = Array.from({ length: ticks }, (_, i) => t0 + (span * i) / (ticks - 1));
  const yt = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
  const at = <T extends { ts: number }>(arr: T[], t: number) => { let r: T | null = null; for (const p of arr) { if (p.ts <= t) r = p; else break; } return r; };
  const hp = hover != null ? at(data.pts, hover) : null;
  const hm = hover != null ? at(data.model, hover) : null;
  const hr = hover != null ? at(data.refs as any[], hover) : null;
  const start = Date.parse(hist.scheduled_start);

  const svgX = (e: React.PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return ((e.clientX - r.left) / r.width) * W;
  };
  const visAnns = vis(data.anns);

  return (
    <div className="stack" ref={wrapRef}>
      <div className="row wrap between">
        <div className="legend" role="group" aria-label="Series">
          {SERIES.map((s) => (
            <button key={s.key} aria-pressed={on[s.key]} onClick={() => setOn({ ...on, [s.key]: !on[s.key] })}
              title={s.key === "entry" ? `Average price to buy the stated paper amount from the recorded depth` :
                s.key === "ref" ? "Sportsbook price with the margin removed. Hollow points were published before the latest game event and are not comparable." : undefined}>
              <span className="sw" style={{ background: s.color, opacity: s.key === "mid" ? 0.6 : 1 }} /> {s.label}
            </button>
          ))}
        </div>
        <div className="row">
          {zoom && <button className="small" onClick={() => setZoom(null)}>Reset zoom</button>}
          <button className="small ghost" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show data table"}</button>
        </div>
      </div>
      {table ? <DataTable pts={vis(data.pts)} model={data.model} /> : (
        <div className="chart-wrap">
          <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" data-testid="prob-price-chart"
            aria-label={`Model estimate and recorded prices for ${participant}. ${data.pts.length} recorded price points.`}
            onPointerMove={(e) => { const px = svgX(e); setHover(Math.min(t1, Math.max(t0, inv(px)))); if (drag) setDrag([drag[0], px]); }}
            onPointerLeave={() => { setHover(null); setDrag(null); }}
            onPointerDown={(e) => { if (e.pointerType === "mouse") setDrag([svgX(e), svgX(e)]); }}
            onPointerUp={() => { if (drag && Math.abs(drag[1] - drag[0]) > 12) { const a = inv(Math.min(...drag)), b = inv(Math.max(...drag)); setZoom([a, b]); } setDrag(null); }}>
            {yt.map((v) => (
              <g key={v}><line x1={ML} x2={W - MR} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth={1} />
                <text x={ML - 8} y={y(v) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{Math.round(v * 100)}</text></g>
            ))}
            <text x={8} y={MT + 4} fontSize={10.5} fill="var(--faint)">%/¢</text>
            {xt.map((t) => <text key={t} x={x(t)} y={H - MB + 16} textAnchor="middle" fontSize={11} fill="var(--muted)">{clock(new Date(t).toISOString()).slice(0, 5)}</text>)}
            {data.gaps.map((g: any, i: number) => g.b >= t0 && g.a <= t1 && (
              <g key={i}><rect x={x(Math.max(g.a, t0))} y={MT} width={Math.max(2, x(Math.min(g.b, t1)) - x(Math.max(g.a, t0)))} height={H - MT - MB} fill="rgba(226,104,94,.10)" />
                <text x={x(Math.max(g.a, t0)) + 3} y={MT + 12} fontSize={10} fill="var(--neg)">gap</text></g>
            ))}
            {start >= t0 && start <= t1 && <g><line x1={x(start)} x2={x(start)} y1={MT} y2={H - MB} stroke="var(--line-2)" strokeDasharray="3 4" />
              <text x={x(start) + 4} y={H - MB - 6} fontSize={10.5} fill="var(--muted)">Start</text></g>}
            {on.model && band && <path d={band} fill="rgba(52,195,201,.12)" />}
            {on.mid && <path d={step(P.map((p) => ({ ts: p.ts, v: p.mid })))} fill="none" stroke="#b3bfca" strokeWidth={1} strokeDasharray="4 3" opacity={0.7} />}
            {on.bid && <path d={step(P.map((p) => ({ ts: p.ts, v: p.bid })))} fill="none" stroke="#7d8a97" strokeWidth={1.2} />}
            {on.entry && <path d={step(P.map((p) => ({ ts: p.ts, v: p.entry })))} fill="none" stroke="#d8a748" strokeWidth={1.4} strokeDasharray="2 3" />}
            {on.ask && <path d={step(P.map((p) => ({ ts: p.ts, v: p.ask })))} fill="none" stroke="#e7edf2" strokeWidth={1.6} />}
            {on.model && <path d={step(MDL.map((m) => ({ ts: m.ts, v: m.p })))} fill="none" stroke="var(--accent-2)" strokeWidth={2.2} />}
            {on.ref && vis(data.refs as any[]).map((r: any, i: number) => (
              <circle key={i} cx={x(r.ts)} cy={y(r.v)} r={3.2} fill={r.compatible ? "#79a7f0" : "transparent"} stroke="#79a7f0" strokeWidth={1.3} />
            ))}
            {visAnns.map((a: any) => (
              <g key={a.id} style={{ cursor: "pointer" }} onClick={() => setAnn(a)} role="button" aria-label={`${clock(a.t)} ${a.label}`} tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setAnn(a); }}>
                <line x1={x(a.ts)} x2={x(a.ts)} y1={MT} y2={H - MB} stroke={annColor(a.kind)} strokeOpacity={0.35} strokeDasharray="2 4" />
                <circle cx={x(a.ts)} cy={H - MB + 30} r={5.5} fill={annColor(a.kind)} />
                <circle cx={x(a.ts)} cy={H - MB + 30} r={11} fill="transparent" />
              </g>
            ))}
            {drag && <rect x={Math.min(...drag)} y={MT} width={Math.abs(drag[1] - drag[0])} height={H - MT - MB} fill="rgba(52,195,201,.10)" stroke="var(--accent)" strokeDasharray="3 3" />}
            {hover != null && <line x1={x(hover)} x2={x(hover)} y1={MT} y2={H - MB} stroke="var(--text-2)" strokeOpacity={0.5} />}
          </svg>
          {hover != null && (
            <div className="chart-tip" style={{ left: `min(calc(${((x(hover) / W) * 100).toFixed(1)}% + 12px), calc(100% - 200px))`, top: 8 }}>
              <div className="tiny muted">{clock(new Date(hover).toISOString())}</div>
              {on.model && <div className="tr"><span>Model</span><b>{hm ? `${pct(hm.p, 1)} (${pct(hm.lo, 0)}–${pct(hm.hi, 0)})` : "—"}</b></div>}
              {on.ask && <div className="tr"><span>Best ask</span><b>{hp?.ask != null ? `${Math.round(hp.ask * 100)}¢` : "—"}</b></div>}
              {on.entry && <div className="tr"><span>Entry for stake</span><b>{hp?.entry != null ? `${(hp.entry * 100).toFixed(1)}¢` : "—"}</b></div>}
              {on.bid && <div className="tr"><span>Best bid</span><b>{hp?.bid != null ? `${Math.round(hp.bid * 100)}¢` : "—"}</b></div>}
              {on.mid && <div className="tr"><span>Midpoint</span><b>{hp?.mid != null ? `${(hp.mid * 100).toFixed(1)}¢` : "—"}</b></div>}
              {on.ref && hr && <div className="tr"><span>Sportsbook</span><b>{pct(hr.v, 0)}{hr.compatible ? "" : " (pre-event)"}</b></div>}
            </div>
          )}
        </div>
      )}
      {ann && (
        <div className="notice" role="dialog" aria-label="Annotation evidence">
          <div className="grow">
            <div className="row between"><b>{ann.label}</b><button className="small ghost" onClick={() => setAnn(null)}>Close</button></div>
            <div className="small muted">Event time {clock(ann.t)} · received {clock(ann.received_time)} · {ann.kind}</div>
            <pre className="caps">{JSON.stringify(ann.evidence, null, 1)}</pre>
          </div>
        </div>
      )}
      <div className="tiny muted">
        {c.total_points} recorded price changes{c.downsampled ? " (thinned for display; every observation is kept)" : ""} · drag across the chart to zoom ·
        hollow sportsbook points were published before the latest game event and are not comparable · last trade is not shown (no trade feed).
      </div>
    </div>
  );
}

function annColor(kind: string) {
  if (kind === "PAPER_FILL") return "#d8a748";
  if (kind === "FORECAST_REVISION") return "#34c3c9";
  if (["GOAL", "SCORE", "TURNOVER", "PLAY", "POINT"].includes(kind)) return "#e7edf2";
  return "#79a7f0";
}

function DataTable({ pts, model }: { pts: any[]; model: any[] }) {
  const rows = pts.slice(-200);
  const at = (t: number) => { let r: any = null; for (const m of model) { if (m.ts <= t) r = m; else break; } return r; };
  return (
    <div className="table-wrap" style={{ maxHeight: 360 }}>
      <table>
        <caption className="sr-only">Recorded prices and model estimates</caption>
        <thead><tr><th>Time (UTC)</th><th className="right">Best bid</th><th className="right">Best ask</th><th className="right">Midpoint</th><th className="right">Entry for stake</th><th className="right">Model</th></tr></thead>
        <tbody>{rows.map((p: any) => {
          const m = at(p.ts);
          return (<tr key={p.t}><td>{clock(p.t)}</td><td className="right">{p.bid != null ? Math.round(p.bid * 100) + "¢" : "—"}</td>
            <td className="right">{p.ask != null ? Math.round(p.ask * 100) + "¢" : "—"}</td><td className="right">{p.mid != null ? (p.mid * 100).toFixed(1) + "¢" : "—"}</td>
            <td className="right">{p.entry != null ? (p.entry * 100).toFixed(1) + "¢" : "—"}</td><td className="right">{m ? pct(m.p, 1) : "—"}</td></tr>);
        })}</tbody>
      </table>
    </div>
  );
}
