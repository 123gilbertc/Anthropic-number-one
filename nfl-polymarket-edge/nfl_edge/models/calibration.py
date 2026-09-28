"""Scoring rules and calibration diagnostics for win-probability models.

All functions take an outcome vector ``y`` (1 = home win, 0 = away win, 0.5 = tie) and one
or more probability vectors ``p`` = P(home win). Ties are scored as half an event under every
rule: the log-loss and Brier score use the fractional label directly and accuracy awards
half a point. Inputs must be finite; NaNs are an error rather than silently dropped, except
in :func:`summarize`, which restricts every model to the common support so the comparison
is paired.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

P_CLIP = 1e-6  # probabilities are clipped to [P_CLIP, 1 - P_CLIP] before taking logs


# ------------------------------------------------------------------ helpers
def _as_arrays(y, p) -> tuple[np.ndarray, np.ndarray]:
    """Validate and coerce ``y`` and ``p`` to equal-length 1-D float arrays."""
    y = np.asarray(y, dtype=float).ravel()
    p = np.asarray(p, dtype=float).ravel()
    if y.shape != p.shape:
        raise ValueError(f"y and p must have the same length, got {y.shape} and {p.shape}")
    if y.size == 0:
        raise ValueError("y and p must not be empty")
    if not np.all(np.isfinite(y)) or not np.all(np.isfinite(p)):
        raise ValueError("y and p must be finite (filter to played games with predictions first)")
    if np.any((y < 0.0) | (y > 1.0)):
        raise ValueError("y must lie in [0, 1] (1 = home win, 0 = away win, 0.5 = tie)")
    if np.any((p < 0.0) | (p > 1.0)):
        raise ValueError("p must lie in [0, 1]")
    return y, p


def _per_game_log_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    pc = np.clip(p, P_CLIP, 1.0 - P_CLIP)
    return -(y * np.log(pc) + (1.0 - y) * np.log1p(-pc))


def _bin_index(p: np.ndarray, bins: int) -> np.ndarray:
    """Equal-width bin index in ``[0, bins)`` for probabilities in ``[0, 1]`` (1.0 lands in the last bin)."""
    if bins < 1:
        raise ValueError("bins must be >= 1")
    return np.minimum(np.floor(p * bins).astype(int), bins - 1)


# ------------------------------------------------------------------ scoring rules
def log_loss(y, p) -> float:
    """Mean binary log-loss with ``p`` clipped to ``[1e-6, 1 - 1e-6]``; ties (y = 0.5) count half."""
    y, p = _as_arrays(y, p)
    return float(np.mean(_per_game_log_loss(y, p)))


def brier(y, p) -> float:
    """Mean squared error between probability and outcome (0.25 = coin flip, lower is better)."""
    y, p = _as_arrays(y, p)
    return float(np.mean((p - y) ** 2))


def accuracy(y, p) -> float:
    """Share of games where the favoured side (p >= 0.5 -> home) won; ties count half."""
    y, p = _as_arrays(y, p)
    pred = (p >= 0.5).astype(float)
    hit = np.where(y == 0.5, 0.5, (pred == y).astype(float))
    return float(np.mean(hit))


# ------------------------------------------------------------------ calibration
def calibration_table(y, p, bins: int = 10) -> pd.DataFrame:
    """Reliability table over ``bins`` equal-width probability buckets on [0, 1].

    Columns: ``bin`` (0-based index), ``bin_lo``, ``bin_hi``, ``p_mean``, ``y_mean``, ``n``,
    ``gap`` (= ``y_mean - p_mean``). Every bucket is listed; empty ones have ``n = 0`` and NaN means.
    """
    y, p = _as_arrays(y, p)
    idx = _bin_index(p, bins)
    n = np.bincount(idx, minlength=bins).astype(int)
    sum_p = np.bincount(idx, weights=p, minlength=bins)
    sum_y = np.bincount(idx, weights=y, minlength=bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_mean = np.where(n > 0, sum_p / np.maximum(n, 1), np.nan)
        y_mean = np.where(n > 0, sum_y / np.maximum(n, 1), np.nan)
    edges = np.linspace(0.0, 1.0, bins + 1)
    return pd.DataFrame(
        {
            "bin": np.arange(bins),
            "bin_lo": edges[:-1],
            "bin_hi": edges[1:],
            "p_mean": p_mean,
            "y_mean": y_mean,
            "n": n,
            "gap": y_mean - p_mean,
        }
    )


def ece(y, p, bins: int = 10) -> float:
    """Expected calibration error: n-weighted mean of |y_mean - p_mean| over equal-width bins."""
    y, p = _as_arrays(y, p)
    idx = _bin_index(p, bins)
    sum_p = np.bincount(idx, weights=p, minlength=bins)
    sum_y = np.bincount(idx, weights=y, minlength=bins)
    return float(np.sum(np.abs(sum_y - sum_p)) / y.size)


# ------------------------------------------------------------------ model comparison
def paired_bootstrap(y, p_a, p_b, n: int = 2000, seed: int = 0) -> dict:
    """Paired bootstrap of the log-loss difference between models ``a`` and ``b``.

    Games are resampled with replacement ``n`` times; on each resample the mean per-game
    log-loss difference ``a - b`` is recorded. Returns a dict with

    * ``diff``: observed mean log-loss(a) - log-loss(b) (negative favours ``a``);
    * ``ci_lo`` / ``ci_hi``: 2.5th / 97.5th percentiles of the bootstrap distribution;
    * ``p_a_better``: share of resamples where ``a`` has the lower log-loss;
    * ``logloss_a``, ``logloss_b``, ``n`` (games) and ``n_boot``.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    y, pa = _as_arrays(y, p_a)
    y2, pb = _as_arrays(y, p_b)
    if y2.shape != pa.shape:
        raise ValueError("p_a and p_b must have the same length")
    d = _per_game_log_loss(y, pa) - _per_game_log_loss(y, pb)
    size = d.size
    rng = np.random.default_rng(seed)
    means = np.empty(n)
    block = max(1, min(n, (2 << 20) // max(size, 1)))  # <= 2M int64 indices (16 MB) per block
    for start in range(0, n, block):
        stop = min(n, start + block)
        idx = rng.integers(0, size, size=(stop - start, size))
        means[start:stop] = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {
        "diff": float(d.mean()),
        "ci_lo": float(lo),
        "ci_hi": float(hi),
        "p_a_better": float(np.mean(means < 0.0)),
        "logloss_a": float(np.mean(_per_game_log_loss(y, pa))),
        "logloss_b": float(np.mean(_per_game_log_loss(y, pb))),
        "n": int(size),
        "n_boot": int(n),
    }


def summarize(y, probs: dict[str, np.ndarray], bins: int = 10) -> pd.DataFrame:
    """Score several models on the same games.

    Rows are restricted to the common support (finite ``y`` and finite prediction for every
    model) so the comparison is paired. Columns: ``model``, ``logloss``, ``brier``, ``acc``,
    ``ece``, ``n``.
    """
    if not probs:
        raise ValueError("probs must contain at least one model")
    y = np.asarray(y, dtype=float).ravel()
    arrays: dict[str, np.ndarray] = {}
    mask = np.isfinite(y)
    for name, p in probs.items():
        arr = np.asarray(p, dtype=float).ravel()
        if arr.shape != y.shape:
            raise ValueError(f"probs[{name!r}] has length {arr.size}, expected {y.size}")
        arrays[name] = arr
        mask &= np.isfinite(arr)
    if not mask.any():
        raise ValueError("no games with a finite outcome and a prediction from every model")
    ym = y[mask]
    rows = []
    for name, arr in arrays.items():
        pm = arr[mask]
        rows.append(
            {
                "model": name,
                "logloss": log_loss(ym, pm),
                "brier": brier(ym, pm),
                "acc": accuracy(ym, pm),
                "ece": ece(ym, pm, bins=bins),
                "n": int(ym.size),
            }
        )
    return pd.DataFrame(rows, columns=["model", "logloss", "brier", "acc", "ece", "n"])
