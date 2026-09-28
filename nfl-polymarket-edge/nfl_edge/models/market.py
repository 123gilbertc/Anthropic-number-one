"""Market baseline: devigged moneyline probabilities and the closing-spread margin model.

The closing line is the strongest single predictor of an NFL game; every other model in
this package is measured against it. This module provides

* :func:`market_probs` - P(home win) from the closing moneylines, devigged with any of the
  methods in :mod:`nfl_edge.odds` (vectorised, cross-checked against ``odds.devig``), falling
  back to the normal margin model on the closing spread when moneylines are missing;
* :func:`fit_spread_sigma` / :func:`fit_spread_model` - maximum-likelihood fit of
  ``margin ~ Normal(spread_line + mu, sigma)`` on played games;
* :func:`spread_calibration` - empirical home win rate by spread bucket;
* :func:`line_to_prob_table` - the spread -> probability lookup for a given sigma.

Conventions follow ``nfl_edge.odds``: ``spread_line`` is the expected (home - away) margin,
so +3.0 means the home side is favoured by three.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from ..config import NFL_MARGIN_SIGMA
from ..odds import american_to_prob_array, devig, spread_to_prob_array

_DEVIG_METHODS: tuple[str, ...] = ("multiplicative", "additive", "power", "shin")
_EPS = 1e-9  # same floor as nfl_edge.odds.devig
_NO_VIG_TOL = 1e-12  # same pass-through tolerance as nfl_edge.odds.devig

#: Default spread buckets for :func:`spread_calibration` (edges in points, home - away).
DEFAULT_SPREAD_BINS: tuple[float, ...] = (
    -math.inf, -10.0, -7.0, -4.0, -2.0, 0.0, 2.0, 4.0, 7.0, 10.0, math.inf,
)


# ------------------------------------------------------------------ devig (vectorised)
def _bisect(f, lo: np.ndarray, hi: np.ndarray, tol: float = 1e-13, max_iter: int = 200) -> np.ndarray:
    """Vectorised bisection for a root of a decreasing function ``f`` on each ``[lo, hi]``.

    ``f`` maps an array of candidate roots to an array of function values; ``f(lo) > 0`` and
    ``f(hi) <= 0`` must hold elementwise on entry.
    """
    lo = lo.astype(float).copy()
    hi = hi.astype(float).copy()
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        pos = f(mid) > 0.0
        lo = np.where(pos, mid, lo)
        hi = np.where(pos, hi, mid)
        if np.all(hi - lo <= tol):
            break
    return 0.5 * (lo + hi)


def _shin_fair(z: np.ndarray, p: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Shin fair probabilities for insider share ``z`` (per row); ``p`` is (n, k), ``b`` its row sums."""
    zc = z[:, None]
    return (np.sqrt(zc * zc + 4.0 * (1.0 - zc) * (p * p) / b[:, None]) - zc) / (2.0 * (1.0 - zc))


def _shin_rows(p: np.ndarray) -> np.ndarray:
    """Row-wise Shin devig of an (n, k) array of vig-inclusive probabilities.

    Mirrors ``nfl_edge.odds._shin`` exactly: rows whose overround is <= 1 are scaled to sum
    one; otherwise the insider share ``z`` solving ``sum(fair(z)) = 1`` is found by bisection
    on ``[0, hi]``, where ``hi`` starts at 0.5 and is pushed toward 1 until the bracket holds.
    """
    b = p.sum(axis=1)
    out = p / b[:, None]
    solve = b > 1.0
    if not solve.any():
        return out
    ps = p[solve]
    bs = b[solve]

    def f(z: np.ndarray) -> np.ndarray:
        return _shin_fair(z, ps, bs).sum(axis=1) - 1.0

    lo = np.zeros(len(ps))
    hi = np.full(len(ps), 0.5)
    for _ in range(12):  # (hi+1)/2 twelve times from 0.5 exceeds 0.999
        need = (f(hi) > 0.0) & (hi < 0.999)
        if not need.any():
            break
        hi = np.where(need, (hi + 1.0) / 2.0, hi)
    unbracketed = f(hi) > 0.0
    if unbracketed.any():
        bad = np.flatnonzero(solve)[unbracketed]
        raise ValueError(f"Shin devig: no root bracket for rows {bad[:10].tolist()} (overround {bs[unbracketed][:10]})")
    z = _bisect(f, lo, hi)
    out[solve] = _shin_fair(z, ps, bs)
    return out


def _power_rows(p: np.ndarray) -> np.ndarray:
    """Row-wise power devig: find ``k`` with ``sum(p_i ** k) = 1`` (bisection, same brackets as ``odds.devig``)."""
    total = p.sum(axis=1)
    lo = np.where(total < 1.0, 1e-6, 1.0)
    hi = np.where(total < 1.0, 1.0, 50.0)

    def g(k: np.ndarray) -> np.ndarray:
        return np.power(p, k[:, None]).sum(axis=1) - 1.0

    k = _bisect(g, lo, hi)
    return np.power(p, k[:, None])


def devig_rows(probs: np.ndarray, method: str = "shin") -> np.ndarray:
    """Vectorised equivalent of ``nfl_edge.odds.devig`` applied to each row of an (n, k) array.

    Returns an (n, k) array of fair probabilities. Row semantics match ``odds.devig`` to
    floating-point precision (bisection tolerance 1e-13 on the internal parameter):
    rows already summing to one pass through untouched, every other row is clipped to
    ``[1e-9, 1 - 1e-9]`` and renormalised.
    """
    if method not in _DEVIG_METHODS:
        raise ValueError(f"unknown devig method {method!r}")
    p = np.asarray(probs, dtype=float)
    if p.ndim != 2 or p.shape[1] < 2:
        raise ValueError("probs must be an (n, k>=2) array")
    if not np.all(np.isfinite(p)):
        raise ValueError("implied probabilities must be finite")
    if np.any(p <= 0.0) or np.any(p >= 1.0):
        raise ValueError("implied probabilities must be in (0,1)")
    if p.shape[0] == 0:
        return p.copy()

    total = p.sum(axis=1)
    if method == "multiplicative":
        out = p / total[:, None]
    elif method == "additive":
        add = p - ((total - 1.0) / p.shape[1])[:, None]
        mult = p / total[:, None]
        out = np.where((add.min(axis=1) <= 0.0)[:, None], mult, add)
    elif method == "power":
        out = _power_rows(p)
    else:
        out = _shin_rows(p)

    out = np.clip(out, _EPS, 1.0 - _EPS)
    out = out / out.sum(axis=1, keepdims=True)
    passthrough = np.abs(total - 1.0) < _NO_VIG_TOL
    if passthrough.any():
        out[passthrough] = p[passthrough]
    return out


def devig_check(probs: Sequence[Sequence[float]], method: str = "shin") -> float:
    """Max absolute difference between :func:`devig_rows` and ``odds.devig`` on the given rows (for tests/audits)."""
    p = np.asarray(probs, dtype=float)
    vec = devig_rows(p, method)
    ref = np.array([devig(row, method) for row in p])
    return float(np.max(np.abs(vec - ref)))


# ------------------------------------------------------------------ market probabilities
def market_probs(games: pd.DataFrame, devig: str = "shin", sigma: float = NFL_MARGIN_SIGMA) -> pd.Series:
    """P(home win) implied by the closing market for every row of ``games``.

    When both ``home_moneyline`` and ``away_moneyline`` are present the two-way market is
    devigged with ``devig`` (one of multiplicative / additive / power / shin, see
    :func:`nfl_edge.odds.devig`). Otherwise the normal margin model on ``spread_line`` with
    standard deviation ``sigma`` is used; rows with neither price get NaN.

    Returns a float Series aligned to ``games.index`` named ``market_prob``.
    """
    if devig not in _DEVIG_METHODS:
        raise ValueError(f"unknown devig method {devig!r}")
    for col in ("home_moneyline", "away_moneyline", "spread_line"):
        if col not in games.columns:
            raise KeyError(f"games is missing required column {col!r}")

    raw_home = american_to_prob_array(games["home_moneyline"].to_numpy(dtype=float))
    raw_away = american_to_prob_array(games["away_moneyline"].to_numpy(dtype=float))
    has_ml = np.isfinite(raw_home) & np.isfinite(raw_away)

    out = spread_to_prob_array(games["spread_line"].to_numpy(dtype=float), sigma=sigma)
    if has_ml.any():
        fair = devig_rows(np.column_stack([raw_home[has_ml], raw_away[has_ml]]), devig)
        out = out.astype(float, copy=True)
        out[has_ml] = fair[:, 0]
    return pd.Series(out, index=games.index, name="market_prob", dtype=float)


# ------------------------------------------------------------------ spread model
def _spread_residuals(games: pd.DataFrame) -> np.ndarray:
    """``margin - spread_line`` for played games with a closing spread."""
    for col in ("played", "margin", "spread_line"):
        if col not in games.columns:
            raise KeyError(f"games is missing required column {col!r}")
    mask = games["played"].to_numpy(dtype=bool) & games["spread_line"].notna().to_numpy() & games["margin"].notna().to_numpy()
    if not mask.any():
        raise ValueError("no played games with a spread_line to fit")
    g = games.loc[mask]
    return (g["margin"].to_numpy(dtype=float) - g["spread_line"].to_numpy(dtype=float))


def fit_spread_sigma(games: pd.DataFrame) -> float:
    """MLE of sigma in ``margin ~ Normal(spread_line, sigma)`` on played games.

    The mean is pinned to the closing spread (no offset), so this is the root mean square of
    ``margin - spread_line``. Empirically ~13.2-13.5 points for 1999-present.
    """
    r = _spread_residuals(games)
    return float(np.sqrt(np.mean(r * r)))


def fit_spread_model(games: pd.DataFrame) -> tuple[float, float]:
    """MLE of ``(mu_offset, sigma)`` in ``margin ~ Normal(spread_line + mu_offset, sigma)``.

    A nonzero ``mu_offset`` means the closing spread is biased (positive: home sides beat
    the number on average). Returns ``(mu_offset, sigma)``.
    """
    r = _spread_residuals(games)
    mu = float(np.mean(r))
    sigma = float(np.sqrt(np.mean((r - mu) ** 2)))
    return mu, sigma


def spread_calibration(
    games: pd.DataFrame,
    bins: Sequence[float] = DEFAULT_SPREAD_BINS,
    sigma: float = NFL_MARGIN_SIGMA,
) -> pd.DataFrame:
    """Empirical home win rate by closing-spread bucket, next to the normal model's prediction.

    Buckets are ``pd.cut(spread_line, bins, right=False)`` so each bucket is ``[lo, hi)`` and a
    pick'em (0.0) falls in the first bucket with non-negative spreads. Ties count as half a win.
    Columns: ``bin`` (label), ``spread_lo``, ``spread_hi``, ``spread_mean``, ``n``,
    ``home_win_rate``, ``model_prob`` (normal model at ``spread_mean`` with ``sigma``), ``gap``
    (= ``home_win_rate - model_prob``). Empty buckets are dropped.
    """
    for col in ("played", "spread_line", "home_win"):
        if col not in games.columns:
            raise KeyError(f"games is missing required column {col!r}")
    edges = [float(b) for b in bins]
    if len(edges) < 2 or any(b >= a for a, b in zip(edges[1:], edges[:-1])):
        raise ValueError("bins must be a strictly increasing sequence of at least two edges")
    g = games.loc[games["played"] & games["spread_line"].notna() & games["home_win"].notna(), ["spread_line", "home_win"]]
    if g.empty:
        raise ValueError("no played games with a spread_line and result")
    cats = pd.cut(g["spread_line"], edges, right=False)
    grouped = g.groupby(cats, observed=True)
    tbl = grouped.agg(spread_mean=("spread_line", "mean"), n=("home_win", "size"), home_win_rate=("home_win", "mean"))
    tbl = tbl.reset_index().rename(columns={"spread_line": "bin"})
    tbl["spread_lo"] = [iv.left for iv in tbl["bin"]]
    tbl["spread_hi"] = [iv.right for iv in tbl["bin"]]
    tbl["bin"] = tbl["bin"].astype(str)
    tbl["model_prob"] = spread_to_prob_array(tbl["spread_mean"].to_numpy(dtype=float), sigma=sigma)
    tbl["gap"] = tbl["home_win_rate"] - tbl["model_prob"]
    tbl["n"] = tbl["n"].astype(int)
    cols = ["bin", "spread_lo", "spread_hi", "spread_mean", "n", "home_win_rate", "model_prob", "gap"]
    return tbl[cols].sort_values("spread_lo").reset_index(drop=True)


def line_to_prob_table(sigma: float = NFL_MARGIN_SIGMA) -> pd.DataFrame:
    """Spread -> probability lookup for spreads -14..+14 in half-point steps.

    Columns: ``spread_line``, ``p_home``, ``p_away``, ``home_american``, ``away_american``
    (fair American odds, no vig).
    """
    if not (sigma > 0):
        raise ValueError("sigma must be positive")
    lines = np.round(np.arange(-28, 29) * 0.5, 1)
    p_home = spread_to_prob_array(lines, sigma=sigma)
    p_away = 1.0 - p_home

    def american(p: np.ndarray) -> np.ndarray:
        return np.where(p >= 0.5, -100.0 * p / (1.0 - p), 100.0 * (1.0 - p) / p)

    return pd.DataFrame(
        {
            "spread_line": lines,
            "p_home": p_home,
            "p_away": p_away,
            "home_american": american(p_home),
            "away_american": american(p_away),
        }
    )
