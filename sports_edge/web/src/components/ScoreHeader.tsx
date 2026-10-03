// Sport-specific scoreboards. Display only: every value comes from the backend scoreboard.
import { Avatar, LivePill } from "./Bits";
import type { EventInfo } from "../types";

export function ScoreHeader({ ev, sb }: { ev: EventInfo; sb: any }) {
  const away = ev.names[ev.away] ?? ev.away;
  const home = ev.names[ev.home] ?? ev.home;
  if (ev.sport === "TENNIS") return <TennisHeader ev={ev} sb={sb} />;
  const score: [number, number] | null = sb?.score ?? null;
  const lead = score ? (score[0] > score[1] ? 0 : score[1] > score[0] ? 1 : -1) : -1;
  return (
    <div className="scorehead" data-testid="scoreboard">
      <div className="side">
        <Avatar name={away} large />
        <div className="grow"><div className="tname truncate">{away}</div><div className="tsub">Away</div></div>
        <div className="score" style={{ color: lead === 1 ? "var(--text-2)" : undefined }}>{score ? score[0] : ""}</div>
      </div>
      <div className="mid">
        <LivePill status={sb?.status === "SCHEDULED" ? "UPCOMING" : (sb?.status ?? ev.status)} />
        <SportClock ev={ev} sb={sb} />
      </div>
      <div className="side right">
        <div className="score" style={{ color: lead === 0 ? "var(--text-2)" : undefined }}>{score ? score[1] : ""}</div>
        <div className="grow"><div className="tname truncate">{home}</div><div className="tsub">Home</div></div>
        <Avatar name={home} large />
      </div>
    </div>
  );
}

function SportClock({ ev, sb }: { ev: EventInfo; sb: any }) {
  if (!sb || sb.status === "SCHEDULED") {
    return <span className="clock">{new Date(ev.scheduled_start).toUTCString().slice(17, 22)} UTC start</span>;
  }
  if (ev.sport === "NHL") {
    const sk = sb.skaters ?? [];
    const strength = sk[0] != null && sk[1] != null && sk[0] !== sk[1] ? `${sk[0]}v${sk[1]}` : "Even strength";
    const en = (sb.net_empty ?? []).some(Boolean) ? " · empty net" : "";
    return <span className="clock">{sb.status === "FINAL" ? `Final${sb.decided_in && sb.decided_in !== "REG" ? ` (${sb.decided_in})` : ""}` :
      `P${sb.period} · ${sb.clock}`}<br /><span className="tiny muted">{strength}{en}</span></span>;
  }
  if (ev.sport === "NFL") {
    return <span className="clock">{sb.status === "FINAL" ? (sb.tie ? "Final · tie" : "Final") : `${sb.period} · ${sb.clock}`}
      {sb.down_distance && <><br /><span className="tiny muted">{ev.names[sb.possession] ?? sb.possession} ball · {sb.down_distance}</span></>}</span>;
  }
  if (ev.sport === "MLB") {
    return (
      <span className="clock row" style={{ gap: 10 }}>
        <span>{sb.status === "FINAL" ? "Final" : `${sb.half === "TOP" ? "▲" : "▼"} ${sb.inning}`}</span>
        {sb.status !== "FINAL" && <><Bases on={sb.runners ?? [false, false, false]} />
          <span className="outs" aria-label={`${sb.outs} outs`}>{[0, 1, 2].map((i) => <i key={i} className={i < sb.outs ? "on" : ""} />)}</span></>}
      </span>
    );
  }
  return null;
}

function Bases({ on }: { on: boolean[] }) {
  const sq = (x: number, y: number, filled: boolean, k: string) => (
    <rect key={k} x={x} y={y} width={11} height={11} transform={`rotate(45 ${x + 5.5} ${y + 5.5})`}
      fill={filled ? "var(--warn)" : "transparent"} stroke="var(--muted)" strokeWidth={1.3} />
  );
  return (
    <svg className="diamond" viewBox="0 0 46 46" role="img" aria-label={`Runners: ${["1st", "2nd", "3rd"].filter((_, i) => on[i]).join(", ") || "none"}`}>
      {sq(28, 17, on[0], "1")}{sq(17.5, 6, on[1], "2")}{sq(7, 17, on[2], "3")}
    </svg>
  );
}

function TennisHeader({ ev, sb }: { ev: EventInfo; sb: any }) {
  const p = [ev.names[ev.home] ?? ev.home, ev.names[ev.away] ?? ev.away];
  const sets: [number, number][] = sb?.sets ?? [];
  const games: [number, number] = sb?.games ?? [0, 0];
  const pts: string = sb?.points ?? "";
  const live = sb?.status === "LIVE";
  const ptsParts = pts.includes("-") ? pts.split("-") : [pts, ""];
  return (
    <div className="scorehead" data-testid="scoreboard" style={{ gridTemplateColumns: "1fr auto" }}>
      <table className="tennis-table" aria-label="Match score">
        <tbody>
          {[0, 1].map((i) => (
            <tr key={i}>
              <td style={{ paddingLeft: 0 }}>
                <span className="row" style={{ gap: 10 }}>
                  <Avatar name={p[i]} />
                  <b style={{ fontSize: 16 }}>{p[i]}</b>
                  {live && sb.server === (i === 0 ? "P1" : "P2") && <span className="srv" title="Serving" aria-label="serving" />}
                  {sb?.winner === (i === 0 ? "P1" : "P2") && <span className="pill ok">Winner</span>}
                </span>
              </td>
              {sets.map((s, k) => <td key={k} style={{ color: s[i] > s[1 - i] ? "var(--text)" : "var(--muted)" }}>{s[i]}</td>)}
              {live && <td className="cur">{games[i]}</td>}
              {live && <td className="pts">{pts.startsWith("Ad") || pts === "Deuce" ? (i === 0 ? pts : "") : ptsParts[i]}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mid">
        <LivePill status={sb?.status === "SCHEDULED" ? "UPCOMING" : (sb?.status ?? ev.status)} />
        <span className="tiny muted">{sb?.format ? `Best of ${sb.format.best_of} · ${sb.format.surface ?? "surface ?"}${sb.format.doubles ? " · doubles" : ""}` : ev.round ?? ""}</span>
        {sb?.in_tiebreak && <span className="pill watch">Tiebreak</span>}
        {sb?.termination && sb.termination !== "COMPLETED" && <span className="pill bad">{sb.termination}</span>}
      </div>
    </div>
  );
}
