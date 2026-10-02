"""Estimate value-gate thresholds on the VALIDATION window, then freeze them.

Selection uses only validation games; the frozen config is evaluated once on
the test window and that evaluation is logged. Each game contributes at most
its first qualifying snapshot per side, so one game cannot dominate.

Inputs per row: ``p_low`` (cautious model probability), ``ask`` (executable
price), ``y`` (outcome), ``game_id``, ``game_start``. On synthetic data the
result is a plumbing check only (status stays SYNTHETIC_ONLY).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from sports_edge.pricing.fees import KalshiQuadraticFee

CANDIDATE_MARGINS_CENTS = (0, 1, 2, 3, 5, 8, 12)


@dataclass(frozen=True)
class FrozenThresholds:
    min_conservative_ev_cents_per_contract: float
    estimated_on: str
    validation_games: int
    validation_mean_pnl_cents: float
    data_status: str
    config_sha256: str = ""


def _pnl(df: pd.DataFrame, margin_cents: float) -> pd.DataFrame:
    fee = KalshiQuadraticFee()
    edge = (df["p_low"] - df["ask"]) * 100
    sel = df[edge >= margin_cents].sort_values("frac_remaining", ascending=False)
    sel = sel.drop_duplicates(["game_id", "is_home"])
    fees = sel["ask"].map(lambda a: float(fee.entry_fee(100, Decimal(str(round(a, 2))))) / 100)
    return sel.assign(pnl=sel["y"] - sel["ask"] - fees)


def estimate(validation: pd.DataFrame, data_status: str, min_games: int = 20) -> FrozenThresholds:
    best = None
    for m in CANDIDATE_MARGINS_CENTS:
        sel = _pnl(validation, m)
        if sel["game_id"].nunique() < min_games:
            continue
        score = float(sel["pnl"].mean() * 100)
        if best is None or score > best[1]:
            best = (m, score, int(sel["game_id"].nunique()))
    if best is None:
        raise ValueError("not enough validation games for any candidate margin")
    rng = f"{validation['game_start'].min()}..{validation['game_start'].max()}"
    ft = FrozenThresholds(best[0], rng, best[2], best[1], data_status)
    sha = hashlib.sha256(json.dumps(asdict(ft), sort_keys=True, default=str).encode()).hexdigest()
    return FrozenThresholds(**{**asdict(ft), "config_sha256": sha})


def evaluate_frozen(ft: FrozenThresholds, test: pd.DataFrame) -> dict:
    sel = _pnl(test, ft.min_conservative_ev_cents_per_contract)
    return {"test_games_selected": int(sel["game_id"].nunique()),
            "mean_pnl_cents_per_contract": float(sel["pnl"].mean() * 100) if len(sel) else None,
            "win_rate": float(sel["y"].mean()) if len(sel) else None}


def save(ft: FrozenThresholds, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"frozen_thresholds_{ft.config_sha256[:12]}.json"
    p.write_text(json.dumps(asdict(ft), indent=2, default=str))
    return p
