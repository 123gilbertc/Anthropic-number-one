"""Pregame forecaster: turns a pregame prior into a Prediction for pregame entries.

The prior's quality decides everything here, so its status must be stated
honestly: priors from a synthetic fixture are SYNTHETIC_ONLY; priors from a
real pregame model are UNVALIDATED until that model passes holdout checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sports_edge.domain.enums import ModelStatus, SettlementRule
from sports_edge.domain.records import Prediction, stable_id


@dataclass
class PregamePriorForecaster:
    status: ModelStatus
    model_version: str
    settlement_rule: SettlementRule
    half_width: float = 0.04  # stated uncertainty of the prior, probability units
    validity: timedelta = timedelta(seconds=90)

    def predict(self, game_id: str, team: str, prior: float | None,
                now: datetime) -> Prediction | None:
        if prior is None:
            return None
        return Prediction(
            prediction_id=stable_id("pp", [game_id, team, prior, self.model_version]),
            game_id=game_id, snapshot_id=f"pregame:{game_id}", selection_team=team,
            settlement_rule=self.settlement_rule, model_version=self.model_version,
            model_status=self.status, feature_version="pregame_prior_v1", probability=prior,
            probability_low=max(0.0, prior - self.half_width),
            probability_high=min(1.0, prior + self.half_width),
            reliability="NONE" if self.status != ModelStatus.VALIDATED else "LOW",
            created_time=now, valid_until=now + self.validity)
