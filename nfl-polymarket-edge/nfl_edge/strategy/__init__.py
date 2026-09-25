"""Strategy layer: Kelly sizing and the Polymarket edge / arbitrage / consistency scanner.

See CONTRACT.md for the interface each module implements.
"""
from .kelly import (  # noqa: F401
    breakeven_prob,
    expected_value,
    exposure_key,
    fractional_kelly,
    growth_rate,
    kelly_fraction,
    kelly_share,
    net_odds,
    size_positions,
)
from .edge import (  # noqa: F401
    GAME_KINDS,
    OPPORTUNITY_COLUMNS,
    Opportunity,
    consistency_checks,
    effective_price,
    find_arbitrage,
    find_edges,
    futures_category,
    market_category,
    market_mid,
    opportunities_table,
)

__all__ = [
    "GAME_KINDS",
    "Opportunity",
    "OPPORTUNITY_COLUMNS",
    "breakeven_prob",
    "consistency_checks",
    "effective_price",
    "expected_value",
    "exposure_key",
    "find_arbitrage",
    "find_edges",
    "fractional_kelly",
    "futures_category",
    "growth_rate",
    "kelly_fraction",
    "kelly_share",
    "market_category",
    "market_mid",
    "net_odds",
    "opportunities_table",
    "size_positions",
]
