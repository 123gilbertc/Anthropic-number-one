"""Odds conversions and sportsbook margin removal.

Sportsbook prices include a margin ("vig"): the implied probabilities of all
outcomes add up to more than 100%. To compare a book to a probability we
remove that margin. We use two documented methods:

* proportional (a.k.a. multiplicative): divide each implied probability by
  the total. Simple, but known to under-correct longshots.
* power: find k so that sum(p_i ** k) == 1. Shifts more margin onto longshots.

Both require *every* outcome of the market. A quote missing an outcome is
rejected, because removing margin from a partial market is meaningless.
"""

from __future__ import annotations

from typing import Literal


class OddsError(ValueError):
    pass


def american_to_decimal(american: int) -> float:
    if american == 0 or -100 < american < 100:
        raise OddsError(f"invalid American odds {american}")
    return 1 + (american / 100 if american > 0 else 100 / -american)


def american_to_implied(american: int) -> float:
    return 1 / american_to_decimal(american)


def overround(implied: list[float]) -> float:
    return sum(implied) - 1


def remove_margin(
    american_by_outcome: dict[str, int],
    expected_outcomes: set[str],
    method: Literal["proportional", "power"] = "proportional",
) -> dict[str, float]:
    if set(american_by_outcome) != expected_outcomes:
        raise OddsError(
            f"quote outcomes {sorted(american_by_outcome)} != required {sorted(expected_outcomes)}"
        )
    implied = {k: american_to_implied(v) for k, v in american_by_outcome.items()}
    total = sum(implied.values())
    if total < 1:
        raise OddsError("implied probabilities sum below 1: not a normal bookmaker quote")
    if method == "proportional":
        return {k: v / total for k, v in implied.items()}
    if method == "power":
        lo, hi = 1.0, 10.0
        for _ in range(100):
            mid = (lo + hi) / 2
            s = sum(p**mid for p in implied.values())
            lo, hi = (mid, hi) if s > 1 else (lo, mid)
        k = (lo + hi) / 2
        fair = {o: p**k for o, p in implied.items()}
        z = sum(fair.values())
        return {o: p / z for o, p in fair.items()}
    raise OddsError(f"unknown method {method}")
