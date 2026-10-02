"""Forecast evaluation metrics, with plain-English meanings.

* Brier score: average squared gap between forecast and outcome (0/1).
  Lower is better. Always forecasting 50% scores 0.25.
* Log loss: penalizes confident wrong forecasts heavily. Lower is better.
  Always forecasting 50% scores 0.693.
* Calibration: among forecasts near 70%, did ~70% of them win? Shown as bins.
* Winner accuracy: how often the side we gave >50% won. Inflated by easy
  late-game states, so always compare within the same game phase.
* Clustered bootstrap: uncertainty is computed by resampling whole games
  (or days), because thousands of snapshots from one game are not
  thousands of independent observations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p > 0.5) == (y == 1)))


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    df = pd.DataFrame({"bin": idx, "p": p, "y": y})
    out = df.groupby("bin").agg(n=("y", "size"), mean_forecast=("p", "mean"),
                                observed_rate=("y", "mean")).reset_index()
    return out


@dataclass(frozen=True)
class MetricWithCI:
    value: float
    low: float
    high: float
    n_rows: int
    n_clusters: int


def clustered_bootstrap(df: pd.DataFrame, metric, cluster_col: str = "game_id",
                        p_col: str = "p", y_col: str = "y", n_boot: int = 300,
                        seed: int = 0) -> MetricWithCI:
    rng = np.random.default_rng(seed)
    groups = df.groupby(cluster_col).indices
    keys = list(groups)
    p, y = df[p_col].to_numpy(float), df[y_col].to_numpy(float)
    stats = []
    for _ in range(n_boot):
        pick = rng.choice(len(keys), size=len(keys), replace=True)
        idx = np.concatenate([groups[keys[i]] for i in pick])
        stats.append(metric(p[idx], y[idx]))
    lo, hi = np.quantile(stats, [0.025, 0.975])
    return MetricWithCI(metric(p, y), float(lo), float(hi), len(df), len(keys))


def phase_of(frac_remaining: float) -> str:
    if frac_remaining > 2 / 3:
        return "P1"
    if frac_remaining > 1 / 3:
        return "P2"
    if frac_remaining > 0.1:
        return "P3_early"
    return "P3_late"


def by_phase(df: pd.DataFrame, p_col: str = "p", y_col: str = "y") -> pd.DataFrame:
    d = df.assign(phase=df["frac_remaining"].map(phase_of))
    rows = []
    for ph, g in d.groupby("phase"):
        p, y = g[p_col].to_numpy(float), g[y_col].to_numpy(float)
        rows.append({"phase": ph, "n": len(g), "games": g["game_id"].nunique(),
                     "brier": brier(p, y), "log_loss": log_loss(p, y),
                     "accuracy": accuracy(p, y)})
    return pd.DataFrame(rows)


def conditional_win_rate(outcomes: list[int]) -> dict:
    """Win rate within a subset (e.g. dipped picks). Empty subset -> UNKNOWN, never a borrowed rate."""
    if not outcomes:
        return {"status": "UNKNOWN", "n": 0, "win_rate": None}
    n = len(outcomes)
    w = sum(outcomes) / n
    # Wilson 95% interval
    z = 1.96
    denom = 1 + z * z / n
    centre = (w + z * z / (2 * n)) / denom
    half = z * np.sqrt(w * (1 - w) / n + z * z / (4 * n * n)) / denom
    return {"status": "ESTIMATED", "n": n, "win_rate": w,
            "ci95": (float(centre - half), float(centre + half))}
