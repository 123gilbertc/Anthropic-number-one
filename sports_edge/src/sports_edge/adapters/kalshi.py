"""Kalshi market-data adapter.

Status (see DATA_SOURCES.md): message field names below follow Kalshi's
WebSocket docs as found by search on 2026-10-02 (``orderbook_snapshot`` then
``orderbook_delta``, dollar-string prices, ``seq`` per subscription). They
are marked SNIPPET, not VERIFIED, until a recorded real message confirms
them. The parser therefore accepts the known variants (integer cents or
dollar strings) and rejects anything else loudly rather than guessing.

The WebSocket handshake requires API-key signing (RSA-PSS over
timestamp + method + path) even for public channels.

Transport behaviour:
* reconnect with capped exponential backoff + jitter;
* on every (re)connect: resubscribe, then require a fresh snapshot before
  any book is valid again;
* per-subscription ``seq`` gap -> book invalidated -> resubscribe for a
  fresh snapshot;
* heartbeat watchdog: no message for ``heartbeat_timeout`` -> reconnect.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sports_edge.domain.enums import SourceStatus, Venue
from sports_edge.ingest.orderbook import BookGap, LocalBook

WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
REST_BASE = "https://external-api.kalshi.com/trade-api/v2"


class KalshiParseError(ValueError):
    pass


def _price(level_or_msg: Any, key_dollars: str = "price_dollars", key_cents: str = "price"
           ) -> Decimal:
    if isinstance(level_or_msg, dict):
        if key_dollars in level_or_msg:
            return Decimal(str(level_or_msg[key_dollars]))
        if key_cents in level_or_msg:
            return Decimal(int(level_or_msg[key_cents])) / 100
        raise KalshiParseError(f"no price field in {level_or_msg}")
    raise KalshiParseError("unexpected price container")


def _level(x: Any, dollars: bool) -> tuple[Decimal, int]:
    if not isinstance(x, list | tuple) or len(x) != 2:
        raise KalshiParseError(f"bad level {x!r}")
    p = Decimal(str(x[0])) if dollars else Decimal(int(x[0])) / 100
    q = int(Decimal(str(x[1])))
    if not (Decimal(0) < p < Decimal(1)) or q < 0:
        raise KalshiParseError(f"level out of range {x!r}")
    return p, q


def _ladder(msg: dict, side: str) -> list[tuple[Decimal, int]]:
    for key, dollars in ((f"{side}_dollars_fp", True), (f"{side}_dollars", True), (side, False)):
        if key in msg:
            return [_level(x, dollars) for x in (msg[key] or [])]
    return []


@dataclass
class KalshiBookManager:
    """Applies parsed WS messages to local books; tracks gaps for resync."""

    source_status: SourceStatus = SourceStatus.LIVE
    books: dict[str, LocalBook] = field(default_factory=dict)
    sid_seq: dict[int, int] = field(default_factory=dict)
    needs_resync: set[str] = field(default_factory=set)
    messages: int = 0

    sid_tickers: dict[int, set[str]] = field(default_factory=dict)
    gaps: int = 0
    duplicates: int = 0

    def handle(self, raw: dict, received: datetime) -> str | None:
        """Apply one message. Returns the affected market ticker, if any.

        ``seq`` is treated as a per-subscription (``sid``) counter: a gap
        invalidates every book on that subscription. Without ``sid`` we fall
        back to per-market sequence checking inside ``LocalBook``.
        """
        self.messages += 1
        typ = raw.get("type")
        msg = raw.get("msg") or {}
        if typ not in ("orderbook_snapshot", "orderbook_delta"):
            return None
        ticker = msg.get("market_ticker")
        if not ticker:
            raise KalshiParseError("orderbook message without market_ticker")
        seq = raw.get("seq")
        if not isinstance(seq, int):
            raise KalshiParseError("orderbook message without integer seq")
        book = self.books.setdefault(
            ticker, LocalBook(Venue.KALSHI, ticker, self.source_status))
        ts = msg.get("ts")
        exch = datetime.fromtimestamp(ts, UTC) if isinstance(ts, int | float) else None

        sid = raw.get("sid")
        if isinstance(sid, int):
            last = self.sid_seq.get(sid)
            if last is not None and seq <= last:
                self.duplicates += 1
                return None
            self.sid_tickers.setdefault(sid, set()).add(ticker)
            gap = last is not None and seq > last + 1
            self.sid_seq[sid] = seq
            if gap:
                self.gaps += 1
                for t in self.sid_tickers[sid]:
                    self.books[t].invalidate("sequence gap")
                    self.needs_resync.add(t)
                if typ == "orderbook_delta":
                    return ticker
            # per-book sequence becomes a local counter
            local_seq = (book.next_seq or 0)
        else:
            local_seq = seq

        if typ == "orderbook_snapshot":
            book.apply_snapshot(local_seq, _ladder(msg, "yes"), _ladder(msg, "no"), received, exch)
            self.needs_resync.discard(ticker)
            return ticker
        side = msg.get("side")
        if side not in ("yes", "no"):
            raise KalshiParseError(f"bad side {side!r}")
        delta_raw = msg.get("delta_fp", msg.get("delta"))
        if delta_raw is None:
            raise KalshiParseError("delta missing")
        if not book.valid:
            self.needs_resync.add(ticker)
            return ticker
        try:
            book.apply_delta(local_seq, side, _price(msg), int(Decimal(str(delta_raw))),
                             received, exch)
        except BookGap:
            book.invalidate()
            self.needs_resync.add(ticker)
        return ticker

    def invalidate_all(self) -> None:
        for t, b in self.books.items():
            b.invalidate("connection lost")
            self.needs_resync.add(t)


# --------------------------------------------------------------------- transport


def sign_headers(key_id: str, private_key_pem: bytes, method: str = "GET",
                 path: str = WS_PATH) -> dict[str, str]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    ts = str(int(time.time() * 1000))
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    sig = key.sign(  # type: ignore[union-attr]
        (ts + method + path).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts}


Connector = Callable[[], Awaitable[Any]]  # returns an object with send/recv/close


@dataclass
class ReconnectingFeed:
    """Generic reconnect/backoff/heartbeat loop around a message connection."""

    connect: Connector
    subscribe_messages: Callable[[], list[dict]]
    on_disconnect: Callable[[], None]
    heartbeat_timeout: float = 30.0
    base_backoff: float = 0.5
    max_backoff: float = 30.0
    max_attempts: int | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    reconnects: int = 0

    async def messages(self) -> AsyncIterator[dict]:
        attempt = 0
        while self.max_attempts is None or attempt < self.max_attempts:
            try:
                conn = await self.connect()
            except Exception:
                attempt += 1
                await self.sleep(self._backoff(attempt))
                continue
            attempt = 0
            try:
                for m in self.subscribe_messages():
                    await conn.send(json.dumps(m))
                while True:
                    raw = await asyncio.wait_for(conn.recv(), timeout=self.heartbeat_timeout)
                    yield json.loads(raw)
            except (TimeoutError, ConnectionError, OSError, EOFError):
                pass
            finally:
                self.on_disconnect()
                self.reconnects += 1
                try:
                    await conn.close()
                except Exception:
                    pass
            attempt += 1
            await self.sleep(self._backoff(attempt))

    def _backoff(self, attempt: int) -> float:
        b = min(self.max_backoff, self.base_backoff * 2 ** (attempt - 1))
        return b * (0.5 + random.random() / 2)


def orderbook_subscription(tickers: list[str], msg_id: int = 1) -> dict:
    return {"id": msg_id, "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": tickers}}
