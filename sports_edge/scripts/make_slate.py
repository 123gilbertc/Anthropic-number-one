"""Generate the deterministic SYNTHETIC multi-sport replay slate.

Everything is invented: fictional teams and players, simulated games, simulated
order books and sportsbook quotes. The meta line says SYNTHETIC and the product
labels every screen built from it. It exercises the multi-sport plumbing; it is
not evidence about any real market.

Markets deliberately *overreact* to scoring events for a few minutes so the
mechanics demo has something to detect. That behaviour is a property of this
generator, not a claim about real markets.

    uv run python scripts/make_slate.py
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from sports_edge.ingest.mlb_state import MLBState
from sports_edge.sports.mlb import PARates, win_probability
from sports_edge.sports.nfl_sim import simulate as simulate_nfl
from sports_edge.sports.tennis_model import p_match
from sports_edge.sports.tennis_rules import P1, P2, Format, Score, play_point, start_set

T0 = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
LAG = 3.0  # seconds from event to our receipt (synthetic)
rng = np.random.default_rng(20261011)
lines: list[dict] = []
ladders: dict[str, dict[str, dict[str, int]]] = {}
kseq = {"n": 0}


def iso(t: datetime) -> str:
    return t.isoformat()


def sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


# ------------------------------------------------------------------ market plumbing


def _send(t: datetime, typ: str, msg: dict) -> None:
    kseq["n"] += 1
    lines.append({"stream": "kalshi", "received_time": iso(t),
                  "raw": {"type": typ, "sid": 1, "seq": kseq["n"], "msg": msg}})


def _ladder(ask: float, spread: float, size: int) -> tuple[dict, dict]:
    bid = ask - spread
    yes = {f"{bid - 0.01 * i:.4f}": size + 40 * i for i in range(3) if bid - 0.01 * i > 0.005}
    no = {f"{1 - ask - 0.01 * i:.4f}": size + 40 * i for i in range(3)
          if 1 - ask - 0.01 * i > 0.005}
    return yes, no


def book(t: datetime, ticker: str, ask: float, spread: float = 0.02, size: int = 150) -> None:
    ask = round(min(0.97, max(0.04, ask)), 2)
    spread = round(min(spread, ask - 0.02), 2)
    yes, no = _ladder(ask, spread, size)
    if ticker not in ladders:
        ladders[ticker] = {"yes": yes, "no": no}
        _send(t, "orderbook_snapshot", {
            "market_ticker": ticker,
            "yes_dollars_fp": [[p, f"{q}.00"] for p, q in yes.items()],
            "no_dollars_fp": [[p, f"{q}.00"] for p, q in no.items()]})
        return
    for side, target in (("yes", yes), ("no", no)):
        cur = ladders[ticker][side]
        for p in sorted(set(cur) | set(target)):
            d = target.get(p, 0) - cur.get(p, 0)
            if d:
                _send(t, "orderbook_delta", {"market_ticker": ticker, "price_dollars": p,
                                             "delta_fp": f"{d}.00", "side": side})
        ladders[ticker][side] = dict(target)


def american(p: float) -> int:
    p = min(0.97, max(0.03, p))
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def odds(t: datetime, gid: str, home: str, away: str, p_home: float, lag_s: float) -> None:
    m = 0.025  # bookmaker margin (synthetic)
    lines.append({"stream": "odds", "game_id": gid, "received_time": iso(t), "quote": {
        "book": "synthbook", "market": "h2h",
        "prices_american": {home: american(p_home + m / 2), away: american(1 - p_home + m / 2)},
        "provider_last_update": iso(t - timedelta(seconds=lag_s))}})


class Market:
    """Fair price + temporary overreaction after material events + noise."""

    def __init__(self, gid: str, home: str, away: str, c_home: str, c_away: str | None,
                 ref: bool) -> None:
        self.gid, self.home, self.away = gid, home, away
        self.c_home, self.c_away = c_home, c_away
        self.ref = ref
        self.shock = 0.0
        self.shock_at: datetime | None = None
        self.last_quote: datetime | None = None
        self.last_odds: datetime | None = None
        self.fair = 0.5

    def event(self, t: datetime, direction: float) -> None:
        """direction > 0: the event favoured home. The market overshoots that way."""
        self.shock = direction * float(rng.uniform(0.06, 0.12))
        self.shock_at = t

    def quote(self, t: datetime, fair: float, force: bool = False) -> None:
        self.fair = fair
        if not force and self.last_quote and (t - self.last_quote).total_seconds() < 20:
            return
        self.last_quote = t
        shock = 0.0
        spread = 0.02
        if self.shock_at is not None:
            age = (t - self.shock_at).total_seconds()
            shock = self.shock * math.exp(-age / 180)
            if age < 20:
                spread = 0.04
        mid = min(0.96, max(0.04, fair + shock + float(rng.normal(0, 0.006))))
        book(t, self.c_home, mid + spread / 2, spread, int(rng.integers(90, 260)))
        if self.c_away:
            book(t, self.c_away, 1 - mid + spread / 2, spread, int(rng.integers(90, 260)))
        if self.ref and (self.last_odds is None or (t - self.last_odds).total_seconds() >= 60):
            self.last_odds = t
            odds(t + timedelta(seconds=2), self.gid, self.home, self.away, fair,
                 float(rng.uniform(15, 40)))


class Events:
    def __init__(self, gid: str) -> None:
        self.gid, self.n = gid, 0

    def add(self, t: datetime, typ: str, data: dict) -> None:
        self.n += 1
        lines.append({"stream": "game", "game_id": self.gid,
                      "received_time": iso(t + timedelta(seconds=LAG)), "event": {
                          "type": typ, "provider_event_id": f"{self.gid}-e{self.n}",
                          "seq": self.n, "event_time": iso(t),
                          "published_time": iso(t + timedelta(seconds=1)), "data": data}})


def mapping(gid: str, contract: str, team: str, rule: str) -> dict:
    return {"mapping_id": f"map-{contract}", "game_id": gid, "venue": "SYNTHETIC",
            "contract_id": contract, "selection_team": team, "settlement_rule": rule,
            "rules_text_hash": None, "tick_size": "0.01", "verified_by": "synthetic fixture"}


def pregame_quotes(m: Market, start: datetime, fair: float, minutes: int = 90) -> None:
    t = start - timedelta(minutes=minutes)
    drift = float(rng.normal(0, 0.02))
    while t < start:
        f = fair + drift * (1 - (start - t).total_seconds() / (minutes * 60))
        m.quote(t, f, force=True)
        t += timedelta(minutes=float(rng.uniform(3, 7)))


# ------------------------------------------------------------------ NHL


def nhl_game(gid: str, home: str, away: str, start: datetime, edge: float) -> dict:
    ev = Events(gid)
    ch, ca = f"{gid}-{home}", f"{gid}-{away}"
    m = Market(gid, home, away, ch, ca, ref=True)
    prior = sig(1.1 * edge)
    pregame_quotes(m, start, prior)
    lam_h = 3.0 / 3600 * math.exp(edge / 2)
    lam_a = 3.0 / 3600 * math.exp(-edge / 2)
    hs = as_ = 0
    pp = {home: 0, away: 0}
    wall = 2.3  # wall seconds per game second

    def at(gs: float) -> datetime:
        return start + timedelta(seconds=gs * wall)

    def clk(gs: int) -> dict:
        per = min(3, gs // 1200 + 1)
        return {"period": per, "seconds_remaining": 1200 - (gs - (per - 1) * 1200)}

    def fair(gs: int) -> float:
        frac = max(0.0, (3600 - gs) / 3600)
        return sig(math.log(prior / (1 - prior)) * (0.4 + 0.6 * frac)
                   + 1.0 * (hs - as_) / math.sqrt(frac + 0.03))

    ev.add(at(0), "SNAPSHOT", {**clk(0), "home_score": 0, "away_score": 0, "home_skaters": 5,
                               "away_skaters": 5, "home_goalie": f"{home} G1",
                               "away_goalie": f"{away} G1", "home_net_empty": False,
                               "away_net_empty": False})
    for gs in range(1, 3600):
        if gs % 30 == 0:
            ev.add(at(gs), "CLOCK", clk(gs))
            m.quote(at(gs), fair(gs))
        for team, lam, opp in ((home, lam_h, away), (away, lam_a, home)):
            mult = 1.8 if pp[opp] else 0.6 if pp[team] else 1.0  # pp keyed by penalized team
            if rng.random() < lam * mult:
                if team == home:
                    hs += 1
                else:
                    as_ += 1
                ev.add(at(gs), "GOAL", {**clk(gs), "team": team})
                if pp[opp]:
                    pp[opp] = 0
                    ev.add(at(gs), "PENALTY_END", {**clk(gs), "team": opp})
                m.event(at(gs), 1 if team == home else -1)
                m.quote(at(gs) + timedelta(seconds=6), fair(gs), force=True)
        for team in (home, away):
            if pp[team]:
                pp[team] -= 1
                if pp[team] == 0:
                    ev.add(at(gs), "PENALTY_END", {**clk(gs), "team": team})
        if not pp[home] and not pp[away] and rng.random() < 1 / 500:
            pen = home if rng.random() < 0.5 else away
            ev.add(at(gs), "PENALTY", {**clk(gs), "team": pen})
            pp[pen] = 120
            m.quote(at(gs) + timedelta(seconds=5), fair(gs), force=True)
    end = 3600
    decided = "REG"
    reg_h, reg_a = hs, as_
    if hs == as_:
        ev.add(at(3600), "CLOCK", {"period": 4, "seconds_remaining": 300})
        decided = "OT"
        win_home = rng.random() < lam_h / (lam_h + lam_a)
        end = 3600 + int(rng.integers(30, 290))
        if win_home:
            hs += 1
        else:
            as_ += 1
        ev.add(at(end), "GOAL", {"period": 4, "seconds_remaining": 300 - (end - 3600),
                                 "team": home if win_home else away})
    ev.add(at(end) + timedelta(seconds=1), "GAME_END", {
        "period": 4 if decided == "OT" else 3,
        "seconds_remaining": 300 - (end - 3600) if decided == "OT" else 0,
        "decided_in": decided, "regulation_home_score": reg_h, "regulation_away_score": reg_a})
    m.quote(at(end) + timedelta(seconds=8), 0.99 if hs > as_ else 0.01, force=True)
    return {"game": {"game_id": gid, "sport": "NHL", "season": "2026-27", "home_team": home,
                     "away_team": away, "scheduled_start": iso(start), "status": "SCHEDULED",
                     "source": "synthetic_slate", "competition": "NHL",
                     "names": NAMES, "venue_name": "Synthetic Arena",
                     "details": {"season_type": "REGULAR"}},
            "mappings": [mapping(gid, ch, home, "NHL_INCLUDING_OT_SO"),
                         mapping(gid, ca, away, "NHL_INCLUDING_OT_SO")],
            "pregame_prob": {home: round(prior, 3), away: round(1 - prior, 3)},
            "anchor_price": {ch: f"{round(prior, 2):.2f}", ca: f"{round(1 - prior, 2):.2f}"}}


# ------------------------------------------------------------------ NFL


def nfl_game(gid: str, home: str, away: str, start: datetime, edge: float,
             play: bool = True) -> dict:
    ch, ca = f"{gid}-{home}", f"{gid}-{away}"
    m = Market(gid, home, away, ch, ca, ref=True)
    prior = sig(1.0 * edge + 0.1)
    pregame_quotes(m, start, prior, minutes=150)
    if play:
        ev = Events(gid)
        sim_events, res = simulate_nfl(rng, home, away, edge, "REGULAR")
        wall = 3.0
        score = {home: 0, away: 0}
        poss = None
        yl = 75
        for e in sim_events:
            t = start + timedelta(seconds=e.t * wall)
            ev.add(t, e.type, e.data)
            if e.type == "SCORE":
                score[e.data["team"]] += e.data["points"]
                if e.data["points"] >= 3:
                    m.event(t, 1 if e.data["team"] == home else -1)
            if e.type == "TURNOVER":
                m.event(t, 0.6 if e.data["gaining_team"] == home else -0.6)
            poss = e.data.get("possession", e.data.get("gaining_team", poss))
            yl = e.data.get("yardline_100", yl)
            frac = max(0.0, 1 - e.t / 3600)
            has = 0 if poss is None else (1 if poss == home else -1)
            f = sig(math.log(prior / (1 - prior)) * (0.3 + 0.7 * frac)
                    + 0.22 * (score[home] - score[away]) / math.sqrt(frac + 0.02)
                    + 0.15 * has * (100 - yl) / 100 / math.sqrt(frac + 0.05))
            if e.type == "GAME_END":
                f = 0.99 if score[home] > score[away] else 0.01 if score[away] > score[home] \
                    else 0.5
            m.quote(t + timedelta(seconds=5), f, force=e.type in ("SCORE", "TURNOVER",
                                                                  "GAME_END"))
    return {"game": {"game_id": gid, "sport": "NFL", "season": "2026", "home_team": home,
                     "away_team": away, "scheduled_start": iso(start), "status": "SCHEDULED",
                     "source": "synthetic_slate", "competition": "NFL", "names": NAMES,
                     "venue_name": "Synthetic Stadium",
                     "details": {"season_type": "REGULAR", "rules_version": "ASSUMED_2026"}},
            "mappings": [mapping(gid, ch, home, "NFL_INCL_OT_TIE_VOID"),
                         mapping(gid, ca, away, "NFL_INCL_OT_TIE_VOID")],
            "pregame_prob": {home: round(prior, 3), away: round(1 - prior, 3)},
            "anchor_price": {ch: f"{round(prior, 2):.2f}", ca: f"{round(1 - prior, 2):.2f}"}}


# ------------------------------------------------------------------ tennis


def tennis_match(gid: str, p1: str, p2: str, start: datetime, s1: float, s2: float,
                 fmt: Format, *, retire_after_points: int | None = None,
                 market: bool = True, competition: str = "SYN-TOUR-500") -> dict:
    ev = Events(gid)
    ch, ca = f"{gid}-{p1}", f"{gid}-{p2}"
    m = Market(gid, p1, p2, ch, ca, ref=False) if market else None
    first = P1 if rng.random() < 0.5 else P2
    pre = 0.5 * (p_match(fmt, Score(server=P1), s1, s2) + p_match(fmt, Score(server=P2), s1, s2))
    if m:
        pregame_quotes(m, start, pre, minutes=60)
    t = start
    details = {"best_of": fmt.best_of, "final_set": fmt.final_set, "no_ad": fmt.no_ad,
               "tiebreak_at": fmt.tiebreak_at, "doubles": fmt.doubles, "surface": "HARD"}
    ev.add(t, "SNAPSHOT", {**details, "sets": [], "games": [0, 0], "points": [0, 0],
                           "server": first})
    sc = start_set(fmt, Score(server=first))
    n = 0
    while sc.winner is None:
        t += timedelta(seconds=float(rng.uniform(28, 48)))
        n += 1
        if retire_after_points is not None and n >= retire_after_points:
            ev.add(t, "RETIRED", {"player": P2})
            if m:
                m.quote(t + timedelta(seconds=10), 0.99, force=True)
            break
        pw = s1 if sc.server == P1 else 1 - s2
        winner = P1 if rng.random() < pw else P2
        r = play_point(fmt, sc, winner)
        ev.add(t, "POINT", {"winner": winner,
                            "expect": {"games": list(r.score.games),
                                       "sets": [list(x) for x in r.score.sets]}})
        if m:
            if r.break_of_serve or r.set_won_by:
                m.event(t, 1 if r.game_won_by == P1 else -1)
            f = p_match(fmt, r.score, s1, s2) if r.score.winner is None else \
                (0.99 if r.score.winner == P1 else 0.01)
            m.quote(t + timedelta(seconds=4), f, force=bool(r.game_won_by))
        sc = r.score
        if r.game_won_by and rng.random() < 0.15:
            t += timedelta(seconds=90)  # changeover
    if sc.winner is not None:
        ev.add(t + timedelta(seconds=2), "MATCH_END", {"winner": sc.winner})
    out = {"game": {"game_id": gid, "sport": "TENNIS", "season": "2026", "home_team": p1,
                    "away_team": p2, "scheduled_start": iso(start), "status": "SCHEDULED",
                    "source": "synthetic_slate", "competition": competition,
                    "participant_kind": "PLAYER", "names": NAMES, "round": "R16",
                    "venue_name": "Synthetic Court 1", "details": details},
           "mappings": [], "pregame_prob": {}, "anchor_price": {}}
    if m:
        out["mappings"] = [mapping(gid, ch, p1, "TENNIS_MATCH_RETIREMENT_ADVANCER_WINS"),
                           mapping(gid, ca, p2, "TENNIS_MATCH_RETIREMENT_ADVANCER_WINS")]
        out["anchor_price"] = {ch: f"{round(pre, 2):.2f}", ca: f"{round(1 - pre, 2):.2f}"}
    return out


# ------------------------------------------------------------------ MLB


def _mlb_state(**k) -> MLBState:
    base = dict(snapshot_id="x", game_id="g", source="s", source_status="SYNTHETIC_DEMO",
                as_of_event_time=None, as_of_received_time=T0, last_material_event_time=None,
                last_material_event_kind=None, applied_seq=None, inning=1, half="TOP",
                outs=0, runners=(False, False, False), home_runs=0, away_runs=0, balls=None,
                strikes=None, home_pitcher=None, away_pitcher=None, home_pitcher_pitches=None,
                away_pitcher_pitches=None, lineup_confirmed=None)
    base.update(k)
    return MLBState(**base)


def mlb_game(gid: str, home: str, away: str, start: datetime, rh: PARates, ra: PARates,
             season: str) -> dict:
    ev = Events(gid)
    ch, ca = f"{gid}-{home}", f"{gid}-{away}"
    m = Market(gid, home, away, ch, ca, ref=True)
    pre = win_probability(_mlb_state(), rh, ra, season)
    pregame_quotes(m, start, pre)
    hp, ap = f"{home} SP", f"{away} SP"
    ev.add(start, "SNAPSHOT", {"inning": 1, "half": "TOP", "outs": 0, "runners": [0, 0, 0],
                               "home_runs": 0, "away_runs": 0, "home_pitcher": hp,
                               "away_pitcher": ap, "home_pitcher_pitches": 0,
                               "away_pitcher_pitches": 0, "lineup_confirmed": True})
    from sports_edge.sports.mlb import _advance
    t = start
    inning, half, outs, bases = 1, "TOP", 0, (0, 0, 0)
    score = {"home": 0, "away": 0}
    changed = {"home": False, "away": False}
    while True:
        rates = ra if half == "TOP" else rh
        t += timedelta(seconds=float(rng.uniform(100, 200)))
        oc = rng.choice(["out", "walk", "single", "double", "triple", "hr"],
                        p=np.array(rates.as_tuple()))
        runs = 0
        if oc == "out":
            outs += 1
        else:
            bases, runs = _advance(bases, str(oc))
        side = "away" if half == "TOP" else "home"
        if half == "BOTTOM" and inning >= 9 and score["home"] + runs > score["away"]:
            runs = min(runs, score["away"] - score["home"] + 1 + (3 if oc == "hr" else 0))
        score[side] += runs
        ev.add(t, "PLAY", {"outs": outs, "runners": list(bases), "runs": runs,
                           "pa_complete": True,
                           "desc": {"hr": "Home run", "triple": "Triple", "double": "Double",
                                    "single": "Single", "walk": "Walk"}.get(str(oc), "Out")})
        if runs:
            m.event(t, 1 if side == "home" else -1)
        st = _mlb_state(inning=inning, half=half, outs=min(outs, 3),
                        runners=tuple(bool(x) for x in bases), home_runs=score["home"],
                        away_runs=score["away"])
        walkoff = half == "BOTTOM" and inning >= 9 and score["home"] > score["away"]
        if walkoff:
            break
        if outs >= 3:
            if half == "TOP" and inning >= 9 and score["home"] > score["away"]:
                break
            if half == "BOTTOM" and inning >= 9 and score["home"] != score["away"]:
                break
            t += timedelta(seconds=120)
            ev.add(t, "HALF_END", {})
            outs, bases = 0, (0, 0, 0)
            if half == "TOP":
                half = "BOTTOM"
            else:
                half, inning = "TOP", inning + 1
            if inning >= 10 and season == "REGULAR":
                bases = (0, 1, 0)
            # a pitching change in the 6th for the fielding team (synthetic)
            fielding = "home" if half == "TOP" else "away"
            if inning == 6 and not changed[fielding]:
                changed[fielding] = True
                ev.add(t, "PITCHER_CHANGE", {"team": home if fielding == "home" else away,
                                             "pitcher": f"{home if fielding == 'home' else away} RP1"})
            st = _mlb_state(inning=inning, half=half, home_runs=score["home"],
                            away_runs=score["away"], runners=tuple(bool(x) for x in bases))
        p = win_probability(st, rh, ra, season)
        m.quote(t + timedelta(seconds=5), p, force=bool(runs))
        if inning > 15:
            break
    ev.add(t + timedelta(seconds=5), "GAME_END", {})
    m.quote(t + timedelta(seconds=12), 0.99 if score["home"] > score["away"] else 0.01,
            force=True)
    return {"game": {"game_id": gid, "sport": "MLB", "season": "2026", "home_team": home,
                     "away_team": away, "scheduled_start": iso(start), "status": "SCHEDULED",
                     "source": "synthetic_slate", "competition": "MLB", "names": NAMES,
                     "venue_name": "Synthetic Park",
                     "details": {"season_type": season, "listed_pitchers": [hp, ap]}},
            "mappings": [mapping(gid, ch, home, "MLB_FULL_GAME_INCL_EXTRAS"),
                         mapping(gid, ca, away, "MLB_FULL_GAME_INCL_EXTRAS")],
            "pregame_prob": {}, "anchor_price": {ch: f"{round(pre, 2):.2f}",
                                                 ca: f"{round(1 - pre, 2):.2f}"}}


# ------------------------------------------------------------------ slate

NAMES = {
    "LKP": "Lakeport Lynx", "RWB": "Redwood Bears", "SHC": "Shoreline Comets",
    "IRN": "Iron Valley Rams", "NRT": "Northgate Tides", "GLV": "Glenvale Owls",
    "BRK": "Brookhaven Kites", "CDR": "Cedar Ridge Foxes", "MSA": "Mesa Arrows",
    "PNE": "Pine Harbor Pilots", "SYP-AVERY": "A. Avery", "SYP-BRANDT": "M. Brandt",
    "SYP-CORTES": "L. Cortes", "SYP-DUVAL": "J. Duval", "SYP-ELLIS": "R. Ellis",
    "SYP-FAROOQ": "S. Farooq", "SYP-GRAY": "T. Gray", "SYP-HOLM": "K. Holm",
    "SYT-AB": "Avery / Brandt", "SYT-CD": "Cortes / Duval",
}


def noisy_rates(true: PARates, n: int) -> dict:
    v = rng.dirichlet(np.array(true.as_tuple()) * n)
    v = np.round(v, 4)
    v[0] += 1 - v.sum()
    return {"rates": dict(zip(("out", "walk", "single", "double", "triple", "hr"),
                              map(float, v), strict=True)), "n_pa": n}


def build() -> list[dict]:
    games = []
    games.append(tennis_match("SYN-TEN-1", "SYP-AVERY", "SYP-BRANDT", T0 - timedelta(minutes=20),
                              0.66, 0.62, Format(3, "TB7")))
    games.append(nhl_game("SYN-NHL-1", "NRT", "GLV", T0 + timedelta(minutes=10), 0.25))
    games.append(nfl_game("SYN-NFL-1", "LKP", "RWB", T0 + timedelta(minutes=25), 0.30))
    games.append(tennis_match("SYN-TEN-2", "SYP-CORTES", "SYP-DUVAL", T0 + timedelta(minutes=40),
                              0.64, 0.65, Format(3, "TB7"), retire_after_points=150))
    games.append(tennis_match("SYN-TEN-3", "SYT-AB", "SYT-CD", T0 + timedelta(minutes=50),
                              0.63, 0.61, Format(3, "MTB10", no_ad=True, doubles=True),
                              competition="SYN-TOUR-500-DOUBLES"))
    games.append(tennis_match("SYN-TEN-4", "SYP-ELLIS", "SYP-FAROOQ", T0 + timedelta(minutes=70),
                              0.65, 0.63, Format(3, "TB7"), market=False))
    rh = PARates(0.675, 0.09, 0.15, 0.047, 0.004, 0.034)
    ra = PARates(0.69, 0.085, 0.145, 0.044, 0.004, 0.032)
    games.append(mlb_game("SYN-MLB-1", "BRK", "CDR", T0 + timedelta(minutes=35), rh, ra,
                          "POSTSEASON"))
    games.append(nhl_game("SYN-NHL-2", "SHC", "IRN", T0 + timedelta(minutes=95), -0.15))
    games.append(nfl_game("SYN-NFL-2", "MSA", "PNE", T0 + timedelta(hours=7), 0.1, play=False))
    meta = {
        "stream": "meta", "format": "slate_v1", "data_label": "SYNTHETIC",
        "description": "Invented multi-sport slate (fictional teams and players) for "
                       "plumbing tests and the demo workspace. Not real data.",
        "games": games,
        "strength": {
            "TENNIS": {"label": "SYNTHETIC", "as_of": "2026-10-10", "averages": {
                "HARD": {"serve_won": 0.635, "shrink_points": 400}},
                "rates": [
                    {"player": pid, "surface": "HARD", "serve_won": round(sv, 3),
                     "serve_n": 2400, "return_won": round(rt, 3), "return_n": 2400}
                    for pid, sv, rt in (("SYP-AVERY", 0.662, 0.385), ("SYP-BRANDT", 0.627, 0.36),
                                        ("SYP-CORTES", 0.645, 0.37), ("SYP-DUVAL", 0.648, 0.372),
                                        ("SYT-AB", 0.63, 0.36), ("SYT-CD", 0.61, 0.35))]},
            "MLB": {"label": "SYNTHETIC", "as_of": "2026-10-10", "teams": {
                "BRK": noisy_rates(rh, 6000), "CDR": noisy_rates(ra, 6000)}},
        },
    }
    for sec in range(int(-150 * 60), int(6.5 * 3600), 5):
        lines.append({"stream": "kalshi", "received_time": iso(T0 + timedelta(seconds=sec)),
                      "raw": {"type": "heartbeat"}})
    body = sorted(lines, key=lambda x: (x["received_time"], x["stream"] != "game"))
    # Kalshi seq must follow delivery order
    n = 0
    for x in body:
        if x["stream"] == "kalshi" and x["raw"].get("type") != "heartbeat":
            n += 1
            x["raw"]["seq"] = n
    return [meta] + body


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "fixtures" / "slate_synthetic.jsonl"
    data = build()
    out.write_text("\n".join(json.dumps(x, sort_keys=True) for x in data) + "\n")
    print(f"wrote {out}: {len(data) - 1} lines, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
