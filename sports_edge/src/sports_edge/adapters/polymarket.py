"""Polymarket (international CLOB) market-channel parser — Phase 5 venue.

Status: SNIPPET-level docs (DATA_SOURCES.md). Message types handled:
``book`` (full snapshot of one asset's bids/asks) and ``price_change``
(absolute new sizes at price levels). The market channel documents no
sequence numbers, so gaps cannot be detected from the stream: after any
reconnect every book is invalid until a fresh ``book`` message arrives.

Do NOT use for Polymarket US: it is a different exchange with its own API,
fees and eligibility. US persons are blocked on the international venue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from sports_edge.domain.enums import SourceStatus, Venue
from sports_edge.domain.records import BookLevel, OrderBookSnapshot, stable_id


class PolymarketParseError(ValueError):
    pass


@dataclass(frozen=True)
class PolymarketTakerFee:
    """fee = C x rate x p x (1 - p); sports rate 0.05 (SNIPPET). Makers pay 0.

    Rounding is not documented in what we could read: we round UP to the cent,
    the conservative choice, and mark it UNKNOWN in DATA_SOURCES.md.
    """

    rate: Decimal = Decimal("0.05")
    name: str = "polymarket_intl_taker"
    source_note: str = "SNIPPET; rounding UNKNOWN (rounded up)"

    def entry_fee(self, quantity: int, price: Decimal) -> Decimal:
        if quantity <= 0:
            return Decimal(0)
        return (self.rate * quantity * price * (1 - price)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING)

    def settlement_fee(self, quantity: int, won: bool) -> Decimal:
        return Decimal(0)


@dataclass
class _Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    valid: bool = False
    received: datetime | None = None
    tick: Decimal = Decimal("0.01")


@dataclass
class PolymarketBookManager:
    source_status: SourceStatus = SourceStatus.LIVE
    books: dict[str, _Book] = field(default_factory=dict)

    @staticmethod
    def _levels(xs) -> dict[Decimal, Decimal]:
        out = {}
        for x in xs or []:
            p, s = Decimal(str(x["price"])), Decimal(str(x["size"]))
            if not Decimal(0) < p < Decimal(1) or s < 0:
                raise PolymarketParseError(f"bad level {x}")
            if s > 0:
                out[p] = s
        return out

    def handle(self, msg: dict, received: datetime) -> list[str]:
        """Apply one message (or list of messages). Returns affected asset ids."""
        if isinstance(msg, list):
            out: list[str] = []
            for m in msg:
                out += self.handle(m, received)
            return out
        typ = msg.get("event_type")
        if typ == "book":
            aid = msg.get("asset_id")
            if not aid:
                raise PolymarketParseError("book without asset_id")
            b = self.books.setdefault(aid, _Book())
            b.bids, b.asks = self._levels(msg.get("bids")), self._levels(msg.get("asks"))
            b.valid, b.received = True, received
            return [aid]
        if typ == "price_change":
            touched = []
            for ch in msg.get("price_changes") or msg.get("changes") or []:
                aid = ch.get("asset_id") or msg.get("asset_id")
                b = self.books.get(aid)
                if b is None or not b.valid:
                    continue  # no snapshot yet: wait for a book message
                p, s = Decimal(str(ch["price"])), Decimal(str(ch["size"]))
                side = b.bids if ch.get("side", "").upper() == "BUY" else b.asks
                if s == 0:
                    side.pop(p, None)
                else:
                    side[p] = s
                b.received = received
                touched.append(aid)
            return touched
        if typ == "tick_size_change":
            b = self.books.get(msg.get("asset_id", ""))
            if b is not None:
                b.tick = Decimal(str(msg.get("new_tick_size", b.tick)))
            return []
        return []

    def invalidate_all(self) -> None:
        for b in self.books.values():
            b.valid = False

    def snapshot(self, asset_id: str) -> OrderBookSnapshot:
        b = self.books[asset_id]
        if b.received is None:
            raise PolymarketParseError("no data")
        asks = tuple(BookLevel(price=p, quantity=int(q)) for p, q in sorted(b.asks.items()))
        bids = tuple(BookLevel(price=p, quantity=int(q))
                     for p, q in sorted(b.bids.items(), reverse=True))
        return OrderBookSnapshot(
            book_id=stable_id("pmbook", [asset_id, str(b.received), [str(a) for a in asks]]),
            venue=Venue.POLYMARKET_INTL, contract_id=asset_id, source_status=self.source_status,
            received_time=b.received, exchange_time=None, seq=None, valid=b.valid,
            market_open=True, bids=bids, asks=asks)
