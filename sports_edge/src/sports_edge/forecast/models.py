"""Probability models.

Three families, compared on the same chronological splits:

A. ``MarketImpliedBaseline``: the same-time market's own (margin-removed)
   probability. Any model must beat this to be worth anything.
B. ``StateModel(kind="logistic")``: L2-regularized logistic regression on
   game state + pregame strength. Transparent.
C. ``StateModel(kind="gbm")``: LightGBM challenger.

Both B and C are calibrated on a *later* chronological validation window
(never the training rows) and carry a game-clustered bootstrap ensemble so
each forecast comes with a defensible uncertainty band. The band reflects
parameter uncertainty from finite training data; it is not a guarantee.

Odds-free and market-informed variants are distinguished by
``uses_market_inputs`` on the ModelVersion. A market-informed model is not
independent confirmation of the market it ingests.
"""

from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from sports_edge.domain.enums import ModelStatus, SettlementRule
from sports_edge.domain.records import FeatureVector, ModelVersion, Prediction, stable_id
from sports_edge.features.nhl import critical_missing

Kind = Literal["logistic", "gbm"]


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _make_estimator(kind: Kind, params: dict[str, Any], seed: int):
    if kind == "logistic":
        return LogisticRegression(C=params.get("C", 1.0), max_iter=2000)
    from lightgbm import LGBMClassifier

    return LGBMClassifier(
        n_estimators=params.get("n_estimators", 200),
        learning_rate=params.get("learning_rate", 0.05),
        num_leaves=params.get("num_leaves", 15),
        min_child_samples=params.get("min_child_samples", 50),
        reg_lambda=params.get("reg_lambda", 1.0),
        random_state=seed,
        verbose=-1,
    )


@dataclass
class StateModel:
    kind: Kind
    feature_names: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    # Platt (sigmoid) scaling is smooth and stable on small validation windows;
    # isotonic needs much more validation data to avoid coarse steps.
    calibration: Literal["sigmoid", "isotonic", "none"] = "sigmoid"
    n_bootstrap: int = 10
    seed: int = 7
    members: list[Any] = field(default_factory=list)
    calibrator: IsotonicRegression | LogisticRegression | None = None

    def fit(self, train: pd.DataFrame, val: pd.DataFrame | None, label: str = "y",
            game_col: str = "game_id") -> StateModel:
        rng = np.random.default_rng(self.seed)
        games = train[game_col].unique()
        x, y = train[list(self.feature_names)].to_numpy(float), train[label].to_numpy(int)
        self.members = [_make_estimator(self.kind, self.params, self.seed).fit(x, y)]
        by_game = {g: idx for g, idx in train.groupby(game_col).indices.items()}
        for b in range(self.n_bootstrap):
            # resample whole games, not rows: rows within a game are highly correlated
            pick = rng.choice(games, size=len(games), replace=True)
            idx = np.concatenate([by_game[g] for g in pick])
            if len(np.unique(y[idx])) < 2:
                continue
            est = _make_estimator(self.kind, self.params, self.seed + b + 1)
            self.members.append(est.fit(x[idx], y[idx]))
        if self.calibration != "none" and val is not None and len(val):
            raw = self._raw(val[list(self.feature_names)].to_numpy(float))[:, 0]
            yv = val[label].to_numpy(int)
            if self.calibration == "isotonic":
                self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                self.calibrator.fit(raw, yv)
            else:
                self.calibrator = LogisticRegression(C=1e6, max_iter=1000)
                self.calibrator.fit(_logit(raw)[:, None], yv)
        return self

    def _calibrate(self, col: np.ndarray) -> np.ndarray:
        if isinstance(self.calibrator, IsotonicRegression):
            return self.calibrator.predict(col)
        if isinstance(self.calibrator, LogisticRegression):
            return self.calibrator.predict_proba(_logit(col)[:, None])[:, 1]
        return col

    def _raw(self, x: np.ndarray) -> np.ndarray:
        if self.kind == "logistic" and all(isinstance(m, LogisticRegression)
                                           for m in self.members):
            # same arithmetic as predict_proba, without sklearn's per-call validation cost
            w = np.column_stack([m.coef_[0] for m in self.members])
            b = np.array([m.intercept_[0] for m in self.members])
            return 1.0 / (1.0 + np.exp(-(x @ w + b)))
        return np.column_stack([m.predict_proba(x)[:, 1] for m in self.members])

    def predict_matrix(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        raw = self._raw(x)
        if self.calibrator is not None:
            raw = np.column_stack([self._calibrate(raw[:, j]) for j in range(raw.shape[1])])
        raw = np.clip(raw, 1e-4, 1 - 1e-4)
        point = raw[:, 0]
        if raw.shape[1] > 2:
            lo, hi = np.quantile(raw[:, 1:], [0.1, 0.9], axis=1)
        else:
            lo = hi = point
        return point, np.minimum(lo, point), np.maximum(hi, point)


@dataclass
class TrainedForecaster:
    """A fitted model plus the metadata needed to reproduce and audit it."""

    model: StateModel
    version: ModelVersion
    settlement_rule: SettlementRule
    validity: timedelta = timedelta(seconds=90)

    def predict(self, fv: FeatureVector, game_id: str, selection_team: str,
                now: datetime) -> Prediction | str:
        """Returns a Prediction, or an abstention reason string."""
        if fv.feature_version != self.version.feature_version:
            return "FEATURE_VERSION_MISMATCH"
        if critical_missing(fv):
            return "CRITICAL_FEATURE_MISSING:" + ",".join(critical_missing(fv))
        missing = [f for f in self.model.feature_names if fv.values.get(f) is None]
        if missing:
            return "MODEL_FEATURE_MISSING:" + ",".join(missing)
        x = np.array([[float(fv.values[f]) for f in self.model.feature_names]])  # type: ignore[arg-type]
        p, lo, hi = (float(a[0]) for a in self.model.predict_matrix(x))
        width = hi - lo
        reliability = "HIGH" if width < 0.06 else "MEDIUM" if width < 0.12 else "LOW"
        if self.version.status != ModelStatus.VALIDATED:
            reliability = "NONE"
        return Prediction(
            prediction_id=stable_id("pred", [fv.feature_id, self.version.model_version,
                                             selection_team]),
            game_id=game_id,
            snapshot_id=fv.snapshot_id,
            selection_team=selection_team,
            settlement_rule=self.settlement_rule,
            model_version=self.version.model_version,
            model_status=self.version.status,
            feature_version=fv.feature_version,
            probability=p,
            probability_low=lo,
            probability_high=hi,
            reliability=reliability,
            created_time=now,
            valid_until=now + self.validity,
        )


class ChainForecaster:
    """Try the full model first, then a tested reduced-feature model, else abstain."""

    def __init__(self, forecasters: list[TrainedForecaster]) -> None:
        if not forecasters:
            raise ValueError("need at least one forecaster")
        self.forecasters = forecasters

    @property
    def version(self) -> ModelVersion:
        return self.forecasters[0].version

    def predict(self, fv: FeatureVector, game_id: str, selection_team: str,
                now: datetime) -> Prediction | str:
        reasons = []
        for f in self.forecasters:
            out = f.predict(fv, game_id, selection_team, now)
            if isinstance(out, Prediction):
                return out
            reasons.append(out)
            if out.startswith("CRITICAL"):
                break
        return " | ".join(reasons)


def market_implied_baseline(fair_probability: float) -> float:
    """Baseline A: the market's own margin-removed probability at the same time."""
    return fair_probability


# ------------------------------------------------------------------ artifacts


def save_artifact(forecaster: TrainedForecaster, directory: Path) -> TrainedForecaster:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{forecaster.version.model_version}.pkl"
    blob = pickle.dumps(forecaster.model)
    path.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    version = forecaster.version.model_copy(
        update={"artifact_path": str(path), "artifact_sha256": sha}
    )
    (directory / f"{version.model_version}.json").write_text(version.model_dump_json(indent=2))
    return TrainedForecaster(forecaster.model, version, forecaster.settlement_rule,
                             forecaster.validity)


def load_artifact(meta_path: Path, settlement_rule: SettlementRule) -> TrainedForecaster:
    version = ModelVersion.model_validate_json(meta_path.read_text())
    if version.artifact_path is None:
        raise ValueError("model metadata has no artifact path")
    blob = Path(version.artifact_path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != version.artifact_sha256:
        raise ValueError("artifact hash mismatch: refusing to load")
    model = pickle.loads(blob)  # noqa: S301 - only our own hash-checked artifacts
    return TrainedForecaster(model, version, settlement_rule)
