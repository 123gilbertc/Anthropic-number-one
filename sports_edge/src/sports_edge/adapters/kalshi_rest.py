"""Kalshi public REST helpers (market discovery for mapping verification).

UNTESTED against the live API from the build environment (egress blocked).
Output includes each market's rules text so a human can confirm whether a
game market settles including overtime/shootout before any mapping is made.
"""

from __future__ import annotations

import hashlib

import httpx


async def list_game_markets(base: str, series: str, status: str = "open") -> list[dict]:
    out: list[dict] = []
    cursor = None
    async with httpx.AsyncClient(timeout=15) as c:
        while True:
            params = {"series_ticker": series, "status": status, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = await c.get(f"{base}/markets", params=params)
            r.raise_for_status()
            body = r.json()
            for m in body.get("markets", []):
                rules = (m.get("rules_primary") or "") + "\n" + (m.get("rules_secondary") or "")
                out.append({
                    "ticker": m.get("ticker"),
                    "event_ticker": m.get("event_ticker"),
                    "title": m.get("title"),
                    "yes_sub_title": m.get("yes_sub_title"),
                    "close_time": m.get("close_time"),
                    "rules_text": rules.strip(),
                    "rules_text_sha256": hashlib.sha256(rules.encode()).hexdigest(),
                    "mapping_status": "UNVERIFIED: read rules_text and confirm OT/SO handling",
                })
            cursor = body.get("cursor")
            if not cursor:
                return out
