"""Central configuration: paths, endpoints, constants, strategy defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NFL_EDGE_DATA_DIR", PROJECT_ROOT / "data"))
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = DATA_DIR / "reports"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMMA_API = os.environ.get("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com")
CLOB_API = os.environ.get("POLYMARKET_CLOB_API", "https://clob.polymarket.com")

FIRST_SEASON = 1999
FIRST_MONEYLINE_SEASON = 2007  # nflverse moneylines are complete from 2007 onward

# NFL final-margin standard deviation around the closing spread (empirical, ~13.5).
NFL_MARGIN_SIGMA = 13.45
ELO_MEAN = 1500.0
ELO_POINTS_PER_SPREAD_POINT = 25.0  # 25 Elo points ~ 1 point of spread


@dataclass(frozen=True)
class StrategyConfig:
    """Bankroll and edge thresholds. Conservative by default; loosen only with evidence."""

    kelly_fraction: float = 0.25        # quarter Kelly
    max_stake_fraction: float = 0.03    # max bankroll share on any single position
    max_weekly_exposure: float = 0.15   # max bankroll share open across a week
    max_game_exposure: float = 0.05     # max across correlated positions on one game
    min_edge: float = 0.03              # fair_prob - effective_price must exceed this
    min_liquidity_usd: float = 500.0    # skip books thinner than this at the touch
    fee_rate: float = 0.0               # Polymarket taker fee; set from markets.polymarket.fee_for_market
    slippage_buffer: float = 0.01       # price impact assumed beyond the best ask
    min_prob: float = 0.05              # never buy below 5c or above 95c (longshot/favorite traps)
    max_prob: float = 0.95


DEFAULT_STRATEGY = StrategyConfig()


def season_for_date(d: date) -> int:
    """NFL season label for a calendar date. Jan/Feb belong to the prior season's playoffs."""
    return d.year if d.month >= 3 else d.year - 1


def ensure_dirs() -> None:
    for p in (CACHE_DIR, REPORTS_DIR, SNAPSHOT_DIR):
        p.mkdir(parents=True, exist_ok=True)
