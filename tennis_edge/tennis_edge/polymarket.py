"""Read-only Polymarket client: find tennis head-to-head markets and prices.

Uses the public Gamma API (market metadata) and CLOB API (order books).
No keys needed for reading. This module never places orders.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

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


def fetch_tennis_markets(tags=("tennis",), limit: int = 200, with_books: bool = True,
                         session=None) -> list[TennisMarket]:
    import requests

    s = session or requests.Session()
    seen, out = set(), []
    for tag in tags:
        r = s.get(f"{GAMMA}/events", params={"tag_slug": tag, "active": "true",
                                               "closed": "false", "limit": limit}, timeout=30)
        r.raise_for_status()
        for ev in r.json():
            out += _parse_event(ev, s if with_books else None, seen)
    return out


def _parse_event(ev: dict, session, seen: set) -> list[TennisMarket]:
    title = ev.get("title", "")
    res = []
    for mk in ev.get("markets", []):
        if mk.get("closed") or not mk.get("active", True):
            continue
        names = _j(mk.get("outcomes"))
        if len(names) != 2 or {n.lower() for n in names} == {"yes", "no"}:
            continue  # only head-to-head match-winner markets
        q = mk.get("question", "")
        if re.search(r"\bset\b|games|handicap|o/u|over|under|total", q, re.I):
            continue  # skip set / game / totals props
        cid = mk.get("conditionId") or mk.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        prices = [float(x) for x in _j(mk.get("outcomePrices"))] or [None, None]
        tokens = _j(mk.get("clobTokenIds")) or ["", ""]
        outs = []
        for i, n in enumerate(names):
            ask, size = best_ask(session, tokens[i]) if session and tokens[i] else (None, None)
            outs.append(Outcome(n, tokens[i], prices[i] if i < len(prices) else None, ask, size))
        ctx = f"{title} {q}"
        res.append(TennisMarket(
            event_title=title, question=q, slug=ev.get("slug", ""),
            start=mk.get("gameStartTime") or ev.get("startDate", ""),
            surface=guess_surface(ctx), best_of=guess_best_of(ctx),
            liquidity=float(mk.get("liquidity") or 0), volume=float(mk.get("volume") or 0),
            outcomes=outs))
    return res
