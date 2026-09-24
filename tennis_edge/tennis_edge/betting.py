"""Pricing, edge and stake sizing for binary (Polymarket-style) contracts.

A Polymarket share pays $1 if the outcome happens. Buying at price `c`:
    expected profit per share = p - c - fees
    Kelly fraction of bankroll  = (p - c) / (1 - c)
Full Kelly is far too aggressive when your probability is itself uncertain,
so we use fractional Kelly and a hard per-bet cap.
"""
from __future__ import annotations

from dataclasses import dataclass


def implied_from_decimal(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Remove the bookmaker margin (proportional method)."""
    ia, ib = 1 / odds_a, 1 / odds_b
    s = ia + ib
    return ia / s, ib / s


def shrink(p_model: float, p_market: float, w_model: float) -> float:
    """Blend our number with the market's. Markets are sharp; w_model ~0.2-0.4 is realistic."""
    return w_model * p_model + (1 - w_model) * p_market


def kelly_binary(p: float, price: float, fee: float = 0.0) -> float:
    """Full-Kelly bankroll fraction for buying a $1 binary share at `price`."""
    cost = price + fee
    if cost >= 1 or p <= cost:
        return 0.0
    return (p - cost) / (1 - cost)


@dataclass
class Bet:
    side: str
    prob: float
    price: float
    edge: float        # p - price - fee (dollars per $1 share)
    roi: float         # edge / price
    stake: float       # dollars
    kelly_full: float


def size_bet(side: str, p: float, price: float, bankroll: float, *,
             kelly_fraction: float = 0.25, max_bet_pct: float = 0.02,
             min_edge: float = 0.03, fee: float = 0.0) -> Bet | None:
    """Return a Bet if there is enough edge, else None."""
    if not 0 < price < 1:
        return None
    edge = p - price - fee
    if edge < min_edge:
        return None
    kf = kelly_binary(p, price, fee)
    stake = bankroll * min(kf * kelly_fraction, max_bet_pct)
    return Bet(side, p, price, edge, edge / price, round(stake, 2), kf)
