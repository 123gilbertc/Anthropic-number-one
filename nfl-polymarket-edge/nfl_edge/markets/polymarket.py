"""Polymarket access: Gamma (event/market metadata) + CLOB (order books, price history).

Everything in this module is either pure parsing (``parse_market``, ``match_game_markets``,
``fee_for_market``, ``snapshot``/``load_snapshots``) or goes through ``PolymarketClient``,
which can be pointed at a directory of JSON fixtures instead of the network. Tests and
offline development use the fixture path; production uses a ``requests.Session`` with
timeouts and bounded exponential-backoff retries on 429/5xx.

Conventions
-----------
* A Polymarket share price in (0, 1) *is* the implied probability of that outcome.
* ``PolyMarket.yes_index`` is the outcome/token that a strategy-layer ``fair_prob`` refers
  to: the **home** team's outcome when an outcome label names a team (moneyline, spread),
  ``Over`` for totals, and the literal ``Yes`` outcome otherwise (Yes/No futures, and Yes/No
  game questions such as "Will the Chiefs beat the Dolphins?"). ``PolyMarket.yes_team`` is the
  team whose win/cover resolves that outcome: the outcome label when it names a team, else the
  subject of the question, else ``None`` (the home team is never assumed). The labels ``Yes``
  and ``No`` are never read as team names ("No" is not the Saints).
* Polymarket game-event slugs are ``nfl-<away>-<home>-<YYYY-MM-DD>`` (away listed first);
  titles are ``"Away vs. Home"``. Home/away derived from the slug are flagged
  ``home_away_confident=True``; a title-only derivation, or team-labelled outcomes that do not
  include the derived home team, is flagged ``False``.
* Spread lines are kept raw in ``PolyMarket.line`` as Gamma serves them. The sign convention of
  that field (relative to the first outcome? the home team? the favourite?) is unverified and is
  never used to pick a side. ``spread_line_home`` takes the side from a ``<Team> (-x)`` /
  ``<Team> -x`` quote in the question, ``groupItemTitle`` or the outcome labels and returns the
  nflverse ``spread_line`` convention (expected home - away margin) in the *market's* home frame;
  it is NaN when no single-team quote exists or the quotes disagree. ``match_game_markets``
  re-expresses it in the schedule's home frame as ``line_home`` and keeps the market-frame value
  as ``line_market_home``.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
import requests

from .. import config
from ..teams import normalize_team_name, teams_in_text

logger = logging.getLogger(__name__)

__all__ = [
    "OrderBook",
    "PolyMarket",
    "PolymarketClient",
    "PolymarketError",
    "parse_market",
    "classify_kind",
    "match_game_markets",
    "fee_for_market",
    "snapshot",
    "load_snapshots",
    "MATCH_COLUMNS",
    "SNAPSHOT_COLUMNS",
    "KINDS",
    "FUTURES_TYPES",
]

KINDS: tuple[str, ...] = ("moneyline", "spread", "total", "futures", "other")
FUTURES_TYPES: tuple[str, ...] = ("super_bowl", "conference", "division", "playoffs", "win_total", "top_seed")

_EVENT_SLUG_RE = re.compile(r"^nfl-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})")
_GAME_SEPARATOR_RE = re.compile(r"\bvs\.?\b|\bversus\b|\bv\.?\b|@|\bat\b|\bbeat\b|\bdefeats?\b|\bover\b")
_GAME_PROP_RE = re.compile(
    r"overtime|\bot\b|first (?:to )?score|touchdown|yards|\bpoints?\b|margin|both teams|player|prop|"
    r"\btie\b|safety|field goal|halftime|quarter|\bhalf\b|score first|shutout|receptions|rushing|passing"
)
_SPREAD_IN_QUESTION_RE = re.compile(r"\bspread\b|\bcover\b|\(\s*[+-]\d+(?:\.\d+)?\s*\)")
_TOTAL_IN_QUESTION_RE = re.compile(r"\bo/u\b|over/under|total points|\bover\s+\d+(?:\.\d+)?\b|\btotal\b")
_DIVISION_RE = re.compile(r"\b(afc|nfc)\s+(east|north|south|west)\b|\bdivision\b")
_CONFERENCE_RE = re.compile(
    r"\b(afc|nfc)\s+(championship|champion|title|conference)\b|\bwin the (afc|nfc)\b|"
    r"\b(make|reach|advance to|play in|get to)\b[^?]*\bsuper bowl\b|\bconference champion"
)
_SUPER_BOWL_RE = re.compile(r"\bsuper bowl\b|\bnfl champion(?:ship)?\b")
_PLAYOFFS_RE = re.compile(r"\bplayoffs?\b|\bpostseason\b")
_WIN_TOTAL_RE = re.compile(
    r"\bwin\b[^?]*?\b\d+(?:\.\d+)?\+?\s*(?:or more |or fewer |regular[- ]season )?(?:games|wins)\b|"
    r"\bwin total\b|\bregular[- ]season wins\b|\b(?:more|fewer|less) than\s+\d+(?:\.\d+)?\s+(?:games|wins)\b"
)
_TOP_SEED_RE = re.compile(r"(?:#\s*1|no\.?\s*1|number one|top|first)\s+seed|\bbye\b")
_PLAYOFF_WINS_RE = re.compile(r"\b\d+(?:\.\d+)?\+?\s*(?:or more\s+)?(?:playoff|postseason)\s+(?:games?|wins?)\b"
                              r"|\b(?:playoff|postseason)\s+(?:games?|wins?)\b|\bwin\b[^?]*\bin the (?:playoffs|postseason)\b")
# A spread quote number: '-10.5' / '(+3)'. The sign must follow whitespace or '(' so that '2026-27' is
# not read as a quote of -27. Which team owns the number is decided by ``_spread_quotes``.
_SIGNED_NUMBER_RE = re.compile(r"(?:(?<=\s)|(?<=\())(?P<num>[+-]\d+(?:\.\d+)?)(?=$|[\s).])")
_CLAUSE_SPLIT_RE = re.compile(r"[:;,?]")
_YES_NO_LABELS = frozenset({"yes", "no"})
_YES_SUBJECT_VERB_RE = re.compile(r"\b(beat|beats|defeat|defeats|cover|covers|win|wins|lose|loses|fall|falls)\b")

MATCH_COLUMNS: list[str] = [
    "market_id", "game_id", "home_team_c", "away_team_c", "kind", "yes_team", "price_yes",
    "condition_id", "question", "event_slug", "token_id_yes", "yes_index", "yes_outcome", "line", "line_home",
    "line_market_home", "market_home", "market_away", "home_matches_schedule", "home_away_confident", "market_date", "game_date",
    "date_diff_days", "season", "week", "game_type", "spread_line", "total_line", "market_prob", "played",
    "liquidity", "volume", "end_date", "best_bid", "best_ask", "mid", "active", "closed",
]
SNAPSHOT_COLUMNS: list[str] = [
    "ts", "market_id", "condition_id", "question", "kind", "event_slug", "token_id", "outcome", "outcome_index",
    "is_yes", "price", "best_bid", "best_ask", "mid", "spread", "liquidity", "volume", "end_date",
    "home", "away", "team", "line",
]


class PolymarketError(RuntimeError):
    """Raised when a Polymarket request fails after retries or returns an unusable payload."""


# ----------------------------------------------------------------------------------------- helpers
def _to_float(value: Any, default: float = math.nan) -> float:
    """Coerce Gamma/CLOB numbers (often strings, sometimes None) to float; ``default`` if unusable."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else default
    s = str(value).strip().replace(",", "")
    if not s:
        return default
    try:
        f = float(s)
    except ValueError:
        return default
    return f if math.isfinite(f) else default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f", ""):
        return False
    return default


def _as_list(value: Any) -> list | None:
    """Gamma encodes list fields as JSON strings ('["A", "B"]'); accept those or real lists."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return None
        return list(parsed) if isinstance(parsed, (list, tuple)) else None
    return None


def _first_present(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            return d[k]
    return None


def _parse_ts(value: Any) -> pd.Timestamp:
    """ISO-8601 / epoch to a tz-naive UTC Timestamp (NaT when missing or unparsable)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return pd.NaT
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return pd.NaT
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------- OrderBook
@dataclass
class OrderBook:
    """A CLOB order book for one outcome token. Levels are ``(price, size)`` sorted best-first.

    ``size`` is in shares (each pays $1 if the outcome wins), so USD notional at a level is
    ``price * size``. Bids are sorted by descending price, asks by ascending price, regardless
    of the order the API returned them in.
    """

    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    token_id: str | None = None
    timestamp: int | None = None

    def __post_init__(self) -> None:
        self.bids = sorted(self._clean(self.bids), key=lambda lv: -lv[0])
        self.asks = sorted(self._clean(self.asks), key=lambda lv: lv[0])

    @staticmethod
    def _clean(levels: Iterable[Any]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for lv in levels:
            if isinstance(lv, dict):
                price, size = _to_float(lv.get("price")), _to_float(lv.get("size"))
            else:
                price, size = _to_float(lv[0]), _to_float(lv[1])
            if math.isnan(price) or math.isnan(size) or size <= 0 or not (0.0 <= price <= 1.0):
                continue
            out.append((price, size))
        return out

    @classmethod
    def from_clob(cls, payload: dict) -> "OrderBook":
        """Build from a ``GET /book`` payload ({bids:[{price,size}], asks:[...], asset_id, timestamp})."""
        ts_f = _to_float(payload.get("timestamp"))
        ts = None if math.isnan(ts_f) else int(ts_f)
        return cls(
            bids=list(payload.get("bids") or []),
            asks=list(payload.get("asks") or []),
            token_id=str(payload["asset_id"]) if payload.get("asset_id") is not None else None,
            timestamp=ts,
        )

    @property
    def is_empty(self) -> bool:
        return not self.bids and not self.asks

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def _side(self, side: str) -> list[tuple[float, float]]:
        s = side.lower()
        if s in ("bid", "bids", "buy"):
            return self.bids
        if s in ("ask", "asks", "sell"):
            return self.asks
        raise ValueError(f"side must be 'bid' or 'ask', got {side!r}")

    def depth_usd(self, side: str, max_price_impact: float = 0.02) -> float:
        """USD notional resting on ``side`` within ``max_price_impact`` (absolute price) of the touch.

        For asks this is what you could buy without paying more than ``best_ask + impact``;
        for bids what you could sell without receiving less than ``best_bid - impact``.
        """
        if max_price_impact < 0:
            raise ValueError("max_price_impact must be >= 0")
        levels = self._side(side)
        if not levels:
            return 0.0
        best = levels[0][0]
        if levels is self.asks:
            limit = best + max_price_impact + 1e-12
            return float(sum(p * s for p, s in levels if p <= limit))
        limit = best - max_price_impact - 1e-12
        return float(sum(p * s for p, s in levels if p >= limit))

    def size_usd_at_touch(self, side: str) -> float:
        """USD notional at the best level only."""
        levels = self._side(side)
        return float(levels[0][0] * levels[0][1]) if levels else 0.0

    def to_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "timestamp": self.timestamp,
            "bids": [list(lv) for lv in self.bids],
            "asks": [list(lv) for lv in self.asks],
        }


# --------------------------------------------------------------------------------------- PolyMarket
@dataclass
class PolyMarket:
    """A parsed Gamma market with the fields the strategy layer needs."""

    market_id: str
    condition_id: str
    question: str
    slug: str
    event_slug: str
    event_title: str
    outcomes: list[str]
    token_ids: list[str]
    prices: list[float]
    end_date: str | None
    liquidity: float
    volume: float
    active: bool
    closed: bool
    kind: str                      # "moneyline" | "spread" | "total" | "futures" | "other"
    home: str | None               # canonical code (game markets)
    away: str | None
    team: str | None               # canonical code (futures / single-team markets)
    line: float | None             # raw Polymarket line (spread as quoted, total, win-total threshold)
    yes_index: int                 # outcome/token index that fair_prob refers to
    book: OrderBook | None = None
    raw: dict = field(default_factory=dict)
    home_away_confident: bool = True   # False when home/away were inferred from title order only
    futures_type: str | None = None    # one of FUTURES_TYPES for kind == "futures"
    event_id: str | None = None
    start_date: str | None = None      # event startDate (kickoff for game events)

    # ------------------------------------------------------------------ convenience
    @property
    def yes_token(self) -> str:
        return self.token_ids[self.yes_index]

    @property
    def yes_outcome(self) -> str:
        return self.outcomes[self.yes_index]

    @property
    def price_yes(self) -> float:
        return self.prices[self.yes_index] if self.yes_index < len(self.prices) else math.nan

    @property
    def no_index(self) -> int | None:
        """The complementary outcome index for binary markets (None otherwise)."""
        return 1 - self.yes_index if len(self.outcomes) == 2 else None

    @property
    def is_game_market(self) -> bool:
        return self.kind in ("moneyline", "spread", "total")

    @property
    def teams(self) -> list[str]:
        return [t for t in (self.home, self.away, self.team) if t]

    @property
    def yes_team(self) -> str | None:
        """Canonical team whose win/cover resolves the YES outcome (None for totals / non-team markets).

        For moneyline/spread markets this is the team named by the YES outcome label; when that
        label is a bare Yes/No it is the subject of the question ("Will the Chiefs beat the
        Dolphins?" -> KC, "Will the Dolphins lose to the Chiefs?" -> KC). ``None`` when neither
        resolves it; the home team is never assumed.
        """
        if self.kind == "total":
            return None
        if self.kind in ("moneyline", "spread"):
            label = self.outcomes[self.yes_index] if self.yes_index < len(self.outcomes) else None
            code = _outcome_team(label) if label is not None else None
            if code is not None:
                return code
            return _yes_team_from_question(self.question, self.home, self.away)
        return self.team

    @property
    def market_date(self) -> pd.Timestamp:
        """Best-effort game/event date (tz-naive UTC): slug date, else event startDate, else endDate."""
        m = _EVENT_SLUG_RE.match(self.event_slug or "")
        if m:
            return _parse_ts(m.group(3))
        ts = _parse_ts(self.start_date)
        if not pd.isna(ts):
            return ts
        return _parse_ts(self.end_date)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("book", "raw")}
        d["book"] = self.book.to_dict() if self.book is not None else None
        return d


# ------------------------------------------------------------------------------- classification
def classify_kind(question: str, sports_market_type: str | None = None,
                  event_title: str | None = None) -> tuple[str, str | None]:
    """Return ``(kind, futures_type)`` for a market.

    Priority: Gamma's ``sportsMarketType`` when present, else question text; the event title is
    consulted only when the question alone is uninformative (e.g. bare team-name questions).
    """
    smt = (sports_market_type or "").strip().lower()
    if smt in ("moneyline", "money_line", "ml", "h2h"):
        return "moneyline", None
    if smt in ("spreads", "spread", "handicap"):
        return "spread", None
    if smt in ("totals", "total", "over_under", "ou"):
        return "total", None

    q = re.sub(r"\s+", " ", str(question or "")).strip().lower()
    q_teams = teams_in_text(q)

    fut = _futures_type(q)
    if fut is not None:
        return "futures", fut
    if _SPREAD_IN_QUESTION_RE.search(q) and len(q_teams) >= 1:
        return "spread", None
    if _TOTAL_IN_QUESTION_RE.search(q) and (len(q_teams) == 2 or re.search(r"\bo/u\b|over/under", q)):
        return "total", None
    if len(q_teams) == 2 and _GAME_SEPARATOR_RE.search(q) and not _GAME_PROP_RE.search(q):
        return "moneyline", None
    # Bare questions ("Kansas City Chiefs") lean on the event title.
    if event_title and not _GAME_SEPARATOR_RE.search(q) and re.fullmatch(r"(will the )?[a-z0-9 .'&-]+\??", q):
        t = re.sub(r"\s+", " ", str(event_title)).strip().lower()
        fut = _futures_type(t)
        if fut is not None:
            return "futures", fut
    return "other", None


def _futures_type(text: str) -> str | None:
    """Detect a season-long futures market type from lowercased text (question or title)."""
    if _DIVISION_RE.search(text):
        return "division"
    if _CONFERENCE_RE.search(text):
        return "conference"
    if _SUPER_BOWL_RE.search(text):
        return "super_bowl"
    if _PLAYOFFS_RE.search(text):
        # "win 2+ playoff games" is neither make-the-playoffs nor a regular-season win total;
        # leave it unclassified so the strategy layer never prices it off the wrong model.
        return None if _PLAYOFF_WINS_RE.search(text) else "playoffs"
    if _WIN_TOTAL_RE.search(text):
        return "win_total"
    if _TOP_SEED_RE.search(text):
        return "top_seed"
    return None


def _home_away_from_slug(event_slug: str) -> tuple[str | None, str | None, str | None]:
    """``nfl-<away>-<home>-<date>`` -> (home, away, date_str). Nones when it does not match."""
    m = _EVENT_SLUG_RE.match((event_slug or "").lower())
    if not m:
        return None, None, None
    away = normalize_team_name(m.group(1))
    home = normalize_team_name(m.group(2))
    if home is None or away is None or home == away:
        return None, None, m.group(3)
    return home, away, m.group(3)


def _home_away_from_title(*texts: str | None) -> tuple[str | None, str | None]:
    """First text of the form 'Away vs. Home' with exactly two teams -> (home, away)."""
    for t in texts:
        if not t:
            continue
        found = teams_in_text(t)
        if len(found) == 2:
            return found[1], found[0]
    return None, None


def _outcome_index(outcomes: Sequence[str], predicate) -> int | None:
    for i, o in enumerate(outcomes):
        if predicate(str(o)):
            return i
    return None


def _outcome_team(outcome: Any) -> str | None:
    """Team code named by an outcome label; None for Yes/No/Over/Under and unknown labels.

    ``normalize_team_name("No")`` resolves to the Saints (``no`` is a vendor alias), so the
    Yes/No labels are excluded before any team lookup.
    """
    s = re.sub(r"\s+", " ", str(outcome or "")).strip().lower()
    if not s or s in _YES_NO_LABELS or s.startswith(("over", "under")):
        return None
    return normalize_team_name(s)


def _yes_team_from_question(question: str, home: str | None, away: str | None) -> str | None:
    """Subject of a Yes/No game question: 'Will the Chiefs beat the Dolphins?' -> KC.

    The team named in the last clause before the verb (beat / defeat / cover / win) is the
    subject; for 'lose' / 'fall' the YES outcome is the *other* team's win. A question with no
    verb and exactly one of the two teams names that team. Anything ambiguous returns None; the
    caller must not substitute the home team.
    """
    if home is None or away is None:
        return None
    q = re.sub(r"\s+", " ", str(question or "")).strip().lower()
    if not q:
        return None
    m = _YES_SUBJECT_VERB_RE.search(q)
    if m is None:
        found = [c for c in teams_in_text(q) if c in (home, away)]
        return found[0] if len(found) == 1 else None
    subject: str | None = None
    for clause in reversed(re.split(r"[:;,?]", q[: m.start()])):
        found = [c for c in teams_in_text(clause) if c in (home, away)]
        if found:
            subject = found[0] if len(found) == 1 else None
            break
    if subject is None:
        return None
    if m.group(1).startswith(("lose", "fall")):
        return away if subject == home else home
    return subject


def _spread_quotes(market: "PolyMarket") -> list[tuple[str, float, str]]:
    """Every '<Team> (-x)' / '<Team> -x' quote in the question, groupItemTitle and outcome labels.

    Text is split into clauses on ``: ; , ?``. Within a clause the owner of a signed number is
    the text between the previous number (or the clause start) and it, and that owner must name
    exactly one team, which must be the market's home or away. So 'Spread: Chiefs (-10.5)',
    'Chiefs vs. Dolphins: Chiefs -10.5', 'Chiefs (-10.5) vs. Dolphins (+10.5)' and an outcome
    label 'Dolphins +10.5' all yield a quote, while a matchup label with a dangling number
    ('Chiefs vs. Dolphins Spread -10.5', 'Chiefs @ Dolphins -10.5') names two teams and yields
    nothing: it does not say whose number it is, and longest-alias matching must not pick a side.
    Returns ``(code, number, source)`` triples.
    """
    sources: list[tuple[str, Any]] = [("question", market.question), ("groupItemTitle", market.raw.get("groupItemTitle"))]
    sources += [(f"outcome[{i}]", o) for i, o in enumerate(market.outcomes)]
    out: list[tuple[str, float, str]] = []
    for source, text in sources:
        if not text:
            continue
        for clause in _CLAUSE_SPLIT_RE.split(str(text)):
            prev_end = 0
            for m in _SIGNED_NUMBER_RE.finditer(clause):
                owner = clause[prev_end:m.start()]
                prev_end = m.end()
                found = teams_in_text(owner)
                if len(found) != 1 or found[0] not in (market.home, market.away):
                    continue
                out.append((found[0], float(m.group("num")), source))
    return out


def spread_line_home(market: "PolyMarket") -> float:
    """Spread as the expected (home - away) margin in the *market's* home frame (nflverse ``spread_line``).

    "Spread: Chiefs (-10.5)" with the Chiefs away -> -10.5 (home underdog by 10.5). The side comes
    only from a single-team quote in the question, ``groupItemTitle`` or the outcome labels
    ("Chiefs -10.5" / "Dolphins +10.5"); every quote found must agree, and the magnitude must
    agree with Gamma's ``line`` when that is present. The sign of ``line`` on its own is never
    used: its convention is unverified, so with no team-labelled quote the result is NaN rather
    than a guess. ``match_game_markets`` flips this into the schedule's home frame as ``line_home``.
    """
    if market.kind != "spread" or market.home is None or market.away is None:
        return math.nan
    quotes = _spread_quotes(market)
    if not quotes:
        logger.debug("market %s: no '<Team> +/-x' quote in question/groupItemTitle/outcomes; "
                     "not inferring the spread side from line=%r", market.market_id, market.line)
        return math.nan
    # "Team (-x)" => team favored by x => expected team margin +x; negate when that team is home.
    values = {(-num if code == market.home else num) for code, num, _ in quotes}
    if len(values) > 1:
        logger.warning("market %s: conflicting spread quotes %s -> line_home NaN", market.market_id, quotes)
        return math.nan
    value = float(values.pop())
    if market.line is not None and not math.isnan(market.line) and abs(abs(market.line) - abs(value)) > 1e-9:
        logger.warning("market %s: text spread %s disagrees with Gamma line=%r -> line_home NaN",
                       market.market_id, quotes, market.line)
        return math.nan
    return value


# ---------------------------------------------------------------------------------- parse_market
def parse_market(market: dict, event: dict | None = None) -> PolyMarket | None:
    """Parse one Gamma market dict (plus its parent event) into a ``PolyMarket``.

    Returns None for structurally unusable markets: no id, fewer than two outcomes, or outcome /
    token-id lists that cannot be parsed or have mismatched lengths. Gamma list fields may be
    JSON-encoded strings or real lists; numbers may be strings.
    """
    event = event or {}
    if not isinstance(market, dict):
        return None
    mid = market.get("id")
    if mid is None or str(mid).strip() == "":
        logger.debug("skipping market without id: %r", market.get("question"))
        return None
    outcomes_raw = _as_list(market.get("outcomes"))
    if not outcomes_raw or len(outcomes_raw) < 2:
        logger.debug("skipping market %s: unusable outcomes %r", mid, market.get("outcomes"))
        return None
    outcomes = [str(o) for o in outcomes_raw]
    token_raw = _as_list(market.get("clobTokenIds"))
    if not token_raw or len(token_raw) != len(outcomes):
        logger.debug("skipping market %s: unusable clobTokenIds %r", mid, market.get("clobTokenIds"))
        return None
    token_ids = [str(t) for t in token_raw]

    prices_raw = _as_list(market.get("outcomePrices"))
    if prices_raw is None:
        prices = [math.nan] * len(outcomes)
    else:
        prices = [_to_float(p) for p in prices_raw]
        if len(prices) != len(outcomes):
            logger.warning("market %s: %d prices for %d outcomes; padding/truncating", mid, len(prices), len(outcomes))
            prices = (prices + [math.nan] * len(outcomes))[: len(outcomes)]

    question = str(market.get("question") or "").strip()
    event_slug = str(event.get("slug") or "").strip()
    event_title = str(event.get("title") or "").strip()
    group_item_title = market.get("groupItemTitle")
    kind, futures_type = classify_kind(question, market.get("sportsMarketType"), event_title)

    home: str | None = None
    away: str | None = None
    team: str | None = None
    confident = True
    if kind in ("moneyline", "spread", "total"):
        home, away, _ = _home_away_from_slug(event_slug)
        if home is not None:
            t_home, t_away = _home_away_from_title(event_title)
            if t_home is not None and {t_home, t_away} == {home, away} and t_home != home:
                # Title order contradicts the slug: keep the slug but flag the ambiguity.
                confident = False
        else:
            home, away = _home_away_from_title(event_title, question, market.get("description"))
            confident = False
            if home is None:
                # Team-named outcomes as a last resort (outcome order is not a home/away signal).
                codes = [c for c in (_outcome_team(o) for o in outcomes) if c]
                if len(codes) == 2 and codes[0] != codes[1]:
                    away, home = codes[0], codes[1]
    else:
        team = normalize_team_name(str(group_item_title)) if group_item_title else None
        if team is None:
            found = teams_in_text(question)
            team = found[0] if found else None

    yes_index = _yes_index(kind, outcomes, home)
    if kind in ("moneyline", "spread") and home is not None:
        # Team-labelled outcomes that do not include the derived home team contradict the slug/title.
        # Bare Yes/No labels say nothing about home/away and leave the flag alone.
        outcome_codes = [c for c in (_outcome_team(o) for o in outcomes) if c]
        if outcome_codes and home not in outcome_codes:
            confident = False

    line = _to_float(_first_present(market, "line"), default=math.nan)
    end_date = _first_present(market, "endDate", "endDateIso", "end_date")
    liquidity = _to_float(_first_present(market, "liquidityNum", "liquidity"), default=0.0)
    volume = _to_float(_first_present(market, "volumeNum", "volume"), default=0.0)

    return PolyMarket(
        market_id=str(mid),
        condition_id=str(market.get("conditionId") or market.get("condition_id") or ""),
        question=question,
        slug=str(market.get("slug") or ""),
        event_slug=event_slug,
        event_title=event_title,
        outcomes=outcomes,
        token_ids=token_ids,
        prices=prices,
        end_date=str(end_date) if end_date is not None else None,
        liquidity=liquidity,
        volume=volume,
        active=_to_bool(market.get("active"), default=True),
        closed=_to_bool(market.get("closed"), default=False),
        kind=kind,
        home=home,
        away=away,
        team=team,
        line=None if math.isnan(line) else line,
        yes_index=yes_index,
        book=None,
        raw=market,
        home_away_confident=confident,
        futures_type=futures_type,
        event_id=str(event["id"]) if event.get("id") is not None else None,
        start_date=str(event["startDate"]) if event.get("startDate") else None,
    )


def _yes_index(kind: str, outcomes: Sequence[str], home: str | None) -> int:
    """Outcome index that ``fair_prob`` refers to (see module docstring)."""
    lowered = [str(o).strip().lower() for o in outcomes]
    if kind == "total":
        idx = _outcome_index(lowered, lambda o: o.startswith("over"))
        return idx if idx is not None else 0
    if kind in ("moneyline", "spread"):
        if home is not None:
            idx = _outcome_index(outcomes, lambda o: _outcome_team(o) == home)
            if idx is not None:
                return idx
        idx = _outcome_index(lowered, lambda o: o == "yes")
        return idx if idx is not None else 0
    idx = _outcome_index(lowered, lambda o: o == "yes")
    return idx if idx is not None else 0


# ------------------------------------------------------------------------------------ client
class PolymarketClient:
    """Gamma + CLOB client. With ``fixture_dir`` set, every call reads JSON files and never touches HTTP.

    Fixture layout: ``events.json`` (list of Gamma events), ``book_<token_id>.json`` (CLOB book),
    ``history_<token_id>.json`` (``{"history": [{"t", "p"}]}``; missing -> empty frame).
    """

    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        gamma_url: str = config.GAMMA_API,
        clob_url: str = config.CLOB_API,
        session: requests.Session | None = None,
        fixture_dir: str | Path | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff: float = 0.5,
        max_pages: int = 100,
    ) -> None:
        self.gamma_url = gamma_url.rstrip("/")
        self.clob_url = clob_url.rstrip("/")
        self.fixture_dir = Path(fixture_dir) if fixture_dir is not None else None
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff = float(backoff)
        self.max_pages = int(max_pages)
        self._session = session
        if self.fixture_dir is not None and not self.fixture_dir.is_dir():
            raise FileNotFoundError(f"fixture_dir does not exist: {self.fixture_dir}")

    # ------------------------------------------------------------------ transport
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _get_json(self, url: str, params: dict | None = None) -> Any:
        """GET with timeout and bounded exponential backoff on 429/5xx and transport errors."""
        headers = {"Accept": "application/json", "User-Agent": "nfl-edge/0.1 (+polymarket scanner)"}
        attempts = self.max_retries + 1
        last_error: str = "no attempt made"
        for attempt in range(attempts):
            resp: Any = None
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
            except requests.RequestException as exc:  # transport-level: retry
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("GET %s attempt %d/%d failed: %s", url, attempt + 1, attempts, last_error)
            else:
                status = int(getattr(resp, "status_code", 0))
                if status in self.RETRY_STATUSES:
                    last_error = f"HTTP {status}"
                    logger.warning("GET %s attempt %d/%d -> %s", url, attempt + 1, attempts, last_error)
                elif status >= 400:
                    raise PolymarketError(f"GET {url} params={params} -> HTTP {status}: {_body_excerpt(resp)}")
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise PolymarketError(f"GET {url} returned non-JSON body: {_body_excerpt(resp)}") from exc
            if attempt < attempts - 1:
                time.sleep(self._sleep_for(attempt, resp))
        raise PolymarketError(f"GET {url} params={params} failed after {attempts} attempts (last: {last_error})")

    def _sleep_for(self, attempt: int, resp: Any) -> float:
        """Exponential backoff (``backoff * 2**attempt``), raised to a Retry-After header (capped at 30s)."""
        delay = self.backoff * (2 ** attempt)
        headers = getattr(resp, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            retry_after = _to_float(headers.get("Retry-After"))
            if not math.isnan(retry_after):
                delay = max(delay, min(retry_after, 30.0))
        return float(delay)

    def _fixture(self, name: str, required: bool = True) -> Any:
        assert self.fixture_dir is not None
        path = self.fixture_dir / name
        if not path.exists():
            if required:
                raise PolymarketError(f"fixture not found: {path}")
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------ Gamma
    def list_nfl_events(
        self,
        active: bool | None = True,
        closed: bool | None = False,
        limit: int = 100,
        tag_slug: str | None = "nfl",
    ) -> list[dict]:
        """All events tagged ``tag_slug`` (paginated by offset). ``None`` for active/closed disables the filter."""
        if self.fixture_dir is not None:
            events = self._fixture("events.json")
            if isinstance(events, dict):
                events = events.get("data") or events.get("events") or []
            return [e for e in events if self._event_matches(e, active, closed, tag_slug)]

        out: list[dict] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(self.max_pages):
            params: dict[str, Any] = {"limit": int(limit), "offset": offset}
            if tag_slug:
                params["tag_slug"] = tag_slug
            if active is not None:
                params["active"] = "true" if active else "false"
            if closed is not None:
                params["closed"] = "true" if closed else "false"
            page = self._get_json(f"{self.gamma_url}/events", params)
            if isinstance(page, dict):
                page = page.get("data") or page.get("events") or []
            if not isinstance(page, list):
                raise PolymarketError(f"unexpected /events payload type {type(page).__name__}")
            for ev in page:
                key = str(ev.get("id") or ev.get("slug"))
                if key not in seen:
                    seen.add(key)
                    out.append(ev)
            if len(page) < limit:
                break
            offset += limit
        else:
            logger.warning("list_nfl_events stopped after max_pages=%d pages", self.max_pages)
        return out

    @staticmethod
    def _event_matches(ev: dict, active: bool | None, closed: bool | None, tag_slug: str | None) -> bool:
        if active is not None and _to_bool(ev.get("active"), default=True) != active:
            return False
        if closed is not None and _to_bool(ev.get("closed"), default=False) != closed:
            return False
        if tag_slug:
            slugs = {str(t.get("slug", "")).lower() for t in (ev.get("tags") or []) if isinstance(t, dict)}
            if tag_slug.lower() not in slugs:
                return False
        return True

    def markets_from_events(self, events: Iterable[dict]) -> list[PolyMarket]:
        """Parse every market of every event; unusable markets are skipped (logged at debug)."""
        out: list[PolyMarket] = []
        seen: set[str] = set()
        for ev in events:
            for m in ev.get("markets") or []:
                pm = parse_market(m, ev)
                if pm is None or pm.market_id in seen:
                    continue
                seen.add(pm.market_id)
                out.append(pm)
        return out

    # ------------------------------------------------------------------ CLOB
    def orderbook(self, token_id: str) -> OrderBook:
        """``GET {clob}/book?token_id=`` -> OrderBook (levels sorted best-first)."""
        token_id = str(token_id)
        if self.fixture_dir is not None:
            payload = self._fixture(f"book_{token_id}.json")
        else:
            payload = self._get_json(f"{self.clob_url}/book", {"token_id": token_id})
        if not isinstance(payload, dict):
            raise PolymarketError(f"unexpected /book payload for token {token_id}: {type(payload).__name__}")
        book = OrderBook.from_clob(payload)
        if book.token_id is None:
            book.token_id = token_id
        return book

    def enrich_with_books(self, markets: Iterable[PolyMarket], strict: bool = False) -> list[PolyMarket]:
        """Attach the YES-token order book to each market (in place) and return the list.

        A failed fetch is logged and leaves ``book=None`` unless ``strict`` is True. Because every
        Polymarket outcome market is binary, the NO side is the complement of the YES book.
        """
        out = list(markets)
        missing: list[str] = []
        for m in out:
            try:
                m.book = self.orderbook(m.yes_token)
            except PolymarketError as exc:
                if strict:
                    raise
                logger.debug("no order book for market %s (%s): %s", m.market_id, m.question, exc)
                missing.append(m.market_id)
                m.book = None
        if missing:
            logger.warning("no order book for %d/%d markets (book=None): %s", len(missing), len(out),
                           ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else ""))
        return out

    def price_history(self, token_id: str, interval: str = "max", fidelity: int = 60) -> pd.DataFrame:
        """``GET {clob}/prices-history?market=<token>`` -> DataFrame[t, price, ts] sorted by time."""
        token_id = str(token_id)
        if self.fixture_dir is not None:
            payload = self._fixture(f"history_{token_id}.json", required=False) or {"history": []}
        else:
            payload = self._get_json(
                f"{self.clob_url}/prices-history",
                {"market": token_id, "interval": interval, "fidelity": int(fidelity)},
            )
        hist = payload.get("history") if isinstance(payload, dict) else payload
        rows = [(int(_to_float(h.get("t"))), _to_float(h.get("p"))) for h in (hist or [])
                if isinstance(h, dict) and not math.isnan(_to_float(h.get("t")))]
        df = pd.DataFrame(rows, columns=["t", "price"])
        df["ts"] = pd.to_datetime(df["t"], unit="s", utc=True)
        df["token_id"] = token_id
        return df.sort_values("t").reset_index(drop=True)


def _body_excerpt(resp: Any, n: int = 200) -> str:
    text = getattr(resp, "text", "")
    return str(text)[:n].replace("\n", " ")


# ------------------------------------------------------------------------------ match_game_markets
def match_game_markets(markets: Iterable[PolyMarket], games: pd.DataFrame, max_days: int = 3) -> pd.DataFrame:
    """Join moneyline/spread/total markets to nflverse game rows.

    Match key: the unordered canonical team pair, with |game_date - market date| <= ``max_days``
    (the closest game wins if several qualify; NFL teams play at most once a week so ties are
    theoretical). Home/away in the output come from the schedule; ``home_matches_schedule`` flags
    whether the market's own home/away derivation agrees. Every signed quantity in a row is in
    the schedule's frame: ``line_home`` is negated when the market lists the schedule's away team
    as home (the raw market-frame value is kept as ``line_market_home``). ``yes_team`` is a code
    and therefore frame-independent; it is None for a Yes/No game question whose subject could
    not be resolved (never a defaulted home team).
    """
    rows = []
    for m in markets:
        if not m.is_game_market or m.home is None or m.away is None:
            continue
        book = m.book
        rows.append({
            "market_id": m.market_id,
            "kind": m.kind,
            "yes_team": m.yes_team,
            "price_yes": m.price_yes,
            "condition_id": m.condition_id,
            "question": m.question,
            "event_slug": m.event_slug,
            "token_id_yes": m.yes_token,
            "yes_index": m.yes_index,
            "yes_outcome": m.yes_outcome,
            "line": math.nan if m.line is None else float(m.line),
            "line_market_home": spread_line_home(m) if m.kind == "spread" else math.nan,
            "market_home": m.home,
            "market_away": m.away,
            "home_away_confident": bool(m.home_away_confident),
            "market_date": m.market_date,
            "liquidity": m.liquidity,
            "volume": m.volume,
            "end_date": m.end_date,
            "best_bid": book.best_bid if book is not None else math.nan,
            "best_ask": book.best_ask if book is not None else math.nan,
            "mid": book.mid if book is not None else math.nan,
            "active": bool(m.active),
            "closed": bool(m.closed),
            "_pair": "|".join(sorted((m.home, m.away))),
        })
    if not rows:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    mk = pd.DataFrame(rows)
    mk["market_date"] = pd.to_datetime(mk["market_date"], errors="coerce")

    g_cols = ["game_id", "home_team_c", "away_team_c", "game_date"]
    optional = ["season", "week", "game_type", "spread_line", "total_line", "market_prob", "played"]
    g = games[g_cols + [c for c in optional if c in games.columns]].copy()
    for c in optional:
        if c not in g.columns:
            g[c] = np.nan
    g = g[g["game_date"].notna()]
    g["game_date"] = pd.to_datetime(g["game_date"])
    pair = np.where(
        g["home_team_c"].astype(str) < g["away_team_c"].astype(str),
        g["home_team_c"].astype(str) + "|" + g["away_team_c"].astype(str),
        g["away_team_c"].astype(str) + "|" + g["home_team_c"].astype(str),
    )
    g["_pair"] = pair

    joined = mk.merge(g, on="_pair", how="inner")
    diff = (joined["game_date"].dt.normalize() - joined["market_date"].dt.normalize()).dt.days
    joined["date_diff_days"] = diff
    joined = joined[diff.abs() <= max_days].copy()
    if joined.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    joined["_absdiff"] = joined["date_diff_days"].abs()
    joined = (
        joined.sort_values(["market_id", "_absdiff", "game_date"])
        .drop_duplicates("market_id", keep="first")
        .drop(columns=["_absdiff", "_pair"])
    )
    joined["home_matches_schedule"] = joined["market_home"] == joined["home_team_c"]
    # Spread in the schedule's home frame: flip when the market's "home" is the schedule's away team.
    joined["line_home"] = np.where(
        joined["home_matches_schedule"], joined["line_market_home"], -joined["line_market_home"]
    ).astype(float)
    joined["date_diff_days"] = joined["date_diff_days"].astype(int)
    out = joined[MATCH_COLUMNS].sort_values(["game_date", "game_id", "kind", "market_id"]).reset_index(drop=True)
    return out


# ------------------------------------------------------------------------------------ fees
_FEE_BPS_KEYS = ("feeRateBps", "takerFeeBps", "makerFeeBps", "fee_rate_bps", "feeBps")
_FEE_FRACTION_KEYS = ("takerFee", "fee", "feeRate", "taker_fee", "fee_rate")


def fee_for_market(market: PolyMarket, default: float = 0.0) -> float:
    """Taker fee rate for a market from its raw Gamma fields, else ``default``.

    Basis-point fields (``feeRateBps`` etc.) are divided by 1e4; fractional fields (``takerFee``,
    ``fee``, ``feeRate``) are used as-is. Out-of-range values are ignored with a warning.
    """
    raw = market.raw if isinstance(market.raw, dict) else {}
    for key in _FEE_BPS_KEYS:
        if key in raw and raw[key] is not None and raw[key] != "":
            v = _to_float(raw[key])
            if not math.isnan(v) and 0.0 <= v < 10_000.0:
                return v / 1e4
            logger.warning("market %s: ignoring bad fee field %s=%r", market.market_id, key, raw[key])
    for key in _FEE_FRACTION_KEYS:
        if key in raw and raw[key] is not None and raw[key] != "":
            v = _to_float(raw[key])
            if not math.isnan(v) and 0.0 <= v < 1.0:
                return v
            logger.warning("market %s: ignoring bad fee field %s=%r", market.market_id, key, raw[key])
    return float(default)


# ------------------------------------------------------------------------------------ snapshots
def snapshot(markets: Iterable[PolyMarket], path: str | Path, ts: str | None = None) -> int:
    """Append one JSONL row per outcome token of each market; returns rows written.

    Book fields come from the YES book; for the other outcome of a binary market they are the
    complement (NO bid = 1 - YES ask, NO ask = 1 - YES bid).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = ts or _iso_now()
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for m in markets:
            book = m.book
            yes_bid = book.best_bid if book is not None else None
            yes_ask = book.best_ask if book is not None else None
            binary = len(m.outcomes) == 2
            for i, (outcome, token) in enumerate(zip(m.outcomes, m.token_ids)):
                if i == m.yes_index:
                    bid, ask = yes_bid, yes_ask
                elif binary and (yes_bid is not None or yes_ask is not None):
                    bid = None if yes_ask is None else round(1.0 - yes_ask, 6)
                    ask = None if yes_bid is None else round(1.0 - yes_bid, 6)
                else:
                    bid, ask = None, None
                mid = None if bid is None or ask is None else round(0.5 * (bid + ask), 6)
                spread = None if bid is None or ask is None else round(ask - bid, 6)
                price = m.prices[i] if i < len(m.prices) else math.nan
                row = {
                    "ts": ts,
                    "market_id": m.market_id,
                    "condition_id": m.condition_id,
                    "question": m.question,
                    "kind": m.kind,
                    "event_slug": m.event_slug,
                    "token_id": token,
                    "outcome": outcome,
                    "outcome_index": i,
                    "is_yes": i == m.yes_index,
                    "price": None if price is None or math.isnan(price) else float(price),
                    "best_bid": bid,
                    "best_ask": ask,
                    "mid": mid,
                    "spread": spread,
                    "liquidity": float(m.liquidity),
                    "volume": float(m.volume),
                    "end_date": m.end_date,
                    "home": m.home,
                    "away": m.away,
                    "team": m.team,
                    "line": m.line,
                }
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                n += 1
    return n


def load_snapshots(path: str | Path) -> pd.DataFrame:
    """Read a JSONL snapshot file into a tidy DataFrame (``ts`` parsed to UTC, numerics as float)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"snapshot file not found: {path}")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: malformed JSONL row: {exc}") from exc
    df = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS) if rows else pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    for c in ("price", "best_bid", "best_ask", "mid", "spread", "liquidity", "volume", "line"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["outcome_index"] = pd.to_numeric(df["outcome_index"], errors="coerce").fillna(0).astype(int)
    df["is_yes"] = df["is_yes"].fillna(False).astype(bool)
    for c in ("market_id", "token_id", "condition_id"):
        df[c] = df[c].astype(str)
    return df.sort_values(["ts", "market_id", "outcome_index"]).reset_index(drop=True)
