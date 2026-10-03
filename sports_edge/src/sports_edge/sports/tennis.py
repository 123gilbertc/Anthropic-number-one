"""Tennis: state, reducer (rules applied point by point), forecaster and display.

Participants: ``game.home_team`` is player/team 1 (P1), ``game.away_team`` is P2.
Match format and surface come from the schedule (``game.details``) or a provider
SNAPSHOT. If the format is unknown, points are recorded but not scored, the state
is flagged ``FORMAT_UNKNOWN``, and the model abstains.

Doubles scoring rules are implemented and tested, but the probability model is
only enabled for singles: the per-team serve model ignores the four-server
rotation, so doubles forecasts abstain (``DOUBLES_MODEL_NOT_ENABLED``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal

from sports_edge.domain.enums import ModelStatus, SettlementRule, Sport
from sports_edge.domain.records import Game, GameStateBase, Prediction, stable_id
from sports_edge.ingest.nhl_state import InvalidEvent, NormalizedGameEvent
from sports_edge.sports.base import BaseReducer, FinalOutcome, add_flag, drop_flag, register
from sports_edge.sports.tennis_model import PlayerRates, SurfaceAverages, band
from sports_edge.sports.tennis_rules import (
    P1,
    P2,
    Format,
    Score,
    other,
    play_point,
    start_set,
    valid_score,
)


class TennisState(GameStateBase):
    best_of: int | None
    final_set: str | None
    no_ad: bool | None
    tiebreak_at: int = 6
    doubles: bool | None
    surface: str | None
    sets: tuple[tuple[int, int], ...] = ()
    games_p1: int = 0
    games_p2: int = 0
    points_p1: int = 0
    points_p2: int = 0
    in_tiebreak: bool = False
    tiebreak_target: int | None = None
    tiebreak_first_server: str | None = None
    server: str | None = None
    winner: str | None = None
    termination: Literal["COMPLETED", "RETIRED", "WALKOVER", "DEFAULTED"] | None = None
    suspended: bool = False

    def fmt(self) -> Format | None:
        if self.best_of is None or self.final_set is None or self.no_ad is None:
            return None
        return Format(self.best_of, self.final_set, self.no_ad, self.tiebreak_at,
                      bool(self.doubles))

    def score(self) -> Score:
        return Score(sets=self.sets, games=(self.games_p1, self.games_p2),
                     points=(self.points_p1, self.points_p2), in_tiebreak=self.in_tiebreak,
                     tiebreak_target=self.tiebreak_target, server=self.server or P1,
                     tiebreak_first_server=self.tiebreak_first_server,
                     winner=self.winner if self.termination == "COMPLETED" else None)

    def coherent(self) -> bool:
        f = self.fmt()
        if f is None or self.server is None:
            return True  # unknown is not incoherent; it is flagged separately
        if self.termination not in (None, "COMPLETED"):
            return True
        return valid_score(f, self.score())

    def material_key(self) -> tuple:
        return (self.sets, self.games_p1, self.games_p2, self.points_p1, self.points_p2,
                self.in_tiebreak, self.server, self.winner, self.termination, self.suspended)


FORMAT_KEYS = ("best_of", "final_set", "no_ad", "tiebreak_at", "doubles", "surface")


def _apply_score(u: dict[str, Any], sc: Score) -> None:
    u.update(sets=sc.sets, games_p1=sc.games[0], games_p2=sc.games[1],
             points_p1=sc.points[0], points_p2=sc.points[1], in_tiebreak=sc.in_tiebreak,
             tiebreak_target=sc.tiebreak_target, tiebreak_first_server=sc.tiebreak_first_server,
             server=sc.server)
    if sc.winner is not None:
        u.update(winner=sc.winner, termination="COMPLETED", is_final=True)


@dataclass
class TennisReducer(BaseReducer):
    details: dict[str, Any] = field(default_factory=dict)

    state_cls: ClassVar = TennisState
    prefix: ClassVar[str] = "ten"
    material_types: ClassVar[frozenset[str]] = frozenset(
        {"SNAPSHOT", "RETIRED", "WALKOVER", "DEFAULTED", "SUSPENDED", "RESUMED", "MATCH_END",
         "SERVER"})

    def initial_fields(self) -> dict[str, Any]:
        d = self.details
        return dict(best_of=d.get("best_of"), final_set=d.get("final_set"),
                    no_ad=d.get("no_ad"), tiebreak_at=d.get("tiebreak_at", 6),
                    doubles=d.get("doubles"), surface=d.get("surface"))

    def material(self, ev, before, after) -> bool:
        if ev.type == "POINT":  # a game, set or match changed hands
            return (before["games_p1"], before["games_p2"], before["sets"]) != \
                (after["games_p1"], after["games_p2"], after["sets"]) or bool(after["winner"])
        return ev.type in self.material_types

    def reduce(self, u: dict[str, Any], ev: NormalizedGameEvent) -> None:
        d, t = ev.data, ev.type
        if t == "SNAPSHOT":
            for k in FORMAT_KEYS:
                if k in d:
                    u[k] = d[k]
            sets = tuple(tuple(int(x) for x in s) for s in d.get("sets", ()))
            u.update(sets=sets, games_p1=int(d.get("games", (0, 0))[0]),
                     games_p2=int(d.get("games", (0, 0))[1]),
                     points_p1=int(d.get("points", (0, 0))[0]),
                     points_p2=int(d.get("points", (0, 0))[1]),
                     in_tiebreak=bool(d.get("in_tiebreak", False)),
                     tiebreak_target=d.get("tiebreak_target"),
                     tiebreak_first_server=d.get("tiebreak_first_server"),
                     server=d.get("server"), suspended=bool(d.get("suspended", False)))
            if d.get("winner"):
                u.update(winner=d["winner"], termination=d.get("termination", "COMPLETED"),
                         is_final=True)
        elif t == "POINT":
            w = d.get("winner")
            if w not in (P1, P2):
                raise InvalidEvent("POINT needs winner P1/P2")
            if u["is_final"]:
                raise InvalidEvent("point after the match ended")
            st = TennisState(snapshot_id="x", game_id=self.game_id, source="x",
                             source_status=ev.source_status, as_of_event_time=None,
                             as_of_received_time=ev.received_time,
                             last_material_event_time=None, last_material_event_kind=None,
                             applied_seq=None, **{k: v for k, v in u.items()
                                                  if k not in ("as_of_event_time",
                                                               "as_of_received_time",
                                                               "last_material_event_time",
                                                               "last_material_event_kind",
                                                               "applied_seq")})
            fmt = st.fmt()
            if fmt is None:
                add_flag(u, "FORMAT_UNKNOWN")
                return
            if st.server is None:
                add_flag(u, "SERVER_UNKNOWN")
                return
            sc = start_set(fmt, st.score())
            r = play_point(fmt, sc, w)
            _apply_score(u, r.score)
            self.last_labels.extend(r.labels)
            exp = d.get("expect")  # provider's own score after the point, when sent
            if exp is not None:
                mine = {"games": [r.score.games[0], r.score.games[1]],
                        "sets": [list(x) for x in r.score.sets]}
                theirs = {"games": list(exp.get("games", mine["games"])),
                          "sets": [list(x) for x in exp.get("sets", mine["sets"])]}
                if mine != theirs:
                    add_flag(u, "SCORE_MISMATCH")
        elif t == "SERVER":
            if d.get("server") not in (P1, P2):
                raise InvalidEvent("SERVER needs P1/P2")
            u["server"] = d["server"]
            drop_flag(u, "SERVER_UNKNOWN")
        elif t in ("RETIRED", "WALKOVER", "DEFAULTED"):
            quitter = d.get("player")
            if quitter not in (P1, P2):
                raise InvalidEvent(f"{t} needs player P1/P2")
            u.update(winner=other(quitter), termination=t if t != "RETIRED" else "RETIRED",
                     is_final=True)
            self.last_labels.append(f"{t.title()}: {quitter}")
        elif t == "SUSPENDED":
            u["suspended"] = True
            self.last_labels.append("Match suspended")
        elif t == "RESUMED":
            u["suspended"] = False
            self.last_labels.append("Match resumed")
        elif t == "MATCH_END":
            w = d.get("winner")
            if u["winner"] is not None and w != u["winner"]:
                add_flag(u, "SCORE_MISMATCH")
            elif u["winner"] is None:
                u.update(winner=w, termination=d.get("termination", "COMPLETED"),
                         is_final=True)
        else:
            raise InvalidEvent(f"unknown tennis event {t}")


# ------------------------------------------------------------------ strength data


@dataclass
class TennisStrengthBook:
    """Serve/return rates per (player, surface) and surface averages.

    ``label`` describes provenance ("SYNTHETIC" for fixtures). Real rates must come
    from licensed historical point data with point-in-time cut-offs.
    """

    rates: dict[tuple[str, str], PlayerRates]
    averages: dict[str, SurfaceAverages]
    label: str
    as_of: str | None = None

    @classmethod
    def from_meta(cls, meta: dict) -> TennisStrengthBook:
        rates = {(r["player"], r["surface"]): PlayerRates(r["serve_won"], r["serve_n"],
                                                          r["return_won"], r["return_n"])
                 for r in meta.get("rates", [])}
        avgs = {k: SurfaceAverages(v["serve_won"], v.get("shrink_points", 400))
                for k, v in meta.get("averages", {}).items()}
        return cls(rates, avgs, meta.get("label", "UNKNOWN"), meta.get("as_of"))


@dataclass
class TennisForecaster:
    strength: TennisStrengthBook
    model_version: str = "tennis_markov_v1"
    validity: timedelta = timedelta(seconds=90)

    @property
    def status(self) -> ModelStatus:
        return ModelStatus.SYNTHETIC_ONLY if self.strength.label == "SYNTHETIC" \
            else ModelStatus.UNVALIDATED

    def inputs(self, state: TennisState | None, game: Game) -> tuple | str:
        src = state if state is not None else None
        details = game.details
        surface = (src.surface if src else None) or details.get("surface")
        fmt = src.fmt() if src else None
        if fmt is None:
            try:
                fmt = Format(details["best_of"], details["final_set"], details["no_ad"],
                             details.get("tiebreak_at", 6), bool(details.get("doubles")))
            except (KeyError, ValueError):
                return "CRITICAL:FORMAT_UNKNOWN"
        if fmt.doubles:
            return "DOUBLES_MODEL_NOT_ENABLED"
        if surface is None:
            return "CRITICAL:SURFACE_UNKNOWN"
        avg = self.strength.averages.get(surface)
        r1 = self.strength.rates.get((game.home_team, surface))
        r2 = self.strength.rates.get((game.away_team, surface))
        missing = [n for n, v in (("surface_average", avg), (f"rates:{game.home_team}", r1),
                                  (f"rates:{game.away_team}", r2)) if v is None]
        if missing:
            return "STRENGTH_DATA_MISSING:" + ",".join(missing)
        return fmt, surface, avg, r1, r2

    def p1_probability(self, state: TennisState | None, game: Game
                       ) -> tuple[float, float, float] | str:
        got = self.inputs(state, game)
        if isinstance(got, str):
            return got
        fmt, _surface, avg, r1, r2 = got
        if state is None or state.server is None:
            # first server unknown (pre-match): average the two possibilities
            a = band(fmt, Score(server=P1), r1, r2, avg)
            b = band(fmt, Score(server=P2), r1, r2, avg)
            return tuple((x + y) / 2 for x, y in zip(a, b, strict=True))  # type: ignore[return-value]
        if state.suspended:
            return "MATCH_SUSPENDED"
        sc = start_set(fmt, state.score())
        return band(fmt, sc, r1, r2, avg)

    def predict(self, state, game: Game, selection: str, prior, rule: SettlementRule,
                now: datetime) -> Prediction | str:
        out = self.p1_probability(state, game)
        if isinstance(out, str):
            return out
        p, lo, hi = out
        if selection == game.away_team:
            p, lo, hi = 1 - p, 1 - hi, 1 - lo
        snap = state.snapshot_id if state is not None else f"pregame:{game.game_id}"
        return Prediction(
            prediction_id=stable_id("pred", [snap, self.model_version, selection]),
            game_id=game.game_id, snapshot_id=snap, selection_team=selection,
            settlement_rule=rule, model_version=self.model_version, model_status=self.status,
            feature_version="tennis_score_v1", probability=float(p),
            probability_low=float(min(lo, p)), probability_high=float(max(hi, p)),
            reliability="NONE" if self.status != ModelStatus.VALIDATED else "MEDIUM",
            created_time=now, valid_until=now + self.validity)


# ------------------------------------------------------------------ adapter

POINT_NAMES = {0: "0", 1: "15", 2: "30", 3: "40"}


def point_text(a: int, b: int, in_tb: bool) -> str:
    if in_tb:
        return f"{a}-{b}"
    if a >= 3 and b >= 3:
        return "Deuce" if a == b else ("Ad P1" if a > b else "Ad P2")
    return f"{POINT_NAMES.get(a, a)}-{POINT_NAMES.get(b, b)}"


@dataclass
class TennisAdapter:
    sport: Sport = Sport.TENNIS
    default_rule: SettlementRule = SettlementRule.TENNIS_MATCH_RETIREMENT_ADVANCER_WINS

    def new_reducer(self, game: Game) -> TennisReducer:
        return TennisReducer(game.game_id, game.home_team, game.away_team,
                             details=dict(game.details))

    def final_outcome(self, state: TennisState, game: Game, data: dict) -> FinalOutcome:
        w = {P1: game.home_team, P2: game.away_team}.get(state.winner or "")
        return FinalOutcome("FINAL", w, False, f"{state.termination or 'UNKNOWN'}",
                            (("termination", state.termination),))

    def scoreboard(self, state: TennisState | None, game: Game) -> dict:
        names = game.names
        if state is None:
            return {"kind": "tennis", "status": "SCHEDULED", "players": [
                names.get(game.home_team, game.home_team),
                names.get(game.away_team, game.away_team)]}
        return {
            "kind": "tennis",
            "status": "FINAL" if state.is_final else "SUSPENDED" if state.suspended else "LIVE",
            "players": [names.get(game.home_team, game.home_team),
                        names.get(game.away_team, game.away_team)],
            "sets": [list(s) for s in state.sets],
            "games": [state.games_p1, state.games_p2],
            "points": point_text(state.points_p1, state.points_p2, state.in_tiebreak),
            "in_tiebreak": state.in_tiebreak,
            "server": state.server,
            "winner": state.winner,
            "termination": state.termination,
            "format": {"best_of": state.best_of, "final_set": state.final_set,
                       "no_ad": state.no_ad, "doubles": state.doubles,
                       "surface": state.surface},
        }

    def features(self, state, game, selection, prior) -> dict[str, float | None]:
        if state is None:
            return {}
        own = 1 if selection == game.home_team else 2
        s = state
        sets_won = sum(1 for a, b in s.sets if (a > b) == (own == 1))
        return {"sets_won": float(sets_won), "sets_lost": float(len(s.sets) - sets_won),
                "games_won": float(s.games_p1 if own == 1 else s.games_p2),
                "games_lost": float(s.games_p2 if own == 1 else s.games_p1),
                "serving": None if s.server is None else float(
                    (s.server == P1) == (own == 1)),
                "in_tiebreak": float(s.in_tiebreak)}


register(TennisAdapter())
