"""Executable-price modelling: walk the visible book for a requested size.

A last trade, midpoint, or displayed percentage is not a price we can buy
at. The cost of buying N contracts is the sum over the ask levels we would
consume. If the book is too thin we get a partial fill, never an invented one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sports_edge.domain.records import BookLevel
from sports_edge.pricing.fees import FeeModel


@dataclass(frozen=True)
class FillEstimate:
    requested: int
    filled: int
    cost: Decimal  # dollars paid for contracts, spread and slippage included
    fees: Decimal  # expected entry fees (charged per consumed level: conservative)
    levels: tuple[tuple[Decimal, int], ...]
    worst_price: Decimal | None

    @property
    def partial(self) -> bool:
        return self.filled < self.requested

    @property
    def average_price(self) -> Decimal | None:
        return self.cost / self.filled if self.filled else None


def walk_asks(
    asks: tuple[BookLevel, ...],
    quantity: int,
    fee_model: FeeModel,
    limit_price: Decimal | None = None,
    depth_haircut: Decimal = Decimal("1"),
) -> FillEstimate:
    """Simulate a marketable limit buy against the visible ask ladder.

    ``depth_haircut`` < 1 assumes only that fraction of displayed size is
    really available to us (others may be ahead, quotes may be pulled).
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remaining = quantity
    cost = Decimal("0")
    fees = Decimal("0")
    used: list[tuple[Decimal, int]] = []
    prev = Decimal("-1")
    for lvl in asks:
        if lvl.price <= prev:
            raise ValueError("asks must be strictly ascending")
        prev = lvl.price
        if limit_price is not None and lvl.price > limit_price:
            break
        avail = int(Decimal(lvl.quantity) * depth_haircut)
        take = min(avail, remaining)
        if take <= 0:
            continue
        cost += lvl.price * take
        fees += fee_model.entry_fee(take, lvl.price)
        used.append((lvl.price, take))
        remaining -= take
        if remaining == 0:
            break
    return FillEstimate(
        requested=quantity,
        filled=quantity - remaining,
        cost=cost,
        fees=fees,
        levels=tuple(used),
        worst_price=used[-1][0] if used else None,
    )


def max_quantity_within_budget(
    asks: tuple[BookLevel, ...],
    budget: Decimal,
    fee_model: FeeModel,
    limit_price: Decimal | None = None,
    depth_haircut: Decimal = Decimal("1"),
) -> int:
    """Largest whole quantity whose all-in cost (contracts + fees) fits ``budget``."""
    total_depth = sum(int(Decimal(level.quantity) * depth_haircut) for level in asks)
    lo, hi = 0, total_depth
    while lo < hi:
        mid = (lo + hi + 1) // 2
        est = walk_asks(asks, mid, fee_model, limit_price, depth_haircut)
        if est.filled == mid and est.cost + est.fees <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo
