"""Polymarket edge scanner: fair probability vs. executable price, arbitrage and sanity checks.

Pricing conventions
-------------------
* A market carries one order book for its YES token (``market.book``; ``yes_index`` says which
  outcome/token is "YES"). Buying YES executes at the book's **best ask**; buying NO executes at
  ``1 - best_bid`` (selling YES to the best bid is the same trade as buying NO).
* **Slippage** is additive in price space: ``effective_price = min(0.999, price + cfg.slippage_buffer)``.
  A 1c buffer means we assume we fill 1c above the touch. This is deliberately *not* multiplicative,
  so a 5c longshot and a 95c favourite are both charged the same absolute buffer.
* **Fee** ``f`` is the taker fee on winnings (``cfg.fee_rate`` or a per-market override). It enters
  through the breakeven probability

      ``breakeven = effective_price / (effective_price + (1 - effective_price) * (1 - f))``

  and ``edge = fair_prob - breakeven``. With ``f == 0`` and no slippage buffer this reduces to
  ``fair_prob - price``.
* Markets with no book are evaluated with ``prices[yes_index]`` as both bid and ask and their
  ``market.liquidity`` as the liquidity figure; the ``reason`` field flags this.

* **Liquidity** is the USD a buyer of the side can deploy within the slippage buffer of the touch:
  for YES ``sum(p * size)`` over asks with ``p <= best_ask + buffer``; for NO
  ``sum((1 - p) * size)`` over YES bids with ``p >= best_bid - buffer`` (a NO share bought against
  a YES bid at ``p`` costs ``1 - p``). ``size_positions`` never stakes more than this figure.
* **Correlation key**: every opportunity carries an ``exposure_key`` used for
  ``cfg.max_game_exposure``: the caller's ``game_id`` when supplied, else the market's
  ``event_slug`` for game markets (moneyline / spread / total / props of one game share it), else
  the sorted ``home|away`` pair, else the market id.

The scanner never bets outside ``[cfg.min_prob, cfg.max_prob]`` -- the band is enforced on the
side's fair probability *and* on its price and effective price (no 3c longshots, no 97c
favourites) -- never below ``cfg.min_liquidity_usd`` of depth within the slippage buffer, and
never below ``cfg.min_edge``. Sizing is delegated to :func:`nfl_edge.strategy.kelly.size_positions`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

import pandas as pd

from ..config import DEFAULT_STRATEGY, StrategyConfig
from ..teams import CURRENT_TEAMS, DIVISIONS, canonical
from .kelly import breakeven_prob, expected_value, fractional_kelly, kelly_share, size_positions

if TYPE_CHECKING:  # pragma: no cover - type-only import; module is written by another builder
    from ..markets.polymarket import OrderBook, PolyMarket

__all__ = [
    "Opportunity",
    "OPPORTUNITY_COLUMNS",
    "DIVISION_SUM_RANGE",
    "EXCLUSIVE_FUTURES",
    "GAME_KINDS",
    "effective_price",
    "futures_category",
    "market_category",
    "market_mid",
    "find_edges",
    "find_arbitrage",
    "consistency_checks",
    "opportunities_table",
]

# Market kinds that refer to one scheduled game; their positions are correlated and share the
# ``max_game_exposure`` cap (see ``_exposure_key``).
GAME_KINDS = frozenset({"moneyline", "spread", "total"})

# Highest price we ever assume a fill at; keeps breakeven/Kelly math finite.
_MAX_EFFECTIVE_PRICE = 0.999

# Division sums of YES mids outside this window are flagged by ``consistency_checks``.
DIVISION_SUM_RANGE = (0.90, 1.15)

OPPORTUNITY_COLUMNS = [
    "market_id",
    "question",
    "kind",
    "side",
    "token_id",
    "teams",
    "game_id",
    "event_slug",
    "exposure_key",
    "fair_prob",
    "price",
    "effective_price",
    "breakeven",
    "edge",
    "ev",
    "kelly",
    "stake_fraction",
    "stake_usd",
    "liquidity_usd",
    "fee",
    "reason",
]


@dataclass
class Opportunity:
    """A single buyable side of a market with positive, executable edge."""

    market_id: str
    question: str
    kind: str
    side: str  # "YES" | "NO"
    token_id: str
    fair_prob: float  # model probability that this side pays out
    price: float  # best ask of this side (NO ask = 1 - YES best bid)
    effective_price: float  # price + cfg.slippage_buffer, capped at 0.999
    edge: float  # fair_prob - breakeven(effective_price, fee)
    ev: float  # expected profit per $1 staked at the effective price
    kelly: float  # full-Kelly fraction at the effective price
    stake_fraction: float  # fraction of bankroll after fractional Kelly and caps
    stake_usd: float
    liquidity_usd: float  # depth within the slippage buffer (or market.liquidity without a book)
    game_id: str | None
    teams: list[str] = field(default_factory=list)
    reason: str = ""
    breakeven: float = 0.0
    fee: float = 0.0
    event_slug: str | None = None
    # Correlation key for ``cfg.max_game_exposure``: "game:<id>" | "event:<slug>" | "teams:A|B" | "market:<id>"
    exposure_key: str | None = None


# ------------------------------------------------------------------ helpers
def effective_price(price: float, cfg: StrategyConfig = DEFAULT_STRATEGY) -> float:
    """Assumed fill price: ``min(0.999, price + cfg.slippage_buffer)`` (additive slippage)."""
    return min(_MAX_EFFECTIVE_PRICE, float(price) + float(cfg.slippage_buffer))


def _is_valid_price(x: Any) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return not math.isnan(v) and 0.0 < v < 1.0


def _quotes(market: "PolyMarket") -> tuple[float | None, float | None, bool]:
    """(YES best bid, YES best ask, has_book) for a market, falling back to ``prices``."""
    book = market.book
    if book is not None:
        bid, ask = book.best_bid, book.best_ask
        return (
            float(bid) if bid is not None and _is_valid_price(bid) else None,
            float(ask) if ask is not None and _is_valid_price(ask) else None,
            True,
        )
    prices = list(market.prices or [])
    idx = int(market.yes_index)
    if 0 <= idx < len(prices) and _is_valid_price(prices[idx]):
        p = float(prices[idx])
        return p, p, False
    return None, None, False


def market_mid(market: "PolyMarket") -> float | None:
    """Mid of the YES book (or the one quoted side), else ``prices[yes_index]``; None if unquoted."""
    bid, ask, _ = _quotes(market)
    if bid is not None and ask is not None:
        return 0.5 * (bid + ask)
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    return None


def _market_teams(market: "PolyMarket") -> list[str]:
    out: list[str] = []
    for attr in ("home", "away", "team"):
        code = getattr(market, attr)
        if code:
            c = canonical(code)
            if c not in out:
                out.append(c)
    return out


def _token_for_side(market: "PolyMarket", side: str) -> str:
    tokens = list(market.token_ids or [])
    idx = int(market.yes_index)
    if side == "YES":
        return str(tokens[idx]) if 0 <= idx < len(tokens) else ""
    if len(tokens) == 2:
        return str(tokens[1 - idx])
    return ""


def _is_binary(market: "PolyMarket") -> bool:
    return len(list(market.outcomes or [])) == 2


def _side_depth_usd(book: "OrderBook", side: str, buffer: float) -> float:
    """USD a buyer of ``side`` can deploy within ``buffer`` (absolute price) of the touch.

    YES consumes the asks of the YES book: ``sum(p * size)`` for ``p <= best_ask + buffer``.
    NO consumes its bids, and a NO share bought against a YES bid at ``p`` costs ``1 - p``:
    ``sum((1 - p) * size)`` for ``p >= best_bid - buffer``. (``OrderBook.depth_usd('bid')`` is the
    YES *seller's* notional ``p * size`` -- the wrong number for a NO buyer.)
    """
    buffer = max(0.0, float(buffer))
    if side == "YES":
        levels = [(float(p), float(s)) for p, s in (getattr(book, "asks", None) or [])]
        if not levels:
            return 0.0
        limit = min(p for p, _ in levels) + buffer + 1e-12
        return float(sum(p * s for p, s in levels if p <= limit))
    levels = [(float(p), float(s)) for p, s in (getattr(book, "bids", None) or [])]
    if not levels:
        return 0.0
    limit = max(p for p, _ in levels) - buffer - 1e-12
    return float(sum((1.0 - p) * s for p, s in levels if p >= limit))


def _exposure_key(market: "PolyMarket", market_id: str, game_id: str | None) -> str:
    """Correlation key for ``cfg.max_game_exposure``.

    ``game:<game_id>`` when the game is known; else, for anything that refers to one scheduled
    game (kind in ``GAME_KINDS`` or both ``home`` and ``away`` set), ``event:<event_slug>``
    (moneyline, spread, total and props of one game share the slug) or the sorted
    ``teams:<home>|<away>`` pair; else ``market:<market_id>`` (the two sides of one market are
    never double-counted).
    """
    if game_id is not None:
        return f"game:{game_id}"
    home = getattr(market, "home", None)
    away = getattr(market, "away", None)
    slug = getattr(market, "event_slug", None)
    if str(getattr(market, "kind", "")) in GAME_KINDS or (home and away):
        if slug:
            return f"event:{slug}"
        if home and away:
            return "teams:" + "|".join(sorted((canonical(home), canonical(away))))
    # Positions inside one mutually exclusive futures event (division, conference, Super Bowl,
    # top seed) are one correlated bet: NO on several teams is YES on the rest. Cap them jointly.
    if str(getattr(market, "kind", "")) == "futures" and slug:
        if market_category(market) in EXCLUSIVE_FUTURES:
            return f"event:{slug}"
    return f"market:{market_id}"


_DIVISION_RE = re.compile(r"\b(afc|nfc)\s+(east|north|south|west)\b", re.IGNORECASE)
_TOP_SEED_RE = re.compile(
    r"((?:\bno\.?|\bnumber|#)\s*1|\bfirst|\btop|\b1st)\s+(?:overall\s+)?seed\b", re.IGNORECASE
)
_CONFERENCE_RE = re.compile(
    r"\b(win|wins|winning)\s+the\s+(afc|nfc)\b(?!\s+(east|north|south|west))"
    r"|\b(afc|nfc)\s+champion(ship)?s?\b|\bconference\b",
    re.IGNORECASE,
)

# "Win the Super Bowl" is a league-wide exclusive market; "make / reach / play in the Super Bowl"
# is won by two teams every season (one per conference) and is therefore a *conference* market.
_WIN_SB_RE = re.compile(r"\b(win|wins|winning|won)\b(?:\s+\S+){0,3}?\s+super bowl\b", re.IGNORECASE)
_REACH_SB_RE = re.compile(
    r"\b(make|makes|making|reach|reaches|reaching|advance|advances|advancing|play|plays|playing|"
    r"get|gets|getting|go|goes|going|appear|appears|appearing|qualify|qualifies|qualifying|"
    r"compete|competes|competing|be in)\b[^?]*\bsuper bowl\b",
    re.IGNORECASE,
)

# Futures categories whose per-team markets are mutually exclusive (exactly one team wins) --
# within the right scope: one division, one conference (also for the #1 seed), or the league.
# "playoffs" (14 teams qualify) and win totals (every team can hit its own number) are not.
EXCLUSIVE_FUTURES = frozenset({"super_bowl", "conference", "division", "top_seed"})

# ``PolyMarket.futures_type`` vocabulary (nfl_edge.markets.polymarket.FUTURES_TYPES) -> ours.
_FUTURES_TYPE_MAP: dict[str, str | None] = {
    "super_bowl": "super_bowl",
    "conference": "conference",
    "division": "division",
    "playoffs": "playoffs",
    "top_seed": "top_seed",
    "win_total": None,
}


def futures_category(question: str) -> str | None:
    """Classify a futures question from its text.

    Returns 'super_bowl' | 'top_seed' | 'division' | 'conference' | 'playoffs', or None for
    anything else (win totals, awards, ...). "Make / reach / play in the Super Bowl" is a
    *conference* market (two teams get there every season); only "win the Super Bowl" (or a
    Super Bowl "champion") is ``super_bowl``.
    """
    q = re.sub(r"\s+", " ", str(question or "")).lower()
    if "super bowl" in q:
        if _WIN_SB_RE.search(q) or "champion" in q:
            return "super_bowl"
        if _REACH_SB_RE.search(q):
            return "conference"
        return "super_bowl"
    if _TOP_SEED_RE.search(q):
        return "top_seed"
    if _DIVISION_RE.search(q):
        return "division"
    if _CONFERENCE_RE.search(q):
        return "conference"
    if "playoff" in q:
        return "playoffs"
    return None


def market_category(market: "PolyMarket") -> str | None:
    """Futures category of a market.

    Prefers the parser's ``market.futures_type`` (set by ``nfl_edge.markets.polymarket``; a
    ``win_total`` maps to None) and falls back to :func:`futures_category` on the question text
    for objects without that attribute or with an unknown value.
    """
    ft = getattr(market, "futures_type", None)
    if ft is not None:
        key = str(ft).strip().lower()
        if key in _FUTURES_TYPE_MAP:
            return _FUTURES_TYPE_MAP[key]
    return futures_category(getattr(market, "question", ""))


def _exclusive_scope(team_codes: Iterable[str], category: str | None) -> bool:
    """True when ``category`` markets on these teams are mutually exclusive (at most one YES pays).

    Division winners must all belong to one division; conference champions and #1 seeds to one
    conference; Super Bowl winners are exclusive league-wide. Unknown team codes -> False.
    """
    s = {canonical(t) for t in team_codes}
    if not s or any(t not in DIVISIONS for t in s):
        return False
    if category == "division":
        return len({DIVISIONS[t] for t in s}) == 1
    if category in ("conference", "top_seed"):
        return len({DIVISIONS[t][0] for t in s}) == 1
    return category == "super_bowl"


def _event_complete(team_codes: Iterable[str], category: str | None = None) -> bool:
    """True when the team set is exhaustive for ``category`` (exactly one YES *must* pay).

    ``division`` -> the four members of one division; ``conference`` / ``top_seed`` -> the sixteen
    members of one conference; ``super_bowl`` -> all 32 teams. ``category=None`` accepts any of
    those shapes. Sixteen AFC *division* markets are not complete (four divisions => four YES).
    """
    s = {canonical(t) for t in team_codes}
    if not s or any(t not in DIVISIONS for t in s):
        return False
    is_division = len(s) == 4 and len({DIVISIONS[t] for t in s}) == 1
    is_conference = len(s) == 16 and len({DIVISIONS[t][0] for t in s}) == 1
    is_league = s == set(CURRENT_TEAMS)
    if category == "division":
        return is_division
    if category in ("conference", "top_seed"):
        return is_conference
    if category == "super_bowl":
        return is_league
    if category is None:
        return is_division or is_conference or is_league
    return False


# ------------------------------------------------------------------ edge scan
def find_edges(
    markets: Sequence["PolyMarket"],
    fair_yes: Mapping[str, float],
    cfg: StrategyConfig = DEFAULT_STRATEGY,
    bankroll: float = 1000.0,
    game_ids: Mapping[str, str] | None = None,
    fees: Mapping[str, float] | None = None,
) -> list[Opportunity]:
    """Scan markets for buyable YES/NO sides whose fair probability beats the executable price.

    Parameters
    ----------
    markets : PolyMarket objects (only ``market_id, question, kind, outcomes, token_ids, prices,
        liquidity, home, away, team, yes_index, book, event_slug`` are read).
    fair_yes : ``market_id -> P(YES outcome)`` from the model. Markets without an entry (or with a
        NaN entry) are skipped; a value outside [0, 1] raises ``ValueError``.
    cfg : thresholds and caps (:class:`~nfl_edge.config.StrategyConfig`).
    bankroll : USD bankroll used to convert stake fractions into ``stake_usd``.
    game_ids : optional ``market_id -> game_id``. Markets sharing an ``event_slug`` with a mapped
        market inherit its game id. Without a mapping, game markets are grouped by event slug /
        team pair anyway (see ``exposure_key``), so the cap holds with the plain signature.
    fees : optional ``market_id -> fee rate`` overriding ``cfg.fee_rate``.

    For every market: YES is priced at the best ask, NO at ``1 - best_bid`` (binary markets only;
    multi-outcome markets get a YES evaluation only). Without a book, ``prices[yes_index]`` stands
    in for both quotes and ``market.liquidity`` for depth; ``reason`` records that. A side is kept
    when its fair probability, its price and its effective price all lie in
    ``[cfg.min_prob, cfg.max_prob]``, the USD a buyer of that side can deploy within the slippage
    buffer (YES: ask notional; NO: ``(1 - bid) * size`` over the YES bids) is at least
    ``cfg.min_liquidity_usd`` and ``edge >= cfg.min_edge``. Every opportunity carries an
    ``exposure_key`` (game id, else event slug, else team pair, else market) so all markets of
    one game share ``cfg.max_game_exposure`` even when ``game_ids`` is not supplied. Results are
    sorted by edge descending and sized with :func:`~nfl_edge.strategy.kelly.size_positions`,
    which also caps every stake at its ``liquidity_usd``.
    """
    game_ids = {str(k): str(v) for k, v in (game_ids or {}).items() if v is not None}
    fees = fees or {}
    # A market the caller mapped to a game lends that game_id to its event-mates (a prop, or a
    # spread/total ``match_game_markets`` did not match) so one game is one exposure group.
    slug_game: dict[str, str] = {}
    for market in markets:
        gid = game_ids.get(str(market.market_id))
        slug = getattr(market, "event_slug", None)
        if gid is not None and slug:
            slug_game.setdefault(str(slug), gid)

    candidates: list[Opportunity] = []
    for market in markets:
        mid = str(market.market_id)
        if mid not in fair_yes:
            continue
        fair = float(fair_yes[mid])
        if math.isnan(fair):
            continue  # NaN = the model has no prediction for this market
        if not (0.0 <= fair <= 1.0):
            raise ValueError(f"fair_yes[{mid!r}] must be a probability in [0, 1], got {fair}")
        fee = float(fees.get(mid, cfg.fee_rate))
        bid, ask, has_book = _quotes(market)
        if bid is None and ask is None:
            continue

        sides: list[tuple[str, float | None, float]] = [("YES", ask, fair)]
        if _is_binary(market):
            sides.append(("NO", None if bid is None else 1.0 - bid, 1.0 - fair))

        teams = _market_teams(market)
        game_id = game_ids.get(mid)
        if game_id is None:
            slug = getattr(market, "event_slug", None)
            game_id = slug_game.get(str(slug)) if slug else None
        exposure_key = _exposure_key(market, mid, game_id)
        for side, price, fair_side in sides:
            if price is None or not _is_valid_price(price):
                continue
            if not (cfg.min_prob <= fair_side <= cfg.max_prob):
                continue
            eff = effective_price(price, cfg)
            # Price band: the config's "never buy below 5c or above 95c" is a rule on what we pay,
            # not only on what the model thinks -- a 3c NO share stays a longshot trap even when
            # the model gives it 6%.
            if not (cfg.min_prob <= price <= cfg.max_prob and cfg.min_prob <= eff <= cfg.max_prob):
                continue
            if has_book:
                liquidity = _side_depth_usd(market.book, side, cfg.slippage_buffer)
                reason = "book"
            else:
                liquidity = float(market.liquidity or 0.0)
                reason = "no book: last price used as bid and ask"
            if math.isnan(liquidity) or liquidity < cfg.min_liquidity_usd:
                continue
            be = breakeven_prob(eff, fee)
            edge = fair_side - be
            if edge < cfg.min_edge:
                continue
            full_kelly = kelly_share(fair_side, eff, fee)
            candidates.append(
                Opportunity(
                    market_id=mid,
                    question=str(market.question),
                    kind=str(market.kind),
                    side=side,
                    token_id=_token_for_side(market, side),
                    fair_prob=fair_side,
                    price=float(price),
                    effective_price=eff,
                    edge=edge,
                    ev=expected_value(fair_side, eff, fee),
                    kelly=full_kelly,
                    stake_fraction=fractional_kelly(
                        fair_side, eff, cfg.kelly_fraction, cfg.max_stake_fraction, fee
                    ),
                    stake_usd=0.0,
                    liquidity_usd=liquidity,
                    game_id=game_id,
                    teams=teams,
                    reason=reason,
                    breakeven=be,
                    fee=fee,
                    event_slug=market.event_slug,
                    exposure_key=exposure_key,
                )
            )
    return size_positions(candidates, bankroll, cfg)


# ------------------------------------------------------------------ arbitrage
def _intra_market_arb(
    market: "PolyMarket",
    books: Mapping[str, "OrderBook"],
    epsilon: float,
    fee: float,
) -> dict[str, Any] | None:
    tokens = list(market.token_ids or [])
    idx = int(market.yes_index)
    yes_token = str(tokens[idx]) if 0 <= idx < len(tokens) else ""
    no_token = str(tokens[1 - idx]) if len(tokens) == 2 else ""

    yes_ask: float | None
    no_ask: float | None
    source: str
    if yes_token and no_token and yes_token in books and no_token in books:
        ya, na = books[yes_token].best_ask, books[no_token].best_ask
        yes_ask = float(ya) if ya is not None and _is_valid_price(ya) else None
        no_ask = float(na) if na is not None and _is_valid_price(na) else None
        source = "two_books"
    else:
        bid, ask, has_book = _quotes(market)
        if not has_book:
            return None
        yes_ask = ask
        no_ask = None if bid is None else 1.0 - bid
        source = "yes_book"
    if yes_ask is None or no_ask is None:
        return None
    cost = yes_ask + no_ask
    if cost >= 1.0 - epsilon:
        return None
    # Fee is charged on the winning share's profit: payout = 1 - fee * (1 - price_of_winner).
    guaranteed = min(1.0 - fee * (1.0 - yes_ask), 1.0 - fee * (1.0 - no_ask))
    profit = guaranteed - cost
    if profit <= 0.0:
        return None
    return {
        "type": "intra_market",
        "event_slug": market.event_slug,
        "market_id": str(market.market_id),
        "question": str(market.question),
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "source": source,
        "cost": cost,
        "guaranteed_payout": guaranteed,
        "profit_per_usd": profit / cost,
    }


def find_arbitrage(
    markets: Sequence["PolyMarket"],
    books: Mapping[str, "OrderBook"] | None = None,
    epsilon: float = 0.0,
    fee: float = 0.0,
) -> list[dict[str, Any]]:
    """Detect risk-free price inconsistencies.

    * ``intra_market``: ``YES ask + NO ask < 1 - epsilon``. NO ask is ``1 - YES best bid`` from the
      market's YES book; when ``books`` (``token_id -> OrderBook``) holds both tokens' books, each
      side's own ask is used instead.
    * ``event_buy_all``: for an event whose markets are one Yes/No futures market per distinct
      team and mutually exclusive *in scope* -- one division's winner, one conference's champion
      or #1 seed, or the Super Bowl winner (category from ``market.futures_type`` when present,
      else the question text; "make the Super Bowl" counts as conference) -- buying one YES of
      each costs ``sum(asks)``; when the team set is exhaustive for that scope (the 4 division
      members, the 16 conference members, all 32 teams) exactly one pays $1, so
      ``sum(asks) < 1`` is a locked profit.
    * ``event_sell_all``: ``sum(YES bids) > 1``: buying NO on every market (cost ``n - sum(bids)``)
      pays at least ``n - 1`` because at most one YES can win. Needs only in-scope exclusivity;
      a slug mixing two divisions or both conferences is skipped (two YES could pay).

    ``fee`` (taker fee on winnings) lowers the guaranteed payout. Each dict reports type, ids,
    prices, cost and ``profit_per_usd`` (guaranteed profit per $1 deployed).
    """
    books = books or {}
    out: list[dict[str, Any]] = []
    for market in markets:
        arb = _intra_market_arb(market, books, epsilon, fee)
        if arb is not None:
            out.append(arb)

    groups: dict[str, list["PolyMarket"]] = {}
    for market in markets:
        slug = market.event_slug
        if not slug or str(market.kind) != "futures" or not _is_binary(market):
            continue
        groups.setdefault(str(slug), []).append(market)

    for slug, group in groups.items():
        teams = [canonical(m.team) for m in group if m.team]
        if len(group) < 2 or len(teams) != len(group) or len(set(teams)) != len(teams):
            continue  # not one-market-per-team => cannot assume mutual exclusivity
        categories = {market_category(m) for m in group}
        if len(categories) != 1:
            continue  # mixed events are not mutually exclusive
        category = next(iter(categories))
        if category not in EXCLUSIVE_FUTURES or not _exclusive_scope(teams, category):
            # playoffs / win totals, or e.g. two divisions' winners (or both conferences'
            # champions / #1 seeds) under one slug: more than one YES can pay, so neither
            # buy-all nor sell-all is risk-free.
            continue
        quotes = [_quotes(m) for m in group]
        asks = [q[1] for q in quotes]
        bids = [q[0] for q in quotes]
        ids = [str(m.market_id) for m in group]
        n = len(group)
        complete = _event_complete(teams, category)

        if complete and all(a is not None for a in asks):
            ask_vals = [float(a) for a in asks if a is not None]
            cost = sum(ask_vals)
            if cost < 1.0 - epsilon:
                guaranteed = 1.0 - fee * (1.0 - min(ask_vals))
                profit = guaranteed - cost
                if profit > 0.0:
                    out.append(
                        {
                            "type": "event_buy_all",
                            "event_slug": slug,
                            "market_ids": ids,
                            "teams": teams,
                            "category": category,
                            "n_markets": n,
                            "complete": True,
                            "sum_asks": cost,
                            "cost": cost,
                            "guaranteed_payout": guaranteed,
                            "profit_per_usd": profit / cost,
                        }
                    )
        if all(b is not None for b in bids):
            bid_vals = [float(b) for b in bids if b is not None]
            sum_bids = sum(bid_vals)
            if sum_bids > 1.0 + epsilon:
                cost = n - sum_bids  # buy NO on every market at 1 - bid
                # Every non-winning NO pays 1 with fee on its (1 - no_price) = bid profit.
                guaranteed = (n - 1) - fee * (sum_bids - min(bid_vals))
                profit = guaranteed - cost
                if profit > 0.0 and cost > 0.0:
                    out.append(
                        {
                            "type": "event_sell_all",
                            "event_slug": slug,
                            "market_ids": ids,
                            "teams": teams,
                            "category": category,
                            "n_markets": n,
                            "complete": complete,
                            "sum_bids": sum_bids,
                            "cost": cost,
                            "guaranteed_payout": guaranteed,
                            "profit_per_usd": profit / cost,
                        }
                    )
    return out


# ------------------------------------------------------------------ consistency
_SIM_COLUMNS = {
    "super_bowl": "p_super_bowl",
    "conference": "p_conference",
    "playoffs": "p_playoffs",
    "division": "p_division",
}


def _sim_by_team(sim: pd.DataFrame) -> dict[str, dict[str, float]]:
    if "team" in sim.columns:
        keyed = sim.set_index("team")
    else:
        keyed = sim
    out: dict[str, dict[str, float]] = {}
    for team, row in keyed.iterrows():
        code = canonical(str(team))
        out[code] = {
            cat: float(row[col]) for cat, col in _SIM_COLUMNS.items() if col in keyed.columns
        }
    return out


def consistency_checks(
    markets: Sequence["PolyMarket"],
    sim: pd.DataFrame | None = None,
    tol: float = 0.0,
    divergence_tol: float = 0.15,
) -> list[dict[str, Any]]:
    """Flag futures prices (and, optionally, simulation output) that violate logical constraints.

    Market checks (mids of futures markets, classified by :func:`futures_category`):

    * ``ordering``: per team, ``P(Super Bowl) <= P(conference) <= P(playoffs)`` and
      ``P(division) <= P(playoffs)``; a violation larger than ``tol`` is flagged.
    * ``division_sum``: per division event (grouped by ``event_slug``, all four members present),
      the YES mids must sum to within ``DIVISION_SUM_RANGE`` = [0.90, 1.15].

    Simulation checks when ``sim`` is given (per-team rows with ``p_super_bowl``, ``p_conference``,
    ``p_playoffs``, ``p_division``; team codes in a ``team`` column or the index):

    * ``sim_ordering``: the same ordering constraints on the simulated probabilities.
    * ``sim_division_sum``: simulated ``p_division`` must sum to ~1 per division.
    * ``divergence``: ``|sim - market mid| > divergence_tol`` for the same team/category.

    Returns a list of dicts with ``check`` and the offending values; empty means consistent.
    """
    flags: list[dict[str, Any]] = []
    by_team: dict[str, dict[str, tuple[float, str]]] = {}
    division_groups: dict[str, list[tuple[str, str, float]]] = {}

    for market in markets:
        if str(market.kind) != "futures" or not market.team:
            continue
        cat = market_category(market)
        if cat is None:
            continue
        mid = market_mid(market)
        if mid is None:
            continue
        team = canonical(market.team)
        by_team.setdefault(team, {})[cat] = (mid, str(market.market_id))
        if cat == "division":
            slug = str(market.event_slug or f"division:{' '.join(DIVISIONS.get(team, ('?', '?')))}")
            division_groups.setdefault(slug, []).append((team, str(market.market_id), mid))

    ordering_pairs = (("super_bowl", "conference"), ("conference", "playoffs"), ("division", "playoffs"))
    for team in sorted(by_team):
        cats = by_team[team]
        for lower, upper in ordering_pairs:
            if lower in cats and upper in cats and cats[lower][0] > cats[upper][0] + tol:
                flags.append(
                    {
                        "check": "ordering",
                        "team": team,
                        "lower": lower,
                        "upper": upper,
                        "lower_mid": cats[lower][0],
                        "upper_mid": cats[upper][0],
                        "gap": cats[lower][0] - cats[upper][0],
                        "market_ids": [cats[lower][1], cats[upper][1]],
                    }
                )

    lo, hi = DIVISION_SUM_RANGE
    for slug in sorted(division_groups):
        rows = division_groups[slug]
        teams = [r[0] for r in rows]
        if len(set(teams)) != 4 or any(t not in DIVISIONS for t in teams) or len({DIVISIONS[t] for t in teams}) != 1:
            continue
        total = sum(r[2] for r in rows)
        if not (lo <= total <= hi):
            flags.append(
                {
                    "check": "division_sum",
                    "event_slug": slug,
                    "division": " ".join(DIVISIONS[teams[0]]),
                    "teams": teams,
                    "market_ids": [r[1] for r in rows],
                    "sum_mids": total,
                    "range": DIVISION_SUM_RANGE,
                }
            )

    if sim is not None and len(sim):
        sim_teams = _sim_by_team(sim)
        for team in sorted(sim_teams):
            probs = sim_teams[team]
            for lower, upper in ordering_pairs:
                if lower in probs and upper in probs and probs[lower] > probs[upper] + tol:
                    flags.append(
                        {
                            "check": "sim_ordering",
                            "team": team,
                            "lower": lower,
                            "upper": upper,
                            "lower_p": probs[lower],
                            "upper_p": probs[upper],
                            "gap": probs[lower] - probs[upper],
                        }
                    )
            for cat, (mid, market_id) in by_team.get(team, {}).items():
                if cat in probs and abs(probs[cat] - mid) > divergence_tol:
                    flags.append(
                        {
                            "check": "divergence",
                            "team": team,
                            "category": cat,
                            "market_id": market_id,
                            "market_mid": mid,
                            "sim_p": probs[cat],
                            "gap": probs[cat] - mid,
                        }
                    )
        div_sums: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for team, probs in sim_teams.items():
            if "division" in probs and team in DIVISIONS:
                div_sums.setdefault(DIVISIONS[team], []).append((team, probs["division"]))
        for key in sorted(div_sums):
            members = div_sums[key]
            if len(members) != 4:
                continue
            total = sum(p for _, p in members)
            if abs(total - 1.0) > 0.02:
                flags.append(
                    {
                        "check": "sim_division_sum",
                        "division": " ".join(key),
                        "teams": [t for t, _ in members],
                        "sum_p": total,
                    }
                )
    return flags


# ------------------------------------------------------------------ reporting
def opportunities_table(opps: Iterable[Opportunity]) -> pd.DataFrame:
    """Tidy DataFrame of opportunities (columns ``OPPORTUNITY_COLUMNS``; ``teams`` joined by '/')."""
    rows = []
    for o in opps:
        rows.append(
            {
                "market_id": o.market_id,
                "question": o.question,
                "kind": o.kind,
                "side": o.side,
                "token_id": o.token_id,
                "teams": "/".join(o.teams),
                "game_id": o.game_id,
                "event_slug": o.event_slug,
                "exposure_key": o.exposure_key,
                "fair_prob": o.fair_prob,
                "price": o.price,
                "effective_price": o.effective_price,
                "breakeven": o.breakeven,
                "edge": o.edge,
                "ev": o.ev,
                "kelly": o.kelly,
                "stake_fraction": o.stake_fraction,
                "stake_usd": o.stake_usd,
                "liquidity_usd": o.liquidity_usd,
                "fee": o.fee,
                "reason": o.reason,
            }
        )
    df = pd.DataFrame(rows, columns=OPPORTUNITY_COLUMNS)
    if len(df):
        df = df.sort_values(["edge", "liquidity_usd"], ascending=[False, False]).reset_index(drop=True)
    return df
