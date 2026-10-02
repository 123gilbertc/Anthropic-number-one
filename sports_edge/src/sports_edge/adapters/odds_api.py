"""The Odds API v4 adapter (sportsbook reference quotes).

Status: SNIPPET-level documentation (see DATA_SOURCES.md). Pinnacle appears
under ``regions=eu`` with a stated delay; Circa coverage is UNKNOWN. This is
an aggregated, polled feed: ``last_update`` is the aggregator's view of the
bookmaker's update time and is never treated as instantaneous.

Cost per live-odds request = (#markets) x (#regions) credits. The client
records the ``x-requests-remaining`` / ``x-requests-used`` response headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from sports_edge.domain.enums import SettlementRule
from sports_edge.domain.records import SportsbookQuote, stable_id

BASE = "https://api.the-odds-api.com/v4"


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_h2h(events: list[dict], team_codes: dict[str, str], game_ids: dict[tuple, str],
              received: datetime, rule: SettlementRule, books: set[str] | None = None,
              ) -> list[SportsbookQuote]:
    """``team_codes``: provider team name -> our code. ``game_ids``: (home, away) codes -> id.

    Events/outcomes we cannot map are skipped, never guessed.
    """
    out = []
    for ev in events:
        home, away = team_codes.get(ev.get("home_team", "")), team_codes.get(ev.get("away_team", ""))
        gid = game_ids.get((home, away))
        if gid is None:
            continue
        for bk in ev.get("bookmakers", []):
            if books is not None and bk.get("key") not in books:
                continue
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                prices = {}
                ok = True
                for o in mk.get("outcomes", []):
                    code = team_codes.get(o.get("name", ""))
                    price = o.get("price")
                    if code is None or not isinstance(price, int):
                        ok = False
                        break
                    prices[code] = price
                if not ok or set(prices) != {home, away}:
                    continue
                last = _ts(mk.get("last_update") or bk.get("last_update"))
                out.append(SportsbookQuote(
                    quote_id=stable_id("q", [gid, bk["key"], prices, str(last)]),
                    game_id=gid, book=bk["key"], market="h2h", settlement_rule=rule,
                    prices_american=prices, provider_last_update=last,
                    received_time=received, source="the_odds_api"))
    return out


@dataclass
class OddsApiClient:
    api_key: str
    requests_remaining: int | None = None
    requests_used: int | None = None

    async def h2h(self, sport_key: str, regions: str = "us,eu",
                  bookmakers: str | None = None) -> list[dict]:
        params = {"apiKey": self.api_key, "markets": "h2h", "oddsFormat": "american",
                  "dateFormat": "iso"}
        if bookmakers:
            params["bookmakers"] = bookmakers
        else:
            params["regions"] = regions
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BASE}/sports/{sport_key}/odds", params=params)
            r.raise_for_status()
        self.requests_remaining = _int(r.headers.get("x-requests-remaining"))
        self.requests_used = _int(r.headers.get("x-requests-used"))
        return r.json()


def _int(x: str | None) -> int | None:
    try:
        return int(float(x)) if x is not None else None
    except ValueError:
        return None
