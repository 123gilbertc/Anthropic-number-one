"""Read-only Polymarket client: find tennis head-to-head markets and prices.

Uses the public Gamma API (market metadata) and CLOB API (order books).
No keys needed for reading. This module never places orders.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SPORTS_FEE_RATE = 0.05  # Polymarket sports taker fee rate (Jul 2026); fee = rate * p * (1-p) per share


def parse_time(s: str) -> Optional[datetime]:
    """Parse Polymarket timestamps like '2026-09-25 05:00:00+00' or '2026-09-25T05:00:00Z'."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

CLAY = ("roland garros", "french open", "monte carlo", "monte-carlo", "madrid", "rome",
        "italian open", "barcelona", "hamburg", "buenos aires", "rio", "estoril",
        "geneva", "lyon", "bastad", "gstaad", "umag", "kitzbuhel", "houston",
        "marrakech", "bucharest", "munich", "santiago", "strasbourg", "rabat", "palermo")
GRASS = ("wimbledon", "queen", "halle", "s-hertogenbosch", "hertogenbosch", "eastbourne",
         "mallorca", "stuttgart", "berlin", "bad homburg", "nottingham", "birmingham")
SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")


@dataclass
class Outcome:
    name: str
    token_id: str
    mid: Optional[float]       # Gamma's last/mid price
    best_ask: Optional[float]  # price you'd pay to buy now
    ask_size: Optional[float]


@dataclass
class TennisMarket:
    event_title: str
    question: str
    slug: str
    start: str
    surface: str
    best_of: int
    liquidity: float
    volume: float
    outcomes: list[Outcome]
    tour: str = ""          # atp / wta / "" (from Polymarket's league tag)
    fee_rate: float = 0.0   # taker fee rate; fee per share = rate * p * (1 - p)

    @property
    def start_dt(self) -> Optional[datetime]:
        return parse_time(self.start)

    @property
    def doubles(self) -> bool:
        return "/" in self.outcomes[0].name or "doubles" in self.event_title.lower()

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.slug}"


def guess_surface(text: str) -> str:
    t = text.lower()
    if any(k in t for k in GRASS):
        return "grass"
    if any(k in t for k in CLAY):
        return "clay"
    return "hard"


def guess_best_of(text: str) -> int:
    t = text.lower()
    womens = any(k in t for k in ("wta", "women", "ladies"))
    return 5 if any(k in t for k in SLAMS) and not womens else 3


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def best_ask(session, token_id: str) -> tuple[Optional[float], Optional[float]]:
    try:
        r = session.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=15)
        r.raise_for_status()
        asks = r.json().get("asks") or []
        if not asks:
            return None, None
        top = min(asks, key=lambda a: float(a["price"]))
        return float(top["price"]), float(top["size"])
    except Exception:
        return None, None


def fetch_events(tags=("tennis",), page_size: int = 100, max_pages: int = 20, session=None) -> list[dict]:
    """All active, open events for the given tags (paginated)."""
    import requests

    s = session or requests.Session()
    events, ids = [], set()
    for tag in tags:
        for page in range(max_pages):
            r = s.get(f"{GAMMA}/events", params={"tag_slug": tag, "active": "true", "closed": "false",
                                                   "limit": page_size, "offset": page * page_size},
                      timeout=30)
            r.raise_for_status()
            batch = r.json()
            events += [e for e in batch if e.get("id") not in ids]
            ids.update(e.get("id") for e in batch)
            if len(batch) < page_size:
                break
    return events


def markets_from_events(events: list[dict], session=None) -> list[TennisMarket]:
    seen, out = set(), []
    for ev in events:
        out += _parse_event(ev, session, seen)
    return out


def fetch_tennis_markets(tags=("tennis",), with_books: bool = True, session=None) -> list[TennisMarket]:
    import requests

    s = session or requests.Session()
    return markets_from_events(fetch_events(tags, session=s), s if with_books else None)


def _parse_event(ev: dict, session, seen: set) -> list[TennisMarket]:
    title = ev.get("title", "")
    league = ((ev.get("sport") or {}).get("sport") or ev.get("seriesSlug") or "").lower()
    tour = league if league in ("atp", "wta") else ""
    res = []
    for mk in ev.get("markets", []):
        if mk.get("closed") or not mk.get("active", True) or mk.get("acceptingOrders") is False:
            continue
        smt = mk.get("sportsMarketType")
        q = mk.get("question", "")
        if smt is not None:
            if smt != "moneyline":
                continue  # set winners, handicaps, totals, "completed match" ...
        elif re.search(r"\bsets?\b|games?|handicap|spread|o/u|over|under|total|completed", q, re.I):
            continue
        names = _j(mk.get("outcomes"))
        if len(names) != 2 or {n.lower() for n in names} == {"yes", "no"}:
            continue  # only head-to-head match-winner markets
        cid = mk.get("conditionId") or mk.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        prices = [float(x) for x in _j(mk.get("outcomePrices"))] or [None, None]
        tokens = _j(mk.get("clobTokenIds")) or ["", ""]
        outs = []
        for i, n in enumerate(names):
            ask, size = best_ask(session, tokens[i]) if session and tokens[i] else (None, None)
            if ask is None and i == 0 and mk.get("bestAsk") is not None:
                ask = float(mk["bestAsk"])  # Gamma quotes the first outcome's top of book
            if ask is None and i == 1 and mk.get("bestBid") is not None:
                ask = round(1 - float(mk["bestBid"]), 4)  # buying B == selling A at the bid
            outs.append(Outcome(n, tokens[i], prices[i] if i < len(prices) else None, ask, size))
        ctx = f"{title} {q}"
        fee = SPORTS_FEE_RATE if mk.get("feeType") or mk.get("takerBaseFee") else 0.0
        res.append(TennisMarket(
            event_title=title, question=q, slug=ev.get("slug", ""),
            start=mk.get("gameStartTime") or ev.get("startTime") or ev.get("startDate", ""),
            surface=guess_surface(ctx),
            best_of=guess_best_of(f"{ctx} {'wta' if tour == 'wta' else ''}"),
            liquidity=float(mk.get("liquidityNum") or mk.get("liquidity") or 0),
            volume=float(mk.get("volumeNum") or mk.get("volume") or 0),
            outcomes=outs, tour=tour, fee_rate=fee))
    return res
