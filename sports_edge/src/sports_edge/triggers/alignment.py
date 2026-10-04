"""Temporal alignment and freshness checks.

The classic false "overreaction" signal: the exchange has already repriced
after a goal, but the sportsbook quote we compare against was published
before the goal. Comparing those two is comparing different games.

Rule: a reference quote may only be used if the provider's own update time
is *after* the last material game event (plus a configurable allowance) and
the quote is fresh. A recent *receipt* time is not enough: an aggregator can
deliver an old quote quickly. Unknown provider time means we cannot prove
alignment, so the quote is rejected.
"""

from __future__ import annotations

from datetime import datetime

from sports_edge.domain.enums import Reason
from sports_edge.domain.records import GameStateBase, OrderBookSnapshot, SportsbookQuote
from sports_edge.triggers.strategy import StrategyConfig


def check_reference(q: SportsbookQuote, state: GameStateBase, now: datetime,
                    cfg: StrategyConfig) -> Reason | None:
    if q.provider_last_update is None:
        return Reason.REFERENCE_STALE
    if (q.provider_last_update - q.received_time).total_seconds() > 5:
        return Reason.TIMESTAMP_INCONSISTENT  # "updated" after we received it: clocks disagree
    if now - q.received_time > cfg.max_reference_age:
        return Reason.REFERENCE_STALE
    if now - q.provider_last_update > cfg.max_reference_age:
        return Reason.REFERENCE_STALE
    ev = state.last_material_event_time
    if ev is not None and q.provider_last_update < ev + cfg.reference_post_event_allowance:
        return Reason.REFERENCE_PRE_EVENT
    return None


def check_book(book: OrderBookSnapshot | None, now: datetime, cfg: StrategyConfig,
               feed_last_seen: datetime | None = None) -> list[Reason]:
    """A WebSocket book that has not changed is not stale; a silent connection is.

    ``feed_last_seen`` is the last time the market connection delivered
    anything (including heartbeats). Falls back to the book's own last update.
    """
    if book is None or not book.valid:
        return [Reason.BOOK_INVALID]
    out = []
    if not book.market_open:
        out.append(Reason.MARKET_INACTIVE)
    seen = max(book.received_time, feed_last_seen) if feed_last_seen else book.received_time
    if now - seen > cfg.max_book_age:
        out.append(Reason.BOOK_STALE)
    if not book.asks:
        out.append(Reason.DEPTH_INSUFFICIENT)
    return out


def check_state(state: GameStateBase | None, now: datetime, cfg: StrategyConfig,
                feed_last_seen: datetime | None = None) -> list[Reason]:
    if state is None:
        return [Reason.GAME_FEED_NOT_CONNECTED]
    out = []
    seen = state.as_of_received_time
    if feed_last_seen is not None:
        seen = max(seen, feed_last_seen)
    if now - seen > cfg.max_state_age:
        out.append(Reason.GAME_STATE_STALE)
    if not state.coherent():
        out.append(Reason.GAME_STATE_INCOHERENT)
    if state.pending_reconciliation or state.in_review:
        out.append(Reason.PENDING_RECONCILIATION)
    return out
