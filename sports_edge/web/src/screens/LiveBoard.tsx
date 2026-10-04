// Live Board: every discovered game, including the ones without forecasts or markets.
// Rows keep their positions while values update; the order changes only when the user
// asks for it (no rows jumping under the cursor).
import { useEffect, useMemo, useRef, useState } from "react";
import { command } from "../api";
import { Avatar, Empty, LivePill, ModelTag, Sparkline, StatePill } from "../components/Bits";
import { IconLeft, IconRefresh, IconRight, IconSearch, IconStar } from "../components/Icons";
import { localDate, localTime, pct, shiftDay, SPORT_LABEL } from "../format";
import { href } from "../router";
import { loaders, setInterest, setMode, useStore } from "../store";
import type { BoardRow, Sport } from "../types";

const SPORTS: ("ALL" | Sport)[] = ["ALL", "NHL", "NFL", "TENNIS", "MLB"];
const FILTERS = ["Live", "Upcoming", "Finished", "Watchlist", "Value candidates"] as const;
type Filter = typeof FILTERS[number];
type Sort = "start" | "favorites" | "probability" | "edge";

export function LiveBoard({ onlyWatchlist = false }: { onlyWatchlist?: boolean }) {
  const mode = useStore((s) => s.mode);
  const me = useStore((s) => s.me);
  const board = useStore((s) => s.boards[mode]);
  const err = useStore((s) => s.errors[`board:${mode}`]);
  const interest = useStore((s) => s.interest);
  const tz = me?.preferences?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC";
  const [sport, setSport] = useState<"ALL" | Sport>(() => (sessionStorage.getItem("te.sport") as any) || "ALL");
  const [filters, setFilters] = useState<Set<Filter>>(new Set(onlyWatchlist ? ["Watchlist"] : []));
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("start");

  useEffect(() => { setInterest({ boardMode: mode, gameId: null, tz }); return () => setInterest({ boardMode: null }); }, [mode, tz]);
  useEffect(() => { try { sessionStorage.setItem("te.sport", sport); } catch { /* optional */ } }, [sport]);

  const watch = useMemo(() => new Set<string>([...(me?.watchlist ?? []).map((w: any) => w.id), ...(me?.preferences?.favorites ?? [])]), [me]);
  const favs = useMemo(() => new Set<string>(me?.preferences?.favorites ?? []), [me]);
  const rows = board?.rows ?? [];
  const isWatched = (r: BoardRow) => watch.has(r.game_id) || r.event.participants.some((p) => watch.has(p));
  const visible = rows.filter((r) => {
    if (sport !== "ALL" && r.event.sport !== sport) return false;
    if (q) {
      const hay = `${Object.values(r.event.names).join(" ")} ${r.event.competition ?? ""} ${r.event.sport}`.toLowerCase();
      if (!hay.includes(q.toLowerCase())) return false;
    }
    const st = r.event.status;
    const fs = [...filters];
    const statusF = fs.filter((f) => ["Live", "Upcoming", "Finished"].includes(f));
    if (statusF.length && !statusF.some((f) => (f === "Live" && (st === "LIVE" || st === "AWAITING DATA")) ||
      (f === "Upcoming" && (st === "UPCOMING" || st === "SCHEDULED")) || (f === "Finished" && st === "FINAL"))) return false;
    if (filters.has("Watchlist") && !isWatched(r)) return false;
    if (filters.has("Value candidates") && r.assessment !== "VALUE CANDIDATE") return false;
    return true;
  });

  // Stable ordering: computed on demand, frozen while values change.
  const sortKey = (r: BoardRow): (number | string)[] => {
    const statusRank = { LIVE: 0, "AWAITING DATA": 0, UPCOMING: 1, SCHEDULED: 1, FINAL: 2 }[r.event.status] ?? 1;
    const start = Date.parse(r.event.scheduled_start);
    if (sort === "favorites") return [isWatched(r) ? 0 : 1, statusRank, start];
    if (sort === "probability") return [-(r.probability ?? -1), start];
    if (sort === "edge") return [r.validated_net_edge == null ? 1 : 0, -(r.validated_net_edge ?? 0), start];
    return [statusRank, start];
  };
  const [order, setOrder] = useState<string[]>([]);
  const frozenFor = useRef<string>("");
  const ident = `${mode}|${sort}|${board?.date}`;
  const computeOrder = () => {
    const sorted = [...rows].sort((a, b) => {
      const ka = sortKey(a), kb = sortKey(b);
      for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
      return a.game_id < b.game_id ? -1 : 1;
    });
    setOrder(sorted.map((r) => r.game_id));
    frozenFor.current = ident;
  };
  useEffect(() => { if (rows.length && frozenFor.current !== ident) computeOrder(); }, [rows.length, ident]);  // eslint-disable-line
  const pos = new Map(order.map((id, i) => [id, i]));
  const ordered = [...visible].sort((a, b) => (pos.get(a.game_id) ?? 1e9) - (pos.get(b.game_id) ?? 1e9));
  const wouldBe = [...rows].sort((a, b) => { const ka = sortKey(a), kb = sortKey(b); for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1; return a.game_id < b.game_id ? -1 : 1; }).map((r) => r.game_id);
  const stale = order.length > 0 && wouldBe.join() !== order.join();

  const counts = (s: "ALL" | Sport) => rows.filter((r) => s === "ALL" || r.event.sport === s).length;
  const day = board?.date ?? interest.date;

  return (
    <div className="stack-lg">
      <div className="board-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>{onlyWatchlist ? "Watchlist" : "Live Board"}</h1>
          <span className="small muted">{mode === "demo" ? "Demo slate · synthetic replay · fictional teams and players" : "Live data mode · configured sources only"}</span>
        </div>
        <div className="board-tools">
          <div className="seg" role="group" aria-label="Data mode">
            <button aria-pressed={mode === "live"} onClick={() => setMode("live")} data-testid="mode-live">Live</button>
            <button aria-pressed={mode === "demo"} onClick={() => setMode("demo")} data-testid="mode-demo">Demo slate</button>
          </div>
          <div className="datenav" aria-label="Date">
            <button className="ghost icon" aria-label="Previous day" disabled={!day} onClick={() => day && setInterest({ date: shiftDay(day, -1) })}><IconLeft /></button>
            <span className="date num" data-testid="board-date">{day ? localDate(day, tz) : "—"}</span>
            <button className="ghost icon" aria-label="Next day" disabled={!day} onClick={() => day && setInterest({ date: shiftDay(day, 1) })}><IconRight /></button>
            {interest.date && <button className="small ghost" onClick={() => setInterest({ date: null })}>Today</button>}
          </div>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Sport">
        {SPORTS.map((s) => (
          <button key={s} role="tab" aria-selected={sport === s} onClick={() => setSport(s)} data-testid={`sport-${s}`}>
            {s === "ALL" ? "All" : SPORT_LABEL[s]}<span className="count">{counts(s)}</span>
          </button>
        ))}
      </div>

      {board && <Coverage board={board} sport={sport} />}

      <div className="row wrap between">
        <div className="row wrap">
          <label className="row" style={{ position: "relative" }}>
            <span className="sr-only">Search teams, players, competitions</span>
            <span style={{ position: "absolute", left: 10, color: "var(--muted)", display: "flex" }}><IconSearch /></span>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams, players" style={{ paddingLeft: 32, width: 220 }} data-testid="board-search" />
          </label>
          {FILTERS.map((f) => (
            <button key={f} className="chip" aria-pressed={filters.has(f)} onClick={() => {
              const n = new Set(filters); if (n.has(f)) n.delete(f); else n.add(f); setFilters(n);
            }} data-testid={`filter-${f}`}>{f}</button>
          ))}
        </div>
        <div className="row">
          <label className="small muted row">Sort
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} data-testid="board-sort">
              <option value="start">Start time</option><option value="favorites">Favorites first</option>
              <option value="probability">Highest probability</option><option value="edge">Validated net edge</option>
            </select>
          </label>
          <button className={`small ${stale ? "" : "ghost"}`} onClick={computeOrder} disabled={!stale} data-testid="refresh-order"
            title="Rows keep their place while values update. Re-sort when you are ready.">
            <IconRefresh /> {stale ? "Update order" : "Order up to date"}
          </button>
        </div>
      </div>
      {sort === "edge" && <div className="small muted">Ranking by net edge uses validated models only. None is validated yet, so every game sorts as “no validated edge”.</div>}

      <section className="panel" aria-label="Games">
        {err && <div className="panel-pad"><div className="error" role="alert">Could not load the board: {err}</div></div>}
        {!board && !err && <div className="panel-pad muted">Loading…</div>}
        {board && ordered.length === 0 && (
          <Empty title={rows.length ? "No games match these filters" : mode === "live" ? "No live schedule available" : "No games on this date"}>
            {mode === "live" && !rows.length ? "No configured schedule source has succeeded, so the number of games is unknown. This is not an empty slate." :
              rows.length ? "Clear a filter or search to see the other games." : "Use the date arrows to move to the demo slate's day."}
          </Empty>
        )}
        <div className="gamelist" data-testid="game-list">
          {ordered.map((r) => <GameRow key={r.game_id} r={r} tz={tz} watched={isWatched(r)} fav={r.event.participants.some((p) => favs.has(p))} />)}
        </div>
      </section>
      <p className="tiny muted">“Most likely winner” and “attractive price” are separate questions: a favourite can be overpriced, and the value side may be none. Estimates from synthetic-only or experimental models are labelled on every row.</p>
    </div>
  );
}

function Coverage({ board, sport }: { board: any; sport: "ALL" | Sport }) {
  const list = (Object.entries(board.sports) as [Sport, any][]).filter(([s]) => sport === "ALL" || s === sport);
  return (
    <div className="coverage" data-testid="coverage">
      {list.map(([s, c]) => (
        <div key={s} className={`cov ${c.schedule_known ? "" : "unknown"}`} data-testid={`coverage-${s}`}>
          <div className="row between"><b className="small">{SPORT_LABEL[s]}</b>
            <span className={`tiny ${c.schedule_known ? "muted" : "warn-text"}`}>{c.schedule_known ? (c.source ?? "schedule") : "schedule unavailable"}</span></div>
          {c.schedule_known ? (
            <div className="counts">
              <span><b>{c.discovered}</b> discovered</span><span><b>{c.monitored}</b> monitored</span>
              <span><b>{c.forecast_ready}</b> forecast-ready</span><span><b>{c.unavailable}</b> unavailable</span>
            </div>
          ) : <div className="tiny muted">Game count unknown — not zero.</div>}
        </div>
      ))}
    </div>
  );
}

function scoreLine(r: BoardRow) {
  const sb = r.scoreboard ?? {};
  if (r.event.sport === "TENNIS") {
    if (sb.status !== "LIVE" && sb.status !== "FINAL") return null;
    return null;
  }
  return sb.score ?? null;
}

function GameRow({ r, tz, watched, fav }: { r: BoardRow; tz: string; watched: boolean; fav: boolean }) {
  const me = useStore((s) => s.me);
  const ev = r.event;
  const sb = r.scoreboard ?? {};
  const names = [ev.names[ev.away] ?? ev.away, ev.names[ev.home] ?? ev.home];
  const tennis = ev.sport === "TENNIS";
  const order = tennis ? [ev.home, ev.away] : [ev.away, ev.home];
  const score = scoreLine(r);
  const lw = r.likely_winner;
  const sm = r.selected_market;
  const toggleWatch = async (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (!me?.signed_in) { window.location.hash = "/login"; return; }
    const items = (me.watchlist ?? []).filter((w: any) => !(w.kind === "game" && w.id === r.game_id));
    if (!watched) items.push({ kind: "game", id: r.game_id });
    await command("PUT", "/api/account/watchlist", { items }); await loaders.me();
  };
  const status = sb.status === "SCHEDULED" ? "UPCOMING" : ev.status;
  return (
    <a className="grow-row" href={href(`/game/${encodeURIComponent(r.game_id)}`)} data-testid={`row-${r.game_id}`}>
      <div className="when">
        <LivePill status={status} />
        <span className="num">{status === "LIVE" ? liveClock(r) : localTime(ev.scheduled_start, tz)}</span>
        <span className="tiny faint truncate">{SPORT_LABEL[ev.sport]}{ev.round ? ` · ${ev.round}` : ""}</span>
      </div>
      <div className="teams">
        {order.map((pid, i) => {
          const nm = ev.names[pid] ?? pid;
          const sc = tennis ? tennisSets(sb, pid === ev.home ? 0 : 1) : score ? score[i] : null;
          const other = tennis ? null : score ? score[1 - i] : null;
          const lead = !tennis && sc != null && other != null ? ((sc as number) > other ? "lead" : (sc as number) < other ? "trail" : "") : "";
          return (
            <div key={pid} className={`team ${lead}`}>
              <Avatar name={nm} />
              <span className="nm">{nm}</span>
              {tennis && sb.status === "LIVE" && sb.server === (pid === ev.home ? "P1" : "P2") && <span className="srv" aria-label="serving" />}
              {sc != null && <span className="sc">{sc}</span>}
            </div>
          );
        })}
        <span className="statusline truncate">{statusText(r)}</span>
      </div>
      <div className="col-model">
        <div className="cell-k">Most likely winner</div>
        {lw.available ? (
          <div className="cell-v"><span className="big">{pct(lw.probability)}</span> <span className="dim">{lw.name}</span>
            <div><ModelTag status={lw.status} /></div></div>
        ) : <div className="small muted" title={lw.reason}>{shortReason(lw.reason)}</div>}
      </div>
      <div className="col-price">
        <div className="cell-k">Price {sm ? `· ${sm.name}` : ""}</div>
        {sm && sm.ask ? (
          <div className="row" style={{ gap: 10 }}>
            <span className="cell-v"><span className="big">{Math.round(Number(sm.ask) * 100)}¢</span> <span className="tiny muted">ask</span></span>
            <Sparkline values={r.sparkline} width={96} height={26} label={`${sm.name} price`} />
          </div>
        ) : <div className="small muted">{r.has_market ? "No current quote" : "No market mapped"}</div>}
        {sm && <div className="tiny faint truncate">{sm.source}</div>}
      </div>
      <div className="assess">
        <StatePill s={r.assessment} />
        <span className="tiny muted">{freshText(r)}</span>
      </div>
      <button className="star" aria-pressed={watched} aria-label={watched ? "Unfollow game" : "Follow game"} onClick={toggleWatch}
        title={fav ? "Includes a favourite" : undefined}><IconStar filled={watched} /></button>
    </a>
  );
}

function tennisSets(sb: any, i: number) {
  if (!sb?.sets) return null;
  // completed set games, then the current set's games (highlighted)
  return (
    <span className="row" style={{ gap: 8 }}>
      {sb.sets.map((s: number[], k: number) => <span key={k} style={{ color: s[i] > s[1 - i] ? "var(--text)" : "var(--muted)" }}>{s[i]}</span>)}
      {sb.status === "LIVE" && <span style={{ color: "var(--accent-2)" }}>{sb.games?.[i] ?? 0}</span>}
    </span>
  );
}
function liveClock(r: BoardRow) {
  const sb = r.scoreboard ?? {};
  if (r.event.sport === "NHL") return `P${sb.period} ${sb.clock}`;
  if (r.event.sport === "NFL") return `${sb.period} ${sb.clock}`;
  if (r.event.sport === "MLB") return `${sb.half === "TOP" ? "▲" : "▼"}${sb.inning} · ${sb.outs} out`;
  if (r.event.sport === "TENNIS") return sb.in_tiebreak ? "Tiebreak" : `Set ${(sb.sets?.length ?? 0) + 1} · ${sb.points}`;
  return "Live";
}
function statusText(r: BoardRow) {
  const sb = r.scoreboard ?? {};
  if (r.event.sport === "NFL" && sb.down_distance) return `${r.event.names[sb.possession] ?? sb.possession} · ${sb.down_distance}`;
  if (r.event.sport === "NHL" && sb.skaters && sb.skaters[0] !== sb.skaters[1]) return `Power play ${sb.skaters[0]}v${sb.skaters[1]}`;
  if (r.event.sport === "TENNIS" && sb.termination && sb.termination !== "COMPLETED") return sb.termination.toLowerCase();
  return r.event.competition ?? "";
}
function freshText(r: BoardRow) {
  const f = r.freshness;
  if (r.event.status === "FINAL") return "final";
  if (!f) return "no feed";
  if (f.pending_reconciliation?.length) return "feed reconciling";
  if (f.game_state_age_s == null) return r.event.status === "FINAL" ? "final" : "no game data yet";
  return `state ${Math.round(f.game_state_age_s)}s old`;
}
function shortReason(r?: string) {
  if (!r) return "Unavailable";
  if (r.startsWith("DOUBLES")) return "Doubles model not enabled";
  if (r.startsWith("No market")) return "No market mapped";
  if (r.startsWith("STRENGTH")) return "Strength data missing";
  if (r.startsWith("NO_MODEL")) return "No model loaded";
  if (r.includes("feed")) return "Live feed not connected";
  return r.length > 40 ? r.slice(0, 40) + "…" : r;
}
