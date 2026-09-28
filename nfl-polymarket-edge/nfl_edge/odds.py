"""Odds conversion, vig removal, and probability <-> spread / Elo mappings.

Conventions
-----------
* Probabilities are floats in (0, 1).
* American odds: +150 means win 150 on 100; -200 means risk 200 to win 100.
* Decimal odds: total return per 1 unit staked (2.50 = +150).
* Polymarket: a share price in (0, 1) *is* the implied probability; decimal odds = 1 / price.
* NFL spreads follow nflverse: ``spread_line`` is the expected (home - away) margin,
  so +3.0 means the home team is favored by 3.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from .config import NFL_MARGIN_SIGMA

_EPS = 1e-9


# ---------------------------------------------------------------- conversions
def american_to_decimal(odds: float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    return 1.0 + odds / 100.0 if odds > 0 else 1.0 + 100.0 / abs(odds)


def decimal_to_american(dec: float) -> float:
    dec = float(dec)
    if dec <= 1.0:
        raise ValueError("Decimal odds must exceed 1.0")
    return (dec - 1.0) * 100.0 if dec >= 2.0 else -100.0 / (dec - 1.0)


def american_to_prob(odds: float) -> float:
    """Implied probability *with* vig (bookmaker's price)."""
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    return 100.0 / (odds + 100.0) if odds > 0 else abs(odds) / (abs(odds) + 100.0)


def prob_to_american(p: float) -> float:
    p = _check_prob(p)
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def decimal_to_prob(dec: float) -> float:
    if dec <= 1.0:
        raise ValueError("Decimal odds must exceed 1.0")
    return 1.0 / float(dec)


def prob_to_decimal(p: float) -> float:
    return 1.0 / _check_prob(p)


def price_to_decimal(price: float) -> float:
    """Polymarket share price (0-1) -> decimal odds."""
    return 1.0 / _check_prob(price)


def _check_prob(p: float) -> float:
    p = float(p)
    if not (0.0 < p < 1.0):
        raise ValueError(f"probability must be in (0,1), got {p}")
    return p


# ----------------------------------------------------------------- vig removal
def overround(probs: Iterable[float]) -> float:
    """Sum of implied probabilities; > 1 means the book holds vig."""
    return float(sum(probs))


def devig(probs: Sequence[float], method: str = "multiplicative") -> list[float]:
    """Remove the bookmaker margin from a full set of mutually exclusive outcome prices.

    method:
      * ``multiplicative`` - scale to sum 1 (simple, slightly biased on longshots)
      * ``additive``       - subtract an equal share of the overround
      * ``power``          - find k with sum(p_i^k) = 1 (handles favorite-longshot bias)
      * ``shin``           - Shin (1993) insider-trading model, standard for sharp-line devig
    """
    p = np.asarray(probs, dtype=float)
    if p.ndim != 1 or len(p) < 2:
        raise ValueError("need at least two outcome probabilities")
    if np.any(p <= 0) or np.any(p >= 1):
        raise ValueError("implied probabilities must be in (0,1)")
    if method not in ("multiplicative", "additive", "power", "shin"):
        raise ValueError(f"unknown devig method {method!r}")
    total = p.sum()
    if abs(total - 1.0) < 1e-12:
        return p.tolist()

    if method == "multiplicative":
        out = p / total
    elif method == "additive":
        out = p - (total - 1.0) / len(p)
        if np.any(out <= 0):  # fall back for extreme longshots
            out = p / total
    elif method == "power":
        if total < 1.0:  # negative margin: k < 1
            k = brentq(lambda k: np.sum(p ** k) - 1.0, 1e-6, 1.0)
        else:
            k = brentq(lambda k: np.sum(p ** k) - 1.0, 1.0, 50.0)
        out = p ** k
    elif method == "shin":
        out = _shin(p)
    else:
        raise ValueError(f"unknown devig method {method!r}")
    out = np.clip(out, _EPS, 1 - _EPS)
    return (out / out.sum()).tolist()


def _shin(p: np.ndarray) -> np.ndarray:
    """Shin's method: solve for insider share z so fair probabilities sum to one."""
    b = p.sum()
    if b <= 1.0:
        return p / b

    def fair(z: float) -> np.ndarray:
        return (np.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / b) - z) / (2.0 * (1.0 - z))

    f = lambda z: fair(z).sum() - 1.0  # noqa: E731
    lo, hi = 0.0, 0.5
    if f(lo) <= 0:
        return p / b
    while f(hi) > 0 and hi < 0.999:
        hi = (hi + 1.0) / 2.0
    z = brentq(f, lo, hi)
    return fair(z)


def devig_two_way(odds_a: float, odds_b: float, method: str = "shin") -> tuple[float, float]:
    """Fair probabilities for a two-outcome market quoted in American odds."""
    pa, pb = devig([american_to_prob(odds_a), american_to_prob(odds_b)], method=method)
    return pa, pb


# ------------------------------------------------------- spread <-> probability
def spread_to_prob(spread: float, sigma: float = NFL_MARGIN_SIGMA) -> float:
    """P(home wins) given an expected (home - away) margin under a normal margin model."""
    return float(norm.cdf(float(spread) / sigma))


def prob_to_spread(p: float, sigma: float = NFL_MARGIN_SIGMA) -> float:
    return float(sigma * norm.ppf(_check_prob(p)))


# ---------------------------------------------------------- elo <-> probability
def elo_diff_to_prob(diff: float) -> float:
    """Logistic Elo expectation; ``diff`` = rating(A) - rating(B) after adjustments."""
    return 1.0 / (1.0 + 10.0 ** (-float(diff) / 400.0))


def prob_to_elo_diff(p: float) -> float:
    p = _check_prob(p)
    return -400.0 * math.log10(1.0 / p - 1.0)


# --------------------------------------------------------------- vectorized
def spread_to_prob_array(spreads, sigma: float = NFL_MARGIN_SIGMA) -> np.ndarray:
    return norm.cdf(np.asarray(spreads, dtype=float) / sigma)


def american_to_prob_array(odds) -> np.ndarray:
    o = np.asarray(odds, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(o > 0, 100.0 / (o + 100.0), np.abs(o) / (np.abs(o) + 100.0))
    out = np.where(np.isnan(o) | (o == 0), np.nan, out)
    return out


def devig_two_way_array(prob_a, prob_b) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized multiplicative devig for two-way markets (fast path for backtests)."""
    a = np.asarray(prob_a, dtype=float)
    b = np.asarray(prob_b, dtype=float)
    tot = a + b
    return a / tot, b / tot
