"""Venue fee models.

Fees are configuration, not constants: venues change them, and some markets
have their own schedule. Every fee model records where its formula came from
and when it was checked, and DATA_SOURCES.md must agree with it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Protocol


class FeeModel(Protocol):
    name: str
    source_note: str

    def entry_fee(self, quantity: int, price: Decimal) -> Decimal: ...

    def settlement_fee(self, quantity: int, won: bool) -> Decimal: ...


@dataclass(frozen=True)
class KalshiQuadraticFee:
    """Kalshi-style trading fee: round_up_to_cent(rate * C * P * (1 - P)).

    ``rate`` must be set from the current Kalshi fee schedule for the
    specific series; the default is a placeholder labelled UNVERIFIED until
    DATA_SOURCES.md records a checked value.
    """

    rate: Decimal = Decimal("0.07")
    name: str = "kalshi_quadratic"
    source_note: str = "UNVERIFIED default; confirm in DATA_SOURCES.md"

    def entry_fee(self, quantity: int, price: Decimal) -> Decimal:
        if quantity <= 0:
            return Decimal("0")
        raw = self.rate * quantity * price * (1 - price)
        return raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING)

    def settlement_fee(self, quantity: int, won: bool) -> Decimal:
        return Decimal("0")


@dataclass(frozen=True)
class ZeroFee:
    name: str = "zero"
    source_note: str = "test/synthetic only"

    def entry_fee(self, quantity: int, price: Decimal) -> Decimal:
        return Decimal("0")

    def settlement_fee(self, quantity: int, won: bool) -> Decimal:
        return Decimal("0")


def round_to_tick(price: Decimal, tick: Decimal, direction: str) -> Decimal:
    """Round a limit price onto the venue tick grid."""
    n = price / tick
    n = Decimal(math.ceil(n)) if direction == "up" else Decimal(math.floor(n))
    return n * tick
