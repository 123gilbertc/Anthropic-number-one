"""Backtests: model probabilities vs closing moneylines, and vs recorded Polymarket quotes.

See CONTRACT.md (section ``nfl_edge/backtest/engine.py``) for the interface.
"""
from .engine import (  # noqa: F401
    BET_COLUMNS,
    SETTLEMENT_LAG,
    WEEKS_PER_YEAR,
    BacktestConfig,
    BacktestResult,
    backtest_snapshots,
    backtest_vs_closing,
    bootstrap_roi,
    max_drawdown,
    nfl_season_week,
    report_markdown,
)

__all__ = [
    "BET_COLUMNS",
    "SETTLEMENT_LAG",
    "WEEKS_PER_YEAR",
    "BacktestConfig",
    "BacktestResult",
    "backtest_snapshots",
    "backtest_vs_closing",
    "bootstrap_roi",
    "max_drawdown",
    "nfl_season_week",
    "report_markdown",
]
