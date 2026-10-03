"""NHL adapter: wraps the original NHL reducer, features and calibrated state models.

The NHL path predates the multi-sport layer; its behaviour is unchanged. Rules
(regulation / OT / shootout) are handled in ``ingest/nhl_state.py`` and settlement
in ``contracts/settlement.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sports_edge.domain.enums import SettlementRule, Sport
from sports_edge.domain.records import Game, NHLState, Prediction
from sports_edge.features.nhl import build_features
from sports_edge.ingest.nhl_state import NHLStateReducer, NormalizedGameEvent
from sports_edge.sports.base import FinalOutcome, register


@dataclass
class NHLForecaster:
    """Adapts a ``ChainForecaster`` (full model -> reduced model -> abstain)."""

    chain: object  # forecast.models.ChainForecaster

    @property
    def model_version(self) -> str:
        return self.chain.version.model_version  # type: ignore[attr-defined]

    @property
    def version(self):
        return self.chain.version  # type: ignore[attr-defined]

    def predict(self, state: NHLState | None, game: Game, selection: str,
                prior: float | None, rule: SettlementRule, now: datetime) -> Prediction | str:
        if state is None:
            return "PREGAME_HANDLED_BY_PRIOR"
        fv = build_features(state, selection == game.home_team, prior)
        return self.chain.predict(fv, game.game_id, selection, now)  # type: ignore[attr-defined]


def _clock(secs: int | None) -> str:
    return "--:--" if secs is None else f"{secs // 60}:{secs % 60:02d}"


@dataclass
class NHLAdapter:
    sport: Sport = Sport.NHL
    default_rule: SettlementRule = SettlementRule.NHL_INCLUDING_OT_SO

    def new_reducer(self, game: Game) -> NHLStateReducer:
        return NHLStateReducer(game.game_id, game.home_team, game.away_team)

    def final_outcome(self, state: NHLState, game: Game, data: dict) -> FinalOutcome:
        w = game.home_team if state.home_score > state.away_score else \
            game.away_team if state.away_score > state.home_score else None
        return FinalOutcome("FINAL", w, False, f"decided in {state.final_decided_in}",
                            (("regulation_home", data.get("regulation_home_score")),
                             ("regulation_away", data.get("regulation_away_score"))))

    def scoreboard(self, state: NHLState | None, game: Game) -> dict:
        if state is None:
            return {"kind": "nhl", "status": "SCHEDULED"}
        per = state.period
        return {"kind": "nhl", "status": "FINAL" if state.is_final else "LIVE",
                "score": [state.away_score, state.home_score],
                "period": str(per) if per <= 3 else ("OT" if per == 4 else "SO"),
                "clock": _clock(state.seconds_remaining_in_period),
                "skaters": [state.away_skaters, state.home_skaters],
                "goalies": [state.away_goalie, state.home_goalie],
                "net_empty": [state.away_net_empty, state.home_net_empty],
                "decided_in": state.final_decided_in}

    def annotations(self, ev: NormalizedGameEvent, game: Game) -> list[str]:
        d = ev.data
        if ev.type == "GOAL":
            return [f"Goal {d.get('team')}"]
        if ev.type == "PENALTY":
            return [f"Penalty {d.get('team')}"]
        if ev.type == "GOALIE_CHANGE":
            return [f"Goalie change {d.get('team')}: {d.get('goalie') or 'UNKNOWN'}"]
        if ev.type == "EMPTY_NET_START":
            return [f"Empty net {d.get('team')}"]
        return []

    def features(self, state, game, selection, prior) -> dict[str, float | None]:
        if state is None:
            return {}
        return dict(build_features(state, selection == game.home_team, prior).values)


register(NHLAdapter())
