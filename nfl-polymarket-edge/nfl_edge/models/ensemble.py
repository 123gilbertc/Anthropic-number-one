"""Logistic stacking of component win probabilities with honest walk-forward evaluation.

:class:`LogisticStacker` is an L2-regularised logistic regression on the logits of component
probabilities (market, Elo, EPA, ...). It is fitted with ``scipy.optimize.minimize``
(L-BFGS-B, analytic gradient) and polished with a few exact Newton steps so the coefficients
are reproducible to well below 1e-6. :func:`walk_forward` re-fits the stacker for every test
season using only seasons strictly before it (the look-ahead rule in CONTRACT.md).

Feature convention: ``features`` names probability columns (``market_prob``, ``elo_prob``,
...). The design matrix uses ``f"{name}_logit"`` columns created by :func:`add_logits`
(probabilities clipped to [0.01, 0.99] before the logit). A feature whose name already ends
in ``_logit`` is used as-is, which lets callers stack pre-computed logits or other real-valued
scores. Naming a raw score (``spread_line``, ``elo_diff``, ``epa_diff``, ...) directly as a
feature is an error: :func:`add_logits` rejects any column with non-NaN values outside
[0, 1] rather than clipping it into a meaningless sign feature. Coefficients are keyed by
the design column name (``market_prob_logit``).

Regularisation: the objective is the summed log-loss plus ``l2 / 2 * ||w||^2`` on the
feature weights only (the intercept is free), i.e. sklearn's ``C = 1 / l2``. With ``l2 = 1``
and thousands of games the shrinkage is negligible; raise it to tame collinear components.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit

P_CLIP = 1e-6  # predict_proba output range
LOGIT_CLIP = 0.01  # add_logits clips probabilities to [0.01, 0.99]
_NEWTON_MAX_ITER = 25
_NEWTON_TOL = 1e-12
_MODEL_FORMAT_VERSION = 1


def logit_column(feature: str) -> str:
    """Design-matrix column for a feature: ``name`` -> ``name_logit`` unless already a logit column."""
    return feature if feature.endswith("_logit") else f"{feature}_logit"


def add_logits(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Return a copy of ``df`` with ``f"{col}_logit"`` for each probability column in ``cols``.

    Probabilities are clipped to ``[0.01, 0.99]`` before the logit so a component that is
    (over)confident cannot dominate the fit. NaN probabilities give NaN logits.

    Every column in ``cols`` must be a probability column: all non-NaN values must lie in
    ``[0, 1]``. A raw score (``spread_line``, ``elo_diff``, ``epa_diff``, ...) would otherwise
    be clipped to ``[0.01, 0.99]`` and silently degenerate into a sign feature, so such a
    column raises ``ValueError``. To stack a real-valued score, add it yourself under a name
    ending in ``_logit`` (e.g. ``df["elo_diff_logit"] = df["elo_diff"] / 400``) and name that
    column in ``features``; ``*_logit`` columns bypass this function.
    """
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            raise KeyError(f"column {col!r} not in frame")
        p = out[col].to_numpy(dtype=float)
        _check_probability_column(col, p)
        with np.errstate(invalid="ignore"):
            out[f"{col}_logit"] = logit(np.clip(p, LOGIT_CLIP, 1.0 - LOGIT_CLIP))
    return out


def _check_probability_column(col: str, p: np.ndarray) -> None:
    """Raise ``ValueError`` unless every non-NaN value of ``p`` lies in ``[0, 1]`` (inf counts as out of range)."""
    with np.errstate(invalid="ignore"):
        bad = ~np.isnan(p) & ((p < 0.0) | (p > 1.0))
    if bad.any():
        vals = p[~np.isnan(p)]
        raise ValueError(
            f"column {col!r} is not a probability column: {int(bad.sum())} of {vals.size} non-NaN values "
            f"fall outside [0, 1] (range {np.min(vals):g}..{np.max(vals):g}). Stack a raw score by adding "
            f"it as a '{col}_logit' column instead."
        )


def _prepare(df: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
    """Add missing logit columns for the probability features in ``features``."""
    need = [f for f in features if not f.endswith("_logit")]
    return add_logits(df, need) if need else df


class LogisticStacker:
    """L2-regularised logistic regression on logit-transformed component probabilities.

    Parameters
    ----------
    features:
        Probability columns to stack (or ``*_logit`` columns to use directly).
    l2:
        Ridge penalty on the feature weights (``l2 / 2 * ||w||^2`` added to the summed
        log-loss). Must be >= 0. The intercept is never penalised.
    use_intercept:
        Whether to fit a free intercept.

    Attributes
    ----------
    coef_ : dict[str, float]
        Weight per design column (``feature_logit``), populated by :meth:`fit`.
    intercept_ : float
        Fitted intercept (0.0 when ``use_intercept`` is False).
    n_fit_ : int
        Number of games used by the last fit.
    converged_ : bool
        Whether the optimiser reported convergence.
    n_iter_ : int
        L-BFGS-B iterations used by the last fit.
    """

    def __init__(self, features: list[str], l2: float = 1.0, use_intercept: bool = True) -> None:
        features = list(features)
        if not features:
            raise ValueError("features must not be empty")
        if len(set(features)) != len(features):
            raise ValueError("features must be unique")
        if not (float(l2) >= 0.0):
            raise ValueError("l2 must be >= 0")
        self.features: list[str] = features
        self.l2: float = float(l2)
        self.use_intercept: bool = bool(use_intercept)
        self.coef_: dict[str, float] = {}
        self.intercept_: float = 0.0
        self.n_fit_: int = 0
        self.converged_: bool = False
        self.n_iter_: int = 0

    # ------------------------------------------------------------- properties
    @property
    def design_columns(self) -> list[str]:
        """Design-matrix column names, in coefficient order."""
        return [logit_column(f) for f in self.features]

    @property
    def is_fitted(self) -> bool:
        return len(self.coef_) == len(self.features)

    def __repr__(self) -> str:
        return f"LogisticStacker(features={self.features!r}, l2={self.l2!r}, use_intercept={self.use_intercept!r})"

    # ------------------------------------------------------------- design matrix
    def _design(self, df: pd.DataFrame) -> np.ndarray:
        prepared = _prepare(df, self.features)
        missing = [c for c in self.design_columns if c not in prepared.columns]
        if missing:
            raise KeyError(f"frame lacks design columns {missing}")
        return prepared[self.design_columns].to_numpy(dtype=float)

    # ------------------------------------------------------------- fitting
    def fit(self, df: pd.DataFrame, y_col: str = "home_win") -> "LogisticStacker":
        """Fit on the rows of ``df`` whose features and ``y_col`` are finite.

        ``y_col`` may contain 0.5 for ties; values must lie in [0, 1].
        """
        if y_col not in df.columns:
            raise KeyError(f"frame lacks outcome column {y_col!r}")
        X_all = self._design(df)
        y_all = df[y_col].to_numpy(dtype=float)
        mask = np.isfinite(y_all) & np.all(np.isfinite(X_all), axis=1)
        X = X_all[mask]
        y = y_all[mask]
        if X.shape[0] == 0:
            raise ValueError("no rows with finite features and outcome to fit")
        if np.any((y < 0.0) | (y > 1.0)):
            raise ValueError(f"{y_col} must lie in [0, 1]")
        w, b, converged, n_iter = _fit_logistic(X, y, self.l2, self.use_intercept)
        self.coef_ = {c: float(v) for c, v in zip(self.design_columns, w)}
        self.intercept_ = float(b)
        self.n_fit_ = int(X.shape[0])
        self.converged_ = bool(converged)
        self.n_iter_ = int(n_iter)
        return self

    def decision_function(self, df: pd.DataFrame) -> np.ndarray:
        """Linear predictor (logit of the stacked probability); NaN where any feature is missing."""
        if not self.is_fitted:
            raise RuntimeError("LogisticStacker is not fitted")
        X = self._design(df)
        w = np.array([self.coef_[c] for c in self.design_columns], dtype=float)
        return X @ w + self.intercept_

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Stacked P(home win) clipped to ``[1e-6, 1 - 1e-6]``; NaN where any feature is missing."""
        z = self.decision_function(df)
        with np.errstate(invalid="ignore"):
            return np.clip(expit(z), P_CLIP, 1.0 - P_CLIP)

    # ------------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        return {
            "format_version": _MODEL_FORMAT_VERSION,
            "features": list(self.features),
            "l2": self.l2,
            "use_intercept": self.use_intercept,
            "coef_": dict(self.coef_),
            "intercept_": self.intercept_,
            "n_fit_": self.n_fit_,
            "converged_": self.converged_,
            "n_iter_": self.n_iter_,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticStacker":
        model = cls(list(d["features"]), l2=float(d["l2"]), use_intercept=bool(d["use_intercept"]))
        coef = {str(k): float(v) for k, v in dict(d.get("coef_", {})).items()}
        if coef and set(coef) != set(model.design_columns):
            raise ValueError(f"coef_ keys {sorted(coef)} do not match design columns {model.design_columns}")
        model.coef_ = {c: coef[c] for c in model.design_columns} if coef else {}
        model.intercept_ = float(d.get("intercept_", 0.0))
        model.n_fit_ = int(d.get("n_fit_", 0))
        model.converged_ = bool(d.get("converged_", False))
        model.n_iter_ = int(d.get("n_iter_", 0))
        return model


# ---------------------------------------------------------------- optimisation
def _fit_logistic(X: np.ndarray, y: np.ndarray, l2: float, use_intercept: bool) -> tuple[np.ndarray, float, bool, int]:
    """Minimise mean log-loss + l2/(2n) ||w||^2 (equivalently summed loss + l2/2 ||w||^2).

    L-BFGS-B with the analytic gradient, then exact Newton steps on the same objective so the
    optimum is reproduced to ~1e-10 in the coefficients regardless of the starting point.
    Returns ``(w, b, converged, n_iter)``.
    """
    n, d = X.shape
    Xa = np.hstack([X, np.ones((n, 1))]) if use_intercept else X
    k = Xa.shape[1]
    penalty = np.zeros(k)
    penalty[:d] = l2 / n  # intercept (last column) unpenalised

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = Xa @ theta
        loss = np.mean(np.logaddexp(0.0, z) - y * z) + 0.5 * np.sum(penalty * theta * theta)
        grad = Xa.T @ ((expit(z) - y) / n) + penalty * theta
        return float(loss), grad

    res = minimize(
        objective,
        np.zeros(k),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 1000, "maxfun": 5000, "ftol": 1e-15, "gtol": 1e-10},
    )
    theta = np.asarray(res.x, dtype=float)
    converged = bool(res.success)

    # Newton polish: the Hessian is tiny (k x k) and positive definite whenever l2 > 0.
    for _ in range(_NEWTON_MAX_ITER):
        z = Xa @ theta
        s = expit(z)
        grad = Xa.T @ ((s - y) / n) + penalty * theta
        H = (Xa * (s * (1.0 - s) / n)[:, None]).T @ Xa + np.diag(penalty)
        if not np.all(np.isfinite(H)) or np.linalg.cond(H) > 1e12:
            break  # ill-conditioned (e.g. l2 = 0 with collinear features): keep the L-BFGS-B solution
        step = np.linalg.solve(H, grad)
        theta = theta - step
        if np.max(np.abs(step)) < _NEWTON_TOL:
            converged = True
            break

    w = theta[:d]
    b = float(theta[d]) if use_intercept else 0.0
    return w, b, converged, int(res.nit)


# ---------------------------------------------------------------- walk-forward
def walk_forward(
    df: pd.DataFrame,
    features: list[str],
    first_test: int,
    last_test: int | None = None,
    min_train_seasons: int = 5,
    l2: float = 1.0,
) -> pd.DataFrame:
    """Season-by-season out-of-sample stacking.

    For every test season ``S`` in ``[first_test, last_test]`` a :class:`LogisticStacker` is
    fitted on played games with ``season < S`` (and finite features) and applied to every row
    of season ``S`` (played or not). Rows of a test season with a missing feature get NaN.

    Returns a copy of ``df`` restricted to the test seasons with the logit columns and a
    ``fair_prob`` column. ``attrs["coefs"]`` maps season -> ``{feature_logit: coef, ...,
    "intercept": b}``; ``attrs["n_train"]`` maps season -> training rows; ``attrs["features"]``
    and ``attrs["l2"]`` record the configuration.

    Raises ``ValueError`` if any test season has fewer than ``min_train_seasons`` distinct
    training seasons with complete data.
    """
    for col in ("season", "played", "home_win"):
        if col not in df.columns:
            raise KeyError(f"frame lacks required column {col!r}")
    features = list(features)
    data = _prepare(df, features)
    design = [logit_column(f) for f in features]
    missing = [c for c in design if c not in data.columns]
    if missing:
        raise KeyError(f"frame lacks design columns {missing}")

    seasons = data["season"].to_numpy()
    if last_test is None:
        last_test = int(seasons.max())
    first_test, last_test = int(first_test), int(last_test)
    if first_test > last_test:
        raise ValueError(f"first_test={first_test} is after last_test={last_test}")

    complete = data["played"].to_numpy(dtype=bool) & data["home_win"].notna().to_numpy()
    complete &= np.all(np.isfinite(data[design].to_numpy(dtype=float)), axis=1)

    out = data.loc[(seasons >= first_test) & (seasons <= last_test)].copy()
    out["fair_prob"] = np.nan
    coefs: dict[int, dict[str, float]] = {}
    n_train: dict[int, int] = {}
    for season in range(first_test, last_test + 1):
        train_mask = complete & (seasons < season)
        n_seasons = int(np.unique(seasons[train_mask]).size)
        if n_seasons < min_train_seasons:
            raise ValueError(
                f"test season {season}: only {n_seasons} complete training seasons "
                f"(< min_train_seasons={min_train_seasons})"
            )
        model = LogisticStacker(features, l2=l2).fit(data.loc[train_mask])
        test_idx = out.index[out["season"].to_numpy() == season]
        if len(test_idx):
            out.loc[test_idx, "fair_prob"] = model.predict_proba(out.loc[test_idx])
        coefs[season] = {**model.coef_, "intercept": model.intercept_}
        n_train[season] = model.n_fit_

    out.attrs["coefs"] = coefs
    out.attrs["n_train"] = n_train
    out.attrs["features"] = features
    out.attrs["l2"] = float(l2)
    return out


def fit_final(df: pd.DataFrame, features: list[str], l2: float = 1.0) -> LogisticStacker:
    """Fit a stacker on every played game in ``df`` (for live predictions)."""
    if "played" not in df.columns:
        raise KeyError("frame lacks required column 'played'")
    return LogisticStacker(list(features), l2=l2).fit(df.loc[df["played"].to_numpy(dtype=bool)])


# ---------------------------------------------------------------- persistence
def save_model(model: LogisticStacker, path: str | Path) -> Path:
    """Write a fitted stacker to JSON; returns the path."""
    if not model.is_fitted:
        raise ValueError("cannot save an unfitted LogisticStacker")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.to_dict(), indent=2, sort_keys=True))
    return path


def load_model(path: str | Path) -> LogisticStacker:
    """Read a stacker saved by :func:`save_model`."""
    d = json.loads(Path(path).read_text())
    version = int(d.get("format_version", 0))
    if version != _MODEL_FORMAT_VERSION:
        raise ValueError(f"unsupported model format version {version}")
    return LogisticStacker.from_dict(d)
