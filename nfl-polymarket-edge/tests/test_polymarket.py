"""Offline tests for nfl_edge.markets.polymarket against Gamma/CLOB-shaped fixtures."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import load_games
from nfl_edge.markets import polymarket as pm
from nfl_edge.markets.polymarket import (
    OrderBook,
    PolyMarket,
    PolymarketClient,
    PolymarketError,
    classify_kind,
    fee_for_market,
    load_snapshots,
    match_game_markets,
    parse_market,
    snapshot,
    spread_line_home,
)

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
GAMES = CACHE_DIR / "games.csv"
FIXTURE_GAMES = {"2026_03_KC_MIA", "2026_03_BAL_DAL", "2026_03_SEA_WAS", "2026_03_PHI_CHI"}


# ------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def client() -> PolymarketClient:
    return PolymarketClient(fixture_dir=FIXTURES)


@pytest.fixture(scope="module")
def events(client) -> list[dict]:
    return client.list_nfl_events()


@pytest.fixture(scope="module")
def markets(client, events) -> list[PolyMarket]:
    return client.markets_from_events(events)


@pytest.fixture(scope="module")
def by_slug(markets) -> dict[str, PolyMarket]:
    return {m.slug: m for m in markets}


@pytest.fixture(scope="module")
def games() -> pd.DataFrame:
    if not GAMES.exists():
        pytest.skip("games.csv not cached")
    return load_games(path=GAMES)


def _event(slug: str) -> dict:
    return next(e for e in json.loads((FIXTURES / "events.json").read_text()) if e["slug"] == slug)


def _yes_no(question: str, **extra) -> dict:
    m = {
        "id": "1", "question": question, "conditionId": "0xabc", "slug": "x",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.3", "0.7"]', "clobTokenIds": '["11", "22"]',
        "endDate": "2027-01-10T23:59:00Z", "liquidity": "1000", "volume": "5000", "active": True, "closed": False,
    }
    m.update(extra)
    return m


# ------------------------------------------------------------------------------ parse_market
def test_parse_str_encoded_fields(by_slug):
    m = by_slug["nfl-kc-mia-2026-09-27-moneyline"]
    raw = m.raw
    assert isinstance(raw["outcomes"], str) and isinstance(raw["outcomePrices"], str)
    assert isinstance(raw["clobTokenIds"], str) and isinstance(raw["liquidity"], str)
    assert m.outcomes == ["Chiefs", "Dolphins"]
    assert m.prices == [0.86, 0.14]
    assert len(m.token_ids) == 2 and all(t.isdigit() and len(t) > 60 for t in m.token_ids)
    assert m.liquidity > 0 and m.volume > 0 and isinstance(m.liquidity, float)
    assert m.end_date == "2026-09-27T23:59:00Z"
    assert m.condition_id.startswith("0x") and len(m.condition_id) == 66
    assert m.event_slug == "nfl-kc-mia-2026-09-27" and m.event_title == "Chiefs vs. Dolphins"
    assert m.event_id and m.start_date == "2026-09-27T17:00:00Z"
    assert m.active and not m.closed and m.book is None


def test_parse_list_encoded_fields(by_slug):
    m = by_slug["nfl-bal-dal-2026-09-27-spread"]
    raw = m.raw
    assert isinstance(raw["outcomes"], list) and isinstance(raw["outcomePrices"], list)
    assert isinstance(raw["clobTokenIds"], list) and isinstance(raw["liquidity"], float)
    assert m.outcomes == ["Ravens", "Cowboys"]
    assert m.prices == [0.507, 0.493]
    assert m.line == -3.5 and isinstance(m.line, float)
    assert m.kind == "spread"


def test_parse_numbers_as_strings_and_bools():
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins", "id": 5}
    m = parse_market(_yes_no("Chiefs vs. Dolphins", outcomes=["Chiefs", "Dolphins"], outcomePrices=["0.8", 0.2],
                             line="-10.5", liquidity="12,345.5", volume=None, active="true", closed="false",
                             sportsMarketType="spreads"), ev)
    assert m is not None
    assert m.prices == [0.8, 0.2] and m.line == -10.5 and m.liquidity == 12345.5 and m.volume == 0.0
    assert m.active is True and m.closed is False and m.event_id == "5"


def test_parse_returns_none_for_unusable_markets():
    assert parse_market({}, {}) is None
    assert parse_market(_yes_no("q", id=""), {}) is None
    assert parse_market(_yes_no("q", outcomes="not json"), {}) is None
    assert parse_market(_yes_no("q", outcomes='["Yes"]'), {}) is None
    assert parse_market(_yes_no("q", clobTokenIds='["only-one"]'), {}) is None
    assert parse_market("nope", {}) is None  # type: ignore[arg-type]


def test_parse_missing_prices_gives_nan_not_crash():
    m = parse_market(_yes_no("Will the Chicago Bears make the playoffs?", outcomePrices=None), {})
    assert m is not None and len(m.prices) == 2 and all(math.isnan(p) for p in m.prices)
    assert math.isnan(m.price_yes)


# ------------------------------------------------------------------------------- classification
@pytest.mark.parametrize(
    "question,smt,kind,ftype",
    [
        ("Chiefs vs. Dolphins", "moneyline", "moneyline", None),
        ("Chiefs vs. Dolphins", None, "moneyline", None),
        ("Spread: Chiefs (-10.5)", "spreads", "spread", None),
        ("Spread: Chiefs (-10.5)", None, "spread", None),
        ("Chiefs vs. Dolphins: O/U 45.5", "totals", "total", None),
        ("Chiefs vs. Dolphins: O/U 45.5", None, "total", None),
        ("Will the Kansas City Chiefs win Super Bowl LXI?", None, "futures", "super_bowl"),
        ("Will the Buffalo Bills win the AFC Championship?", None, "futures", "conference"),
        ("Will the Buffalo Bills win the AFC?", None, "futures", "conference"),
        ("Will the Bills make the Super Bowl?", None, "futures", "conference"),
        ("Will the Detroit Lions win the NFC North?", None, "futures", "division"),
        ("Will the Dallas Cowboys win the NFC East?", None, "futures", "division"),
        ("Will the Chicago Bears make the playoffs?", None, "futures", "playoffs"),
        ("Will the Eagles win 12+ regular season games?", None, "futures", "win_total"),
        ("Will the Eagles win more than 11.5 games?", None, "futures", "win_total"),
        ("Will the Lions get the #1 seed in the NFC?", None, "futures", "top_seed"),
        ("Will Patrick Mahomes win the 2026-27 NFL MVP?", None, "other", None),
        ("Chiefs vs. Dolphins: Will the game go to overtime?", None, "other", None),
        ("Will there be a safety in Chiefs vs. Dolphins?", None, "other", None),
    ],
)
def test_classify_every_kind(question, smt, kind, ftype):
    assert classify_kind(question, smt) == (kind, ftype)


def test_classify_bare_question_uses_event_title():
    assert classify_kind("Kansas City Chiefs", None, "Super Bowl Champion 2027") == ("futures", "super_bowl")
    assert classify_kind("Buffalo Bills", None, "AFC Champion 2026-27") == ("futures", "conference")
    assert classify_kind("Detroit Lions", None, "NFC North Winner 2026-27") == ("futures", "division")
    assert classify_kind("Kansas City Chiefs", None, "Chiefs vs. Dolphins") == ("other", None)


def test_fixture_covers_every_kind(markets):
    kinds = Counter(m.kind for m in markets)
    assert set(kinds) == set(pm.KINDS)
    assert kinds["moneyline"] == 4 and kinds["spread"] == 4 and kinds["total"] == 4
    ftypes = Counter(m.futures_type for m in markets if m.kind == "futures")
    assert ftypes["super_bowl"] >= 6 and ftypes["division"] == 4 and ftypes["playoffs"] >= 1
    assert ftypes["conference"] >= 1 and ftypes["win_total"] >= 1


# ------------------------------------------------------------------------------ team extraction
def test_futures_team_extraction_incl_commanders_and_49ers(markets):
    sb = {m.team: m for m in markets if m.futures_type == "super_bowl"}
    assert {"KC", "PHI", "BUF", "BAL", "DET", "SF", "WAS", "LA", "GB", "CHI"} <= set(sb)
    assert sb["SF"].question == "Will the San Francisco 49ers win Super Bowl LXI?"
    assert sb["WAS"].question == "Will the Washington Commanders win Super Bowl LXI?"
    po = {m.team for m in markets if m.futures_type == "playoffs"}
    assert po == {"CHI", "WAS"}
    assert {m.team for m in markets if m.futures_type == "division"} == {"DET", "GB", "MIN", "CHI"}
    assert {m.team for m in markets if m.futures_type == "win_total"} == {"PHI", "KC"}
    assert all(m.team is None for m in markets if m.question.endswith("NFL MVP?"))


def test_team_from_question_when_group_item_title_missing():
    m = parse_market(_yes_no("Will the San Francisco 49ers win Super Bowl LXI?"), {"title": "Super Bowl Champion 2027"})
    assert m is not None and m.team == "SF" and m.kind == "futures"
    m = parse_market(_yes_no("Will the Commanders make the playoffs?", groupItemTitle=""), {})
    assert m is not None and m.team == "WAS" and m.futures_type == "playoffs"


def test_home_away_from_slug(by_slug):
    for slug, home, away in [
        ("nfl-kc-mia-2026-09-27-moneyline", "MIA", "KC"), ("nfl-bal-dal-2026-09-27-moneyline", "DAL", "BAL"),
        ("nfl-sea-was-2026-09-27-total", "WAS", "SEA"), ("nfl-phi-chi-2026-09-28-spread", "CHI", "PHI"),
    ]:
        m = by_slug[slug]
        assert (m.home, m.away) == (home, away), slug
        assert m.home_away_confident is True
        assert m.market_date == pd.Timestamp(m.event_slug[-10:])


def test_home_away_from_title_when_slug_absent():
    ev = {"slug": "chiefs-dolphins-week-3", "title": "Chiefs vs. Dolphins", "id": "9"}
    m = parse_market(_yes_no("Chiefs vs. Dolphins", outcomes='["Chiefs", "Dolphins"]', sportsMarketType="moneyline"), ev)
    assert m is not None
    assert (m.home, m.away) == ("MIA", "KC")  # title order is Away vs. Home
    assert m.home_away_confident is False
    assert m.yes_index == 1


def test_home_away_slug_with_vendor_codes_and_contradicting_title():
    ev = {"slug": "nfl-wsh-lar-2026-10-04", "title": "Rams vs. Commanders", "id": "9"}
    m = parse_market(_yes_no("Commanders vs. Rams", outcomes='["Commanders", "Rams"]', sportsMarketType="moneyline"), ev)
    assert m is not None
    assert (m.home, m.away) == ("LA", "WAS")     # slug wins ...
    assert m.home_away_confident is False        # ... but the disagreement is exposed


# ------------------------------------------------------------------------------------ yes_index
def test_yes_index_rules(by_slug):
    ml = by_slug["nfl-kc-mia-2026-09-27-moneyline"]
    assert ml.outcomes[ml.yes_index] == "Dolphins" and ml.yes_team == "MIA"
    assert ml.price_yes == 0.14 and ml.yes_token == ml.token_ids[1] and ml.no_index == 0
    sp = by_slug["nfl-kc-mia-2026-09-27-spread"]
    assert sp.outcomes[sp.yes_index] == "Dolphins" and sp.yes_team == "MIA"
    tot = by_slug["nfl-kc-mia-2026-09-27-total"]
    assert tot.outcomes[tot.yes_index] == "Over" and tot.yes_team is None
    sb = by_slug["will-the-kansas-city-chiefs-win-super-bowl-lxi"]
    assert sb.outcomes[sb.yes_index] == "Yes" and sb.yes_team == "KC"


def test_yes_index_when_outcome_order_varies():
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    m = parse_market(_yes_no("Chiefs vs. Dolphins", outcomes='["Dolphins", "Chiefs"]', sportsMarketType="moneyline"), ev)
    assert m is not None and m.yes_index == 0 and m.yes_outcome == "Dolphins"
    m = parse_market(_yes_no("Chiefs vs. Dolphins: O/U 45.5", outcomes='["Under", "Over"]', sportsMarketType="totals"), ev)
    assert m is not None and m.yes_index == 1 and m.yes_outcome == "Over"
    m = parse_market(_yes_no("Will the Chicago Bears make the playoffs?", outcomes='["No", "Yes"]'), {})
    assert m is not None and m.yes_index == 1


def test_yes_team_for_yes_no_game_questions(games):
    """Yes/No outcomes: YES is the question's subject, never a defaulted home team."""
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    m = parse_market(_yes_no("Will the Chiefs beat the Dolphins?", outcomePrices='["0.86", "0.14"]'), ev)
    assert m.kind == "moneyline" and (m.home, m.away) == ("MIA", "KC")
    assert m.yes_index == 0 and m.yes_outcome == "Yes" and m.yes_team == "KC" and m.price_yes == 0.86
    assert m.home_away_confident is True  # slug-derived; Yes/No labels say nothing about home/away
    m = parse_market(_yes_no("Will the Chiefs cover -10.5?"), ev)
    assert m.kind == "spread" and m.yes_team == "KC" and spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Will the Dolphins lose to the Chiefs?", sportsMarketType="moneyline"), ev)
    assert m.yes_team == "KC"
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Will the Dolphins win?", sportsMarketType="moneyline"), ev)
    assert m.yes_team == "MIA"
    m = parse_market(_yes_no("Will the Dolphins beat the Chiefs?", outcomes='["No", "Yes"]'), ev)
    assert m.yes_index == 1 and m.yes_outcome == "Yes" and m.yes_team == "MIA"
    m = parse_market(_yes_no("Dolphins", sportsMarketType="moneyline"), ev)  # bare single-team title
    assert m.yes_team == "MIA"
    # subject cannot be determined -> None (a strategy layer must skip it), never the home team
    unresolved = parse_market(_yes_no("Chiefs vs. Dolphins", id="2", sportsMarketType="moneyline"), ev)
    assert unresolved.yes_team is None
    df = match_game_markets([parse_market(_yes_no("Will the Chiefs beat the Dolphins?"), ev), unresolved], games)
    assert list(df["market_id"]) == ["1", "2"] and (df["game_id"] == "2026_03_KC_MIA").all()
    assert df.loc[0, "yes_team"] == "KC" and df.loc[0, "price_yes"] == 0.3
    assert pd.isna(df.loc[1, "yes_team"])
    assert df["home_matches_schedule"].all() and df["home_away_confident"].all()


def test_no_outcome_label_is_not_the_saints():
    """'no' is a vendor alias for New Orleans; a 'No' outcome must never be treated as the Saints."""
    ev = {"slug": "nfl-atl-no-2026-10-04", "title": "Falcons vs. Saints"}
    m = parse_market(_yes_no("Will the Falcons beat the Saints?", sportsMarketType="moneyline"), ev)
    assert (m.home, m.away) == ("NO", "ATL") and m.home_away_confident is True
    assert m.yes_index == 0 and m.yes_outcome == "Yes" and m.yes_team == "ATL"
    m = parse_market(_yes_no("Will the Saints beat the Falcons?", outcomes='["No", "Yes"]',
                             sportsMarketType="moneyline"), ev)
    assert m.yes_index == 1 and m.yes_outcome == "Yes" and m.yes_team == "NO"
    m = parse_market(_yes_no("Falcons vs. Saints", outcomes='["Falcons", "Saints"]', sportsMarketType="moneyline"), ev)
    assert m.yes_index == 1 and m.yes_team == "NO"


def test_home_away_confident_only_flags_team_labelled_contradictions():
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    m = parse_market(_yes_no("Chiefs vs. Dolphins", outcomes='["Ravens", "Cowboys"]', sportsMarketType="moneyline"), ev)
    assert m.home_away_confident is False  # labels name teams, none of them the derived home
    m = parse_market(_yes_no("Will the Chiefs beat the Dolphins?", sportsMarketType="moneyline"), ev)
    assert m.home_away_confident is True   # Yes/No labels are not a home/away signal


def test_spread_line_home_convention(by_slug):
    # "Spread: Chiefs (-10.5)" with the Chiefs away => nflverse spread_line -10.5 (home dog)
    assert spread_line_home(by_slug["nfl-kc-mia-2026-09-27-spread"]) == -10.5
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    m = parse_market(_yes_no("Spread: Dolphins (+10.5)", outcomes='["Chiefs", "Dolphins"]', line=10.5,
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Spread", outcomes='["Chiefs", "Dolphins"]', line=-10.5,
                             groupItemTitle="Chiefs -10.5", sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Spread", outcomes='["Chiefs", "Dolphins"]', line=10.5,
                             groupItemTitle="Chiefs -10.5", sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5  # sign of Gamma `line` is irrelevant; only its magnitude is checked
    assert math.isnan(spread_line_home(by_slug["nfl-kc-mia-2026-09-27-moneyline"]))


def test_spread_line_home_from_outcome_labels_only():
    """Signed numbers that live only in the outcome labels carry the side (both labels must agree)."""
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    m = parse_market(_yes_no("Spread", outcomes=["Chiefs -10.5", "Dolphins +10.5"], line=10.5,
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5 and m.yes_index == 1 and m.yes_team == "MIA"
    m = parse_market(_yes_no("Spread", outcomes=["Dolphins +10.5", "Chiefs -10.5"], line=-10.5,
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5 and m.yes_index == 0 and m.yes_team == "MIA"
    m = parse_market(_yes_no("Spread", outcomes=["Chiefs (+3)", "Dolphins (-3)"], sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == 3.0
    m = parse_market(_yes_no("Chiefs vs. Dolphins", outcomes=["Chiefs -10.5", "Dolphins +10.5"],
                             groupItemTitle="Spread", sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5


def test_spread_line_home_never_guesses_sign_of_gamma_line():
    """With no '<Team> +/-x' quote anywhere the side is unknown: NaN, whatever `line` and outcome order say."""
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    for outcomes in (["Chiefs", "Dolphins"], ["Dolphins", "Chiefs"]):
        for line in (10.5, -10.5):
            for q in ("Chiefs vs. Dolphins: Spread", "Spread", "Chiefs vs. Dolphins", "Will the Chiefs cover?"):
                m = parse_market(_yes_no(q, outcomes=outcomes, line=line, groupItemTitle="Spread",
                                         sportsMarketType="spreads"), ev)
                assert m.kind == "spread" and m.line == line
                assert math.isnan(spread_line_home(m)), (q, outcomes, line)
    m = parse_market(_yes_no("Will the Chiefs cover?", line=-10.5, sportsMarketType="spreads"), ev)  # Yes/No
    assert math.isnan(spread_line_home(m)) and m.yes_team == "KC"


def test_spread_line_home_rejects_multi_team_spans():
    """'Chiefs vs. Dolphins -10.5' does not say whose number it is: longest-alias matching must not pick a side."""
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    for q in ("Chiefs vs. Dolphins Spread -10.5", "Chiefs vs. Dolphins -10.5", "Chiefs vs. Dolphins Spread +10.5",
              "Chiefs @ Dolphins -10.5", "Dolphins vs. Chiefs -10.5"):
        m = parse_market(_yes_no(q, outcomes=["Chiefs", "Dolphins"], sportsMarketType="spreads"), ev)
        assert math.isnan(spread_line_home(m)), q
    ev2 = {"slug": "nfl-bal-dal-2026-09-27", "title": "Ravens vs. Cowboys"}
    m = parse_market(_yes_no("Ravens vs. Cowboys Spread -3.5", outcomes=["Ravens", "Cowboys"],
                             sportsMarketType="spreads"), ev2)
    assert math.isnan(spread_line_home(m))
    # a separator before the quote isolates the team, and either side's quote gives the same line
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Chiefs -10.5", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Dolphins +10.5", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Chiefs (-10.5) vs. Dolphins (+10.5)", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5  # both sides quoted in one clause: each number's owner is the team before it
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Spread", outcomes=["Chiefs", "Dolphins"],
                             groupItemTitle="Ravens -3.5", sportsMarketType="spreads"), ev)
    assert math.isnan(spread_line_home(m))  # a team that is not in this game is not a quote
    m = parse_market(_yes_no("Chiefs vs. Dolphins Spread: -10.5", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert math.isnan(spread_line_home(m))  # number with no owner in its clause
    # a season label is not a spread quote
    m = parse_market(_yes_no("Spread: Chiefs (-10.5) 2026-27", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5
    m = parse_market(_yes_no("Chiefs vs. Dolphins: Chiefs -10.5.", outcomes=["Chiefs", "Dolphins"],
                             sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5  # sentence-final quote


def test_spread_line_home_nan_on_conflicting_quotes(caplog):
    ev = {"slug": "nfl-kc-mia-2026-09-27", "title": "Chiefs vs. Dolphins"}
    with caplog.at_level("WARNING", logger="nfl_edge.markets.polymarket"):
        m = parse_market(_yes_no("Spread: Chiefs (-10.5)", outcomes=["Chiefs +10.5", "Dolphins -10.5"],
                                 sportsMarketType="spreads"), ev)
        assert math.isnan(spread_line_home(m))
        m = parse_market(_yes_no("Spread: Chiefs (-10.5)", outcomes=["Chiefs", "Dolphins"], line=-3.5,
                                 sportsMarketType="spreads"), ev)
        assert math.isnan(spread_line_home(m))
    assert "conflicting spread quotes" in caplog.text and "disagrees with Gamma line" in caplog.text
    # consistent quotes across all three sources agree
    m = parse_market(_yes_no("Spread: Chiefs (-10.5)", outcomes=["Chiefs -10.5", "Dolphins +10.5"], line=-10.5,
                             groupItemTitle="Chiefs -10.5", sportsMarketType="spreads"), ev)
    assert spread_line_home(m) == -10.5


# ------------------------------------------------------------------------------------ OrderBook
def test_orderbook_sorts_unsorted_input_and_computes_touch():
    raw = {
        "asset_id": "123",
        "bids": [{"price": "0.22", "size": "500"}, {"price": "0.24", "size": "1000"}, {"price": "0.23", "size": "700"}],
        "asks": [{"price": "0.27", "size": "900"}, {"price": "0.25", "size": "800"}, {"price": "0.26", "size": "600"}],
        "hash": "abc", "timestamp": "1758999600123",
    }
    ob = OrderBook.from_clob(raw)
    assert ob.token_id == "123" and ob.timestamp == 1758999600123
    assert [p for p, _ in ob.bids] == [0.24, 0.23, 0.22]
    assert [p for p, _ in ob.asks] == [0.25, 0.26, 0.27]
    assert ob.best_bid == 0.24 and ob.best_ask == 0.25
    assert ob.mid == pytest.approx(0.245) and ob.spread == pytest.approx(0.01)
    # depth within 1c of the touch: ask levels 0.25, 0.26 ; bid levels 0.24, 0.23
    assert ob.depth_usd("ask", 0.01) == pytest.approx(0.25 * 800 + 0.26 * 600)
    assert ob.depth_usd("bid", 0.01) == pytest.approx(0.24 * 1000 + 0.23 * 700)
    assert ob.depth_usd("asks", 0.0) == pytest.approx(0.25 * 800)
    assert ob.depth_usd("bids", 1.0) == pytest.approx(0.24 * 1000 + 0.23 * 700 + 0.22 * 500)
    assert ob.size_usd_at_touch("ask") == pytest.approx(0.25 * 800)
    with pytest.raises(ValueError):
        ob.depth_usd("sideways", 0.01)


def test_orderbook_empty_sides_and_bad_levels():
    ob = OrderBook(bids=[(0.4, 100), (0.5, 0), (1.5, 10), ("x", 5)], asks=[])
    assert ob.bids == [(0.4, 100.0)] and ob.asks == []
    assert ob.best_bid == 0.4 and ob.best_ask is None and ob.mid is None and ob.spread is None
    assert ob.depth_usd("ask", 0.05) == 0.0 and not ob.is_empty
    assert OrderBook().is_empty


def test_fixture_book_unsorted_is_normalised(client, by_slug):
    m = by_slug["nfl-sea-was-2026-09-27-moneyline"]
    raw = json.loads((FIXTURES / f"book_{m.yes_token}.json").read_text())
    raw_bid_prices = [float(x["price"]) for x in raw["bids"]]
    assert raw_bid_prices != sorted(raw_bid_prices, reverse=True), "fixture should be unsorted"
    ob = client.orderbook(m.yes_token)
    assert [p for p, _ in ob.bids] == sorted(p for p, _ in ob.bids)[::-1]
    assert [p for p, _ in ob.asks] == sorted(p for p, _ in ob.asks)
    assert ob.best_bid < ob.best_ask and len(ob.bids) >= 4 and len(ob.asks) >= 4


# --------------------------------------------------------------------------------------- client
def test_client_events_filtering_and_pagination_free(client):
    open_events = client.list_nfl_events()
    all_events = client.list_nfl_events(active=None, closed=None)
    assert len(all_events) == len(open_events) + 1
    closed = [e for e in all_events if e["closed"]]
    assert len(closed) == 1 and closed[0]["slug"] == "nfl-gb-nyj-2026-09-20"
    assert client.list_nfl_events(tag_slug="nba") == []
    assert all(any(t["slug"] == "nfl" for t in e["tags"]) for e in open_events)


def test_enrich_with_books_attaches_yes_book(client, markets, caplog):
    fresh = client.markets_from_events(client.list_nfl_events())
    with caplog.at_level("WARNING", logger="nfl_edge.markets.polymarket"):
        out = client.enrich_with_books(fresh)
    assert len(out) == len(fresh) and all(a is b for a, b in zip(out, fresh))  # mutated in place
    with_book = [m for m in out if m.book is not None]
    assert len(with_book) >= 4
    ml = next(m for m in with_book if m.slug == "nfl-kc-mia-2026-09-27-moneyline")
    assert ml.book.token_id == ml.yes_token and ml.book.best_ask > ml.book.best_bid
    assert abs(ml.book.mid - ml.price_yes) < 0.03
    assert "no order book" in caplog.text  # missing books are reported, not swallowed
    with pytest.raises(PolymarketError):
        client.enrich_with_books(fresh, strict=True)


def test_price_history_fixture_and_missing(client, by_slug):
    m = by_slug["nfl-kc-mia-2026-09-27-moneyline"]
    df = client.price_history(m.yes_token)
    assert list(df.columns) == ["t", "price", "ts", "token_id"]
    assert len(df) == 144 and df["t"].is_monotonic_increasing
    assert df["price"].between(0.05, 0.4).all() and str(df["ts"].dt.tz) == "UTC"
    empty = client.price_history("0")
    assert empty.empty and list(empty.columns) == ["t", "price", "ts", "token_id"]


def test_client_without_fixture_dir_hits_network_block():
    """Proves tests are offline: the conftest block fires on the first real request."""
    c = PolymarketClient()
    with pytest.raises(RuntimeError, match="network access is disabled"):
        c.list_nfl_events()
    with pytest.raises(RuntimeError, match="network access is disabled"):
        c.orderbook("1")
    with pytest.raises(RuntimeError, match="network access is disabled"):
        c.price_history("1")


class _FakeResponse:
    def __init__(self, status: int, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else "error body"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    """Scripted responses; records calls. Not a requests.Session, so the conftest block does not apply."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_client_retries_on_429_and_5xx_then_succeeds(monkeypatch):
    import requests as _rq

    sleeps: list[float] = []
    monkeypatch.setattr(pm.time, "sleep", lambda s: sleeps.append(s))
    sess = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "2"}), _FakeResponse(503),
        _rq.ConnectionError("boom"), _FakeResponse(200, {"bids": [], "asks": [], "asset_id": "7"}),
    ])
    c = PolymarketClient(session=sess, backoff=0.5, max_retries=3)
    ob = c.orderbook("7")
    assert ob.is_empty and ob.token_id == "7"
    assert len(sess.calls) == 4 and sess.calls[0][1] == {"token_id": "7"}
    assert sleeps == [2.0, 1.0, 2.0]  # Retry-After honoured, then 0.5*2**1, 0.5*2**2


def test_client_raises_clear_error_after_retries_exhausted(monkeypatch):
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    sess = _FakeSession([_FakeResponse(500)] * 4)
    c = PolymarketClient(session=sess, max_retries=3)
    with pytest.raises(PolymarketError, match="failed after 4 attempts.*HTTP 500"):
        c.orderbook("7")
    sess = _FakeSession([_FakeResponse(404)])
    with pytest.raises(PolymarketError, match="HTTP 404"):
        PolymarketClient(session=sess).orderbook("7")
    assert len(sess.calls) == 1  # 4xx (non-429) is not retried
    sess = _FakeSession([_FakeResponse(200, None)])
    with pytest.raises(PolymarketError, match="non-JSON"):
        PolymarketClient(session=sess).orderbook("7")


def test_client_paginates_events_by_offset():
    page1 = [{"id": str(i), "slug": f"e{i}", "markets": []} for i in range(3)]
    page2 = [{"id": "2", "slug": "e2", "markets": []}, {"id": "3", "slug": "e3", "markets": []}]
    sess = _FakeSession([_FakeResponse(200, page1), _FakeResponse(200, page2)])
    c = PolymarketClient(session=sess)
    evs = c.list_nfl_events(limit=3)
    assert [e["id"] for e in evs] == ["0", "1", "2", "3"]  # de-duplicated across pages
    assert [p["offset"] for _, p in sess.calls] == [0, 3]
    assert sess.calls[0][1]["tag_slug"] == "nfl" and sess.calls[0][1]["active"] == "true"
    assert sess.calls[0][1]["closed"] == "false" and sess.calls[0][0].endswith("/events")


def test_client_rejects_missing_fixture_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        PolymarketClient(fixture_dir=tmp_path / "nope")


# ------------------------------------------------------------------------------ match_game_markets
def test_match_game_markets_on_real_schedule(markets, games):
    df = match_game_markets(markets, games)
    assert list(df.columns) == pm.MATCH_COLUMNS
    assert set(df["game_id"]) == FIXTURE_GAMES
    assert len(df) == 12 and df["market_id"].is_unique
    assert Counter(df["kind"]) == {"moneyline": 4, "spread": 4, "total": 4}
    assert (df["date_diff_days"] == 0).all() and df["home_matches_schedule"].all()
    assert df["home_away_confident"].all()
    # schedule truth: home/away codes come from nflverse rows
    kc = df[df["game_id"] == "2026_03_KC_MIA"].set_index("kind")
    assert kc.loc["moneyline", "home_team_c"] == "MIA" and kc.loc["moneyline", "away_team_c"] == "KC"
    assert kc.loc["moneyline", "yes_team"] == "MIA" and kc.loc["moneyline", "price_yes"] == 0.14
    assert kc.loc["spread", "line"] == -10.5 and kc.loc["spread", "line_home"] == kc.loc["spread", "spread_line"]
    assert kc.loc["total", "line"] == 45.5 and kc.loc["total", "total_line"] == 45.5
    assert pd.isna(kc.loc["total", "yes_team"]) and kc.loc["total", "yes_outcome"] == "Over"
    assert (kc["season"] == 2026).all() and (kc["week"] == 3).all() and (~kc["played"].astype(bool)).all()
    # every fixture spread agrees with the nflverse closing spread convention
    sp = df[df["kind"] == "spread"]
    assert (sp["line_home"] == sp["spread_line"]).all()
    assert (sp["line_market_home"] == sp["line_home"]).all()  # market home == schedule home: no flip
    assert df.loc[df["kind"] != "spread", ["line_home", "line_market_home"]].isna().all().all()
    # the neutral-site BAL@DAL game still matches on the unordered pair
    assert "2026_03_BAL_DAL" in set(sp["game_id"])


def test_match_game_markets_date_window_and_swapped_home(games):
    ev = {"slug": "nfl-mia-kc-2026-09-27", "title": "Dolphins vs. Chiefs"}   # home/away swapped vs schedule
    m = parse_market(_yes_no("Dolphins vs. Chiefs", outcomes='["Dolphins", "Chiefs"]', sportsMarketType="moneyline"), ev)
    df = match_game_markets([m], games)
    assert len(df) == 1 and df.loc[0, "game_id"] == "2026_03_KC_MIA"
    assert df.loc[0, "home_team_c"] == "MIA" and not df.loc[0, "home_matches_schedule"]
    assert df.loc[0, "yes_team"] == "KC"  # YES still refers to the market's (wrong) home outcome
    sp = parse_market(_yes_no("Spread: Chiefs (-10.5)", id="4", outcomes='["Chiefs", "Dolphins"]', line=-10.5,
                              sportsMarketType="spreads"), ev)
    assert spread_line_home(sp) == 10.5  # market frame: the market (wrongly) lists KC as home
    row = match_game_markets([sp], games).iloc[0]
    assert row["game_id"] == "2026_03_KC_MIA" and row["home_team_c"] == "MIA" and not row["home_matches_schedule"]
    assert row["line_market_home"] == 10.5
    assert row["line_home"] == row["spread_line"] == -10.5  # schedule frame: comparable to spread_line in the same row
    assert row["yes_team"] == "KC" and row["yes_outcome"] == "Chiefs"  # a code: frame-independent
    far = {"slug": "nfl-kc-mia-2026-10-15", "title": "Chiefs vs. Dolphins"}
    m2 = parse_market(_yes_no("Chiefs vs. Dolphins", id="2", outcomes='["Chiefs", "Dolphins"]',
                              sportsMarketType="moneyline"), far)
    assert match_game_markets([m2], games).empty
    near = {"slug": "nfl-kc-mia-2026-09-25", "title": "Chiefs vs. Dolphins"}   # 2 days early: within +-3
    m3 = parse_market(_yes_no("Chiefs vs. Dolphins", id="3", outcomes='["Chiefs", "Dolphins"]',
                              sportsMarketType="moneyline"), near)
    out = match_game_markets([m3], games)
    assert len(out) == 1 and out.loc[0, "date_diff_days"] == 2


def test_match_game_markets_ignores_futures_and_empty(markets, games):
    fut = [m for m in markets if m.kind == "futures"]
    assert match_game_markets(fut, games).empty
    empty = match_game_markets([], games)
    assert empty.empty and list(empty.columns) == pm.MATCH_COLUMNS


# ----------------------------------------------------------------------------------------- fees
def test_fee_for_market(by_slug):
    m = by_slug["nfl-kc-mia-2026-09-27-moneyline"]
    assert fee_for_market(m) == 0.0 and fee_for_market(m, default=0.02) == 0.02
    base = m.raw
    mk = lambda **kw: PolyMarket(**{**m.__dict__, "raw": {**base, **kw}})  # noqa: E731
    assert fee_for_market(mk(feeRateBps="200")) == pytest.approx(0.02)
    assert fee_for_market(mk(takerFee=0.015)) == pytest.approx(0.015)
    assert fee_for_market(mk(fee="0.01")) == pytest.approx(0.01)
    assert fee_for_market(mk(feeRateBps=None, fee=0.03)) == pytest.approx(0.03)
    assert fee_for_market(mk(fee=5.0), default=0.001) == 0.001  # nonsense ignored


# ------------------------------------------------------------------------------------ snapshots
def test_snapshot_load_roundtrip(client, tmp_path):
    ms = client.enrich_with_books(client.markets_from_events(client.list_nfl_events()))
    path = tmp_path / "snap" / "2026-09-25.jsonl"
    n1 = snapshot(ms, path, ts="2026-09-25T12:00:00Z")
    n2 = snapshot(ms, path, ts="2026-09-25T13:00:00Z")
    assert n1 == n2 == sum(len(m.outcomes) for m in ms) == 2 * len(ms)
    df = load_snapshots(path)
    assert list(df.columns) == pm.SNAPSHOT_COLUMNS and len(df) == n1 + n2
    assert str(df["ts"].dt.tz) == "UTC" and df["ts"].nunique() == 2
    assert pd.api.types.is_string_dtype(df["market_id"]) and df["outcome_index"].dtype.kind == "i"
    ml = next(m for m in ms if m.slug == "nfl-kc-mia-2026-09-27-moneyline")
    rows = df[(df["market_id"] == ml.market_id) & (df["ts"] == pd.Timestamp("2026-09-25T12:00:00Z"))]
    assert len(rows) == 2
    yes = rows[rows["is_yes"]].iloc[0]
    no = rows[~rows["is_yes"]].iloc[0]
    assert yes["outcome"] == "Dolphins" and yes["token_id"] == ml.yes_token and yes["price"] == 0.14
    assert yes["best_bid"] == ml.book.best_bid and yes["best_ask"] == ml.book.best_ask
    assert yes["mid"] == pytest.approx(ml.book.mid) and yes["kind"] == "moneyline"
    assert no["best_bid"] == pytest.approx(1 - ml.book.best_ask) and no["best_ask"] == pytest.approx(1 - ml.book.best_bid)
    assert yes["liquidity"] == ml.liquidity and yes["end_date"] == ml.end_date and yes["condition_id"] == ml.condition_id
    # markets without a book carry nulls, not crashes
    nobook = df[df["best_bid"].isna()]
    assert not nobook.empty and nobook["price"].notna().all()


def test_load_snapshots_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_snapshots(tmp_path / "missing.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"ts": "2026-09-25T12:00:00Z", "market_id": "1"}\nnot json\n')
    with pytest.raises(ValueError, match="malformed JSONL"):
        load_snapshots(bad)
    (tmp_path / "empty.jsonl").write_text("")
    df = load_snapshots(tmp_path / "empty.jsonl")
    assert df.empty and list(df.columns) == pm.SNAPSHOT_COLUMNS
