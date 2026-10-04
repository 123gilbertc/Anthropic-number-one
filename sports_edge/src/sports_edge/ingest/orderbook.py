"""Local order book maintained from snapshot + delta messages.

Policy (documented in docs/ARCHITECTURE.md):

* A snapshot replaces the book and sets the expected next sequence number.
* A delta with ``seq == expected`` is applied.
* A delta with ``seq < expected`` is a duplicate/late message and is ignored.
* A delta with ``seq > expected`` means we missed messages: the book is
  marked INVALID and stays invalid until a fresh snapshot arrives. We never
  guess what the missing messages contained.

Kalshi books are two bid ladders (YES bids and NO bids). Buying YES at price
p is matched against a resting NO bid at 1 - p, so YES asks are derived from
NO bids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sports_edge.domain.enums import SourceStatus, Venue
from sports_edge.domain.records import BookLevel, OrderBookSnapshot, stable_id

ONE = Decimal("1")


class BookGap(Exception):
    pass


@dataclass
class LocalBook:
    venue: Venue
    contract_id: str
    source_status: SourceStatus
    yes_bids: dict[Decimal, int] = field(default_factory=dict)
    no_bids: dict[Decimal, int] = field(default_factory=dict)
    next_seq: int | None = None
    valid: bool = False
    market_open: bool = True
    last_received: datetime | None = None
    last_exchange_time: datetime | None = None
    gaps_detected: int = 0
    duplicates_ignored: int = 0

    def apply_snapshot(
        self,
        seq: int,
        yes: list[tuple[Decimal, int]],
        no: list[tuple[Decimal, int]],
        received: datetime,
        exchange_time: datetime | None = None,
    ) -> None:
        self.yes_bids = {p: q for p, q in yes if q > 0}
        self.no_bids = {p: q for p, q in no if q > 0}
        self.next_seq = seq + 1
        self.valid = True
        self.last_received = received
        self.last_exchange_time = exchange_time

    def apply_delta(
        self,
        seq: int,
        side: str,
        price: Decimal,
        delta: int,
        received: datetime,
        exchange_time: datetime | None = None,
    ) -> None:
        if self.next_seq is None:
            self.valid = False
            raise BookGap("delta before snapshot")
        if seq < self.next_seq:
            self.duplicates_ignored += 1
            return
        if seq > self.next_seq:
            self.valid = False
            self.gaps_detected += 1
            raise BookGap(f"expected seq {self.next_seq}, got {seq}")
        ladder = self.yes_bids if side == "yes" else self.no_bids
        new_q = ladder.get(price, 0) + delta
        if new_q < 0:
            self.valid = False
            raise BookGap(f"negative size at {side}@{price}: book out of sync")
        if new_q == 0:
            ladder.pop(price, None)
        else:
            ladder[price] = new_q
        self.next_seq = seq + 1
        self.last_received = received
        self.last_exchange_time = exchange_time

    def invalidate(self, reason: str = "") -> None:
        self.valid = False
        self.next_seq = None

    def snapshot(self) -> OrderBookSnapshot:
        bids = tuple(
            BookLevel(price=p, quantity=q) for p, q in sorted(self.yes_bids.items(), reverse=True)
        )
        asks = tuple(
            BookLevel(price=ONE - p, quantity=q)
            for p, q in sorted(self.no_bids.items(), reverse=True)
        )
        received = self.last_received
        if received is None:
            raise BookGap("no data received yet")
        seq = None if self.next_seq is None else self.next_seq - 1
        return OrderBookSnapshot(
            book_id=stable_id(
                "book", [self.contract_id, seq, str(received), self.valid, list(map(str, asks))]
            ),
            venue=self.venue,
            contract_id=self.contract_id,
            source_status=self.source_status,
            received_time=received,
            exchange_time=self.last_exchange_time,
            seq=seq,
            valid=self.valid,
            market_open=self.market_open,
            bids=bids,
            asks=asks,
        )
