"""NFL: state, reducer, rules, features, forecaster and display.

Rules encoded (ASSUMED from general knowledge, label UNVERIFIED until checked against
the current NFL rulebook for the season in ``game.details['rules_version']``):
* four 15-minute quarters;
* regular season: one 10-minute overtime period; the game can end TIED;
* postseason: overtime periods continue until there is a winner.

Because regular-season ties exist, the forecast is a three-way distribution
(win / tie / loss). A contract's settlement rule decides what a tie does:
``NFL_INCL_OT_TIE_VOID`` refunds, ``NFL_INCL_OT_TIE_LOSES`` pays nothing. The
two team probabilities are therefore NOT forced to sum to one.

Field position: ``yardline_100`` = yards the team in possession needs to reach the
opponent's goal line (1..99).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal

from sports_edge.domain.enums import ModelStatus, SettlementRule, Sport
from sports_edge.domain.records import (
    FeatureVector,
    Game,
    GameStateBase,
    ModelVersion,
    Prediction,
    stable_id,
)
from sports_edge.ingest.nhl_state import InvalidEvent, NormalizedGameEvent
from sports_edge.sports.base import BaseReducer, FinalOutcome, add_flag, drop_flag, register

QUARTER_SECONDS = 15 * 60
REG_OT_SECONDS = 10 * 60
POST_OT_SECONDS = 15 * 60


class NFLState(GameStateBase):
    quarter: int  # 1-4 regulation, 5+ overtime
    seconds_remaining_in_quarter: int | None
    home_score: int
    away_score: int
    possession: str | None
    down: int | None
    distance: int | None
    yardline_100: int | None
    home_timeouts: int | None
    away_timeouts: int | None
    season_type: Literal["REGULAR", "POSTSEASON"] | None
    home_qb: str | None
    away_qb: str | None
    final_decided_in: Literal["REG", "OT"] | None = None
    tie: bool = False

    def coherent(self) -> bool:
        if self.home_score < 0 or self.away_score < 0 or self.quarter < 1:
            return False
        if self.down is not None and not 1 <= self.down <= 4:
            return False
        if self.yardline_100 is not None and not 1 <= self.yardline_100 <= 99:
            return False
        for t in (self.home_timeouts, self.away_timeouts):
            if t is not None and not 0 <= t <= 3:
                return False
        if self.season_type == "REGULAR" and self.quarter > 5:
            return False  # one OT period in the regular season
        if self.tie and self.home_score != self.away_score:
            return False
        return True

    def material_key(self) -> tuple:
        return (self.home_score, self.away_score, self.possession, self.down, self.distance,
                self.yardline_100, self.quarter, self.home_qb, self.away_qb, self.is_final)


def seconds_remaining_regulation(s: NFLState) -> int | None:
    if s.seconds_remaining_in_quarter is None:
        return None
    if s.quarter >= 5:
        return 0
    return (4 - s.quarter) * QUARTER_SECONDS + s.seconds_remaining_in_quarter


@dataclass
class NFLReducer(BaseReducer):
    season_type: str | None = None

    state_cls: ClassVar = NFLState
    prefix: ClassVar[str] = "nfl"
    material_types: ClassVar[frozenset[str]] = frozenset(
        {"SCORE", "TURNOVER", "QB_CHANGE", "SNAPSHOT", "GAME_END", "PENALTY"})

    def initial_fields(self) -> dict[str, Any]:
        return dict(quarter=1, seconds_remaining_in_quarter=QUARTER_SECONDS, home_score=0,
                    away_score=0, possession=None, down=None, distance=None,
                    yardline_100=None, home_timeouts=3, away_timeouts=3,
                    season_type=self.season_type, home_qb=None, away_qb=None)

    def material(self, ev, before, after) -> bool:
        if ev.type == "PLAY" and before["possession"] != after["possession"]:
            return True
        return ev.type in self.material_types

    def _team(self, d: dict, key: str = "team") -> str:
        t = d.get(key)
        if t not in (self.home, self.away):
            raise InvalidEvent(f"unknown team {t!r}")
        return t

    def reduce(self, u: dict[str, Any], ev: NormalizedGameEvent) -> None:
        d, t = ev.data, ev.type
        for k in ("quarter", "seconds_remaining_in_quarter"):
            if k in d:
                u[k] = d[k]
        if u["seconds_remaining_in_quarter"] is not None and \
                not 0 <= u["seconds_remaining_in_quarter"] <= QUARTER_SECONDS:
            raise InvalidEvent("invalid clock")
        if t == "SNAPSHOT":
            for k in ("home_score", "away_score", "possession", "down", "distance",
                      "yardline_100", "home_timeouts", "away_timeouts", "season_type",
                      "home_qb", "away_qb"):
                if k in d:
                    u[k] = d[k]
        elif t == "PLAY":
            for k in ("possession", "down", "distance", "yardline_100"):
                if k in d:
                    u[k] = d[k]
            if u["possession"] is not None and u["possession"] not in (self.home, self.away):
                raise InvalidEvent("unknown possession team")
        elif t == "SCORE":
            team = self._team(d)
            pts = int(d["points"])
            if pts not in (1, 2, 3, 6):
                raise InvalidEvent(f"impossible score value {pts}")
            u["home_score" if team == self.home else "away_score"] += pts
            kind = d.get("kind", {6: "TD", 3: "FG", 2: "SAFETY/2PT", 1: "PAT"}[pts])
            self.last_labels.append(f"{kind} {team} (+{pts})")
        elif t == "TURNOVER":
            team = self._team(d, "gaining_team")
            u["possession"] = team
            u.update(down=1, distance=10, yardline_100=d.get("yardline_100"))
            self.last_labels.append(f"Turnover: {team} ball ({d.get('kind', 'turnover')})")
        elif t == "TIMEOUT":
            team = self._team(d)
            k = "home_timeouts" if team == self.home else "away_timeouts"
            if u[k] is None or u[k] <= 0:
                add_flag(u, "TIMEOUT_COUNT_UNKNOWN")
                u[k] = None
            else:
                u[k] -= 1
        elif t == "QB_CHANGE":
            team = self._team(d)
            k = "home_qb" if team == self.home else "away_qb"
            u[k] = d.get("qb")
            flag = f"QB_UNCONFIRMED_{team}"
            if d.get("qb") is None:
                add_flag(u, flag)
            else:
                drop_flag(u, flag)
            self.last_labels.append(f"QB change {team}: {d.get('qb') or 'UNKNOWN'}")
        elif t == "PENALTY":
            self.last_labels.append(f"Penalty {d.get('team', '')}: {d.get('desc', '')}".strip())
            for k in ("down", "distance", "yardline_100"):
                if k in d:
                    u[k] = d[k]
        elif t == "QUARTER_END":
            q = u["quarter"]
            if q == 2:  # halftime: timeouts reset
                u["home_timeouts"] = u["away_timeouts"] = 3
            u["quarter"] = q + 1
            u["seconds_remaining_in_quarter"] = (
                QUARTER_SECONDS if q + 1 <= 4 else
                REG_OT_SECONDS if u["season_type"] == "REGULAR" else POST_OT_SECONDS)
        elif t == "GAME_END":
            u["is_final"] = True
            u["final_decided_in"] = "OT" if u["quarter"] >= 5 else "REG"
            u["tie"] = u["home_score"] == u["away_score"]
            if u["tie"] and u["season_type"] == "POSTSEASON":
                raise InvalidEvent("a postseason game cannot end tied")
        else:
            raise InvalidEvent(f"unknown NFL event {t}")
        if u["quarter"] >= 5 and not u["is_final"] and u["season_type"] is None:
            add_flag(u, "SEASON_TYPE_UNKNOWN")  # OT rules depend on it


# ------------------------------------------------------------------ features

FEATURE_VERSION = "nfl_v1"
FEATURE_NAMES = ("score_diff", "frac_remaining", "score_diff_x_time", "has_ball",
                 "field_value", "down_distance_value", "timeouts_diff", "is_home", "in_ot",
                 "pregame_logit")
CRITICAL = ("score_diff", "frac_remaining")
REG_SECONDS = 4 * QUARTER_SECONDS


def build_features(s: NFLState, game: Game, selection: str, prior: float | None
                   ) -> FeatureVector:
    home = selection == game.home_team
    diff = s.home_score - s.away_score
    if not home:
        diff = -diff
    secs = seconds_remaining_regulation(s)
    frac = None if secs is None else secs / REG_SECONDS
    if s.quarter >= 5 and s.seconds_remaining_in_quarter is not None:
        frac = 0.0
    has = None if s.possession is None else (1.0 if s.possession == selection else -1.0)
    field_value = None if has is None or s.yardline_100 is None else \
        has * (100 - s.yardline_100) / 100
    dd = None
    if has is not None and s.down is not None and s.distance is not None:
        dd = has * (1.0 - 0.15 * (s.down - 1)) / math.sqrt(max(1, s.distance))
    own_to, opp_to = (s.home_timeouts, s.away_timeouts) if home else \
        (s.away_timeouts, s.home_timeouts)
    vals: dict[str, float | None] = {
        "score_diff": float(diff),
        "frac_remaining": frac,
        "score_diff_x_time": None if frac is None else diff / math.sqrt(frac + 0.01),
        "has_ball": has,
        "field_value": field_value,
        "down_distance_value": dd,
        "timeouts_diff": None if own_to is None or opp_to is None else float(own_to - opp_to),
        "is_home": 1.0 if home else 0.0,
        "in_ot": 1.0 if s.quarter >= 5 else 0.0,
        "pregame_logit": None if prior is None else math.log(prior / (1 - prior)),
    }
    return FeatureVector(
        feature_id=stable_id("feat", [s.snapshot_id, selection, prior]),
        snapshot_id=s.snapshot_id, feature_version=FEATURE_VERSION, values=vals,
        missing=tuple(k for k, v in vals.items() if v is None))


# ------------------------------------------------------------------ forecaster


@dataclass
class NFLForecaster:
    """Two calibrated state models: P(win) and P(tie). P(loss) is the remainder."""

    win_model: Any  # forecast.models.StateModel
    tie_model: Any | None
    version: ModelVersion
    validity: timedelta = timedelta(seconds=60)

    @property
    def model_version(self) -> str:
        return self.version.model_version

    def distribution(self, s: NFLState, game: Game, selection: str, prior: float | None
                     ) -> tuple[tuple[float, float, float], float, float] | str:
        import numpy as np

        fv = build_features(s, game, selection, prior)
        crit = [c for c in CRITICAL if fv.values.get(c) is None]
        if crit:
            return "CRITICAL_FEATURE_MISSING:" + ",".join(crit)
        missing = [f for f in self.win_model.feature_names if fv.values.get(f) is None]
        if missing:
            return "MODEL_FEATURE_MISSING:" + ",".join(missing)
        x = np.array([[float(fv.values[f]) for f in self.win_model.feature_names]])
        p, lo, hi = (float(a[0]) for a in self.win_model.predict_matrix(x))
        tie = 0.0
        if self.tie_model is not None and s.season_type != "POSTSEASON":
            tie = float(self.tie_model.predict_matrix(x)[0][0])
        tie = min(tie, 1.0 - p)
        return (p, lo, hi), tie, 1.0 - p - tie

    def predict(self, state, game: Game, selection: str, prior, rule: SettlementRule,
                now: datetime) -> Prediction | str:
        if state is None:
            return "PREGAME_HANDLED_BY_PRIOR"
        if state.season_type is None:
            return "CRITICAL:SEASON_TYPE_UNKNOWN"
        out = self.distribution(state, game, selection, prior)
        if isinstance(out, str):
            return out
        (p, lo, hi), tie, _loss = out
        void = tie if rule == SettlementRule.NFL_INCL_OT_TIE_VOID else 0.0
        return Prediction(
            prediction_id=stable_id("pred", [state.snapshot_id, self.model_version, selection,
                                             rule.value]),
            game_id=game.game_id, snapshot_id=state.snapshot_id, selection_team=selection,
            settlement_rule=rule, model_version=self.model_version,
            model_status=self.version.status, feature_version=FEATURE_VERSION,
            probability=p, probability_low=min(lo, p), probability_high=max(hi, p),
            reliability="NONE" if self.version.status != ModelStatus.VALIDATED else "MEDIUM",
            created_time=now, valid_until=now + self.validity, probability_void=void)


# ------------------------------------------------------------------ adapter


def _clock(secs: int | None) -> str:
    return "--:--" if secs is None else f"{secs // 60}:{secs % 60:02d}"


@dataclass
class NFLAdapter:
    sport: Sport = Sport.NFL
    default_rule: SettlementRule = SettlementRule.NFL_INCL_OT_TIE_VOID

    def new_reducer(self, game: Game) -> NFLReducer:
        return NFLReducer(game.game_id, game.home_team, game.away_team,
                          season_type=game.details.get("season_type"))

    def final_outcome(self, state: NFLState, game: Game, data: dict) -> FinalOutcome:
        if state.tie:
            return FinalOutcome("FINAL", None, True, "tied after overtime")
        w = game.home_team if state.home_score > state.away_score else game.away_team
        return FinalOutcome("FINAL", w, False, f"decided in {state.final_decided_in}")

    def scoreboard(self, state: NFLState | None, game: Game) -> dict:
        if state is None:
            return {"kind": "nfl", "status": "SCHEDULED"}
        q = state.quarter
        dd = None
        if state.down and state.distance is not None and state.yardline_100 is not None:
            side = "opp" if state.yardline_100 < 50 else "own"
            yl = state.yardline_100 if state.yardline_100 <= 50 else 100 - state.yardline_100
            dd = f"{state.down}{['st', 'nd', 'rd', 'th'][state.down - 1]} & " \
                 f"{'Goal' if state.distance >= state.yardline_100 else state.distance} " \
                 f"at {side} {yl}"
        return {"kind": "nfl", "status": "FINAL" if state.is_final else "LIVE",
                "score": [state.away_score, state.home_score],
                "period": f"Q{q}" if q <= 4 else ("OT" if q == 5 else f"{q - 4}OT"),
                "clock": _clock(state.seconds_remaining_in_quarter),
                "possession": state.possession, "down_distance": dd,
                "timeouts": [state.away_timeouts, state.home_timeouts],
                "qbs": [state.away_qb, state.home_qb], "tie": state.tie,
                "season_type": state.season_type}

    def features(self, state, game, selection, prior) -> dict[str, float | None]:
        if state is None:
            return {}
        return dict(build_features(state, game, selection, prior).values)


register(NFLAdapter())
