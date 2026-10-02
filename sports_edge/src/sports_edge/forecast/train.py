"""Training entry point: chronological split -> fit -> calibrate -> evaluate -> artifact.

With ``--synthetic`` the data come from ``forecast.synthetic`` and the
resulting model is ``SYNTHETIC_ONLY``: it exercises the pipeline but can
never pass the model gate in normal operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from sports_edge.domain.enums import ModelStatus, SettlementRule
from sports_edge.domain.records import ModelVersion
from sports_edge.evaluation.metrics import brier, by_phase, clustered_bootstrap, log_loss
from sports_edge.evaluation.splits import ExperimentLog, chronological_split
from sports_edge.features.nhl import CRITICAL, FEATURE_NAMES, FEATURE_VERSION
from sports_edge.forecast.models import ChainForecaster, StateModel, TrainedForecaster
from sports_edge.forecast.synthetic import simulate_season

REDUCED = tuple(CRITICAL) + ("score_diff_x_time", "is_home", "in_overtime")


@dataclass
class TrainReport:
    forecaster: ChainForecaster
    test_metrics: dict


def _version(name: str, kind: str, status: ModelStatus, features: tuple[str, ...], split,
             params: dict, data_desc: str) -> ModelVersion:
    rng = lambda d: (str(d["game_start"].min()), str(d["game_start"].max()))  # noqa: E731
    return ModelVersion(model_version=name, kind=kind, status=status,
                        feature_version=FEATURE_VERSION, uses_market_inputs=False,
                        trained_on=data_desc, train_range=rng(split.train),
                        validation_range=rng(split.validation),
                        hyperparameters={"features": list(features), **params},
                        calibration="sigmoid (Platt) on chronological validation window")


def train_nhl(df: pd.DataFrame, data_desc: str, status: ModelStatus, kind: str = "logistic",
              log_path: Path | None = None, n_bootstrap: int = 10) -> TrainReport:
    split = chronological_split(df)
    params = {"C": 1.0} if kind == "logistic" else {"n_estimators": 150}
    full = StateModel(kind, tuple(FEATURE_NAMES), params, n_bootstrap=n_bootstrap).fit(
        split.train, split.validation)
    reduced = StateModel(kind, REDUCED, params, n_bootstrap=n_bootstrap).fit(
        split.train, split.validation)
    tag = "syn" if status == ModelStatus.SYNTHETIC_ONLY else "v"
    f_full = TrainedForecaster(full, _version(f"nhl_{kind}_full_{tag}1", kind, status,
                                              FEATURE_NAMES, split, params, data_desc),
                               SettlementRule.NHL_INCLUDING_OT_SO, timedelta(seconds=90))
    f_red = TrainedForecaster(reduced, _version(f"nhl_{kind}_reduced_{tag}1", kind, status,
                                                REDUCED, split, params, data_desc),
                              SettlementRule.NHL_INCLUDING_OT_SO, timedelta(seconds=90))
    test = split.test
    p, _, _ = full.predict_matrix(test[list(FEATURE_NAMES)].to_numpy(float))
    scored = test.assign(p=p)
    metrics = {
        "brier": clustered_bootstrap(scored, brier).__dict__,
        "log_loss": clustered_bootstrap(scored, log_loss).__dict__,
        "by_phase": by_phase(scored).to_dict(orient="records"),
        "test_games": int(test["game_id"].nunique()),
    }
    if log_path:
        ExperimentLog(log_path).log("TRAIN_EVAL", model=f_full.version.model_version,
                                    data=data_desc, metrics=metrics)
    return TrainReport(ChainForecaster([f_full, f_red]), metrics)


def train_synthetic(n_games: int = 600, seed: int = 0, kind: str = "logistic",
                    log_path: Path | None = None) -> TrainReport:
    df = simulate_season(n_games, seed=seed)
    return train_nhl(df, f"SYNTHETIC simulate_season(n={n_games}, seed={seed})",
                     ModelStatus.SYNTHETIC_ONLY, kind, log_path)
