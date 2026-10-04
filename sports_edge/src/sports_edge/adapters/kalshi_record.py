"""Record Kalshi order-book WebSocket messages to JSONL (permitted market recording).

Each line: {"stream": "kalshi", "received_time": <UTC ISO>, "raw": <message>}.
The same file format is consumed by ``replay``. Recording starts early so
that real market paths exist before any strategy is evaluated.

UNTESTED against the live API from the build environment (egress blocked).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sports_edge.adapters.kalshi import (
    WS_PATH,
    KalshiBookManager,
    ReconnectingFeed,
    orderbook_subscription,
    sign_headers,
)


async def record(settings, tickers: list[str], out: Path) -> None:
    import websockets

    assert settings.kalshi_key_id and settings.kalshi_private_key_path
    pem = Path(settings.kalshi_private_key_path).read_bytes()
    manager = KalshiBookManager()

    async def connect():
        headers = sign_headers(settings.kalshi_key_id, pem, "GET", WS_PATH)
        return await websockets.connect(settings.kalshi_ws_url, additional_headers=headers,
                                        ping_interval=10, ping_timeout=10)

    feed = ReconnectingFeed(connect, lambda: [orderbook_subscription(tickers)],
                            manager.invalidate_all)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        async for msg in feed.messages():
            now = datetime.now(UTC)
            f.write(json.dumps({"stream": "kalshi", "received_time": now.isoformat(),
                                "raw": msg}) + "\n")
            f.flush()
            try:
                manager.handle(msg, now)
            except ValueError as e:  # record anyway; surface parse problems loudly
                print(f"PARSE WARNING (message recorded verbatim): {e}")
