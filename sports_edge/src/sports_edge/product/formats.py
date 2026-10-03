"""Display formats computed on the server so every screen shows identical numbers.

Units stay distinct: a contract price in cents, an implied probability, decimal and
American odds for that price, percentage-point edge and percentage return are
different quantities and are labelled separately everywhere.
"""

from __future__ import annotations

from decimal import Decimal


def price_formats(price: Decimal | float | None) -> dict | None:
    """A $1 binary contract bought at ``price`` dollars, as odds (ignoring fees)."""
    if price is None:
        return None
    p = float(price)
    if not 0 < p < 1:
        return None
    dec = 1 / p
    american = round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)
    return {"cents": round(p * 100, 2), "implied_probability": round(p, 4),
            "decimal_odds": round(dec, 3), "american_odds": int(american)}


def probability_formats(p: float | None) -> dict | None:
    """A probability shown as fair odds (no margin, no fees)."""
    if p is None or not 0 < p < 1:
        return None
    dec = 1 / p
    american = round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)
    return {"probability": round(p, 4), "decimal_odds": round(dec, 3),
            "american_odds": int(american)}
