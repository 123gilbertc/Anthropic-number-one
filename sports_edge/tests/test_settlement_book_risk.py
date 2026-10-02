from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sports_edge.contracts.settlement import FinalResult, MappingError, resolve, validate_mapping
from sports_edge.domain.enums import Outcome, Reason, SettlementRule, SourceStatus, Sport, Venue
from sports_edge.domain.records import Game, MarketMapping
from sports_edge.ingest.orderbook import BookGap, LocalBook
from sports_edge.risk.exposure import ExposureLedger, RiskLimits

D = Decimal
T0 = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)


def nhl_game():
    return Game(game_id="G1", sport=Sport.NHL, season="2026-27", home_team="BOS",
                away_team="TOR", scheduled_start=T0, status="LIVE", source="fixture")


def mapping(rule=SettlementRule.NHL_INCLUDING_OT_SO, team="BOS", **kw):
    return MarketMapping(mapping_id="m", game_id="G1", venue=Venue.KALSHI, contract_id="C-BOS",
                         selection_team=team, settlement_rule=rule, rules_text_hash=None,
                         tick_size=D("0.01"), verified_by="test", **kw)


def test_mapping_rejects_regulation_contract_for_full_game_forecast():
    with pytest.raises(MappingError):
        validate_mapping(mapping(SettlementRule.NHL_REGULATION_ONLY), nhl_game(),
                         SettlementRule.NHL_INCLUDING_OT_SO)


def test_mapping_rejects_unknown_team_and_wrong_sport_rule():
    with pytest.raises(MappingError):
        validate_mapping(mapping(team="NYR"), nhl_game(), SettlementRule.NHL_INCLUDING_OT_SO)
    with pytest.raises(MappingError):
        validate_mapping(mapping(SettlementRule.MLB_FULL_GAME_INCL_EXTRAS), nhl_game(),
                         SettlementRule.MLB_FULL_GAME_INCL_EXTRAS)


def test_nhl_ot_win_settles_differently_by_rule():
    res = FinalResult(home_score=3, away_score=2, status="FINAL", nhl_decided_in="OT",
                      nhl_regulation_home=2, nhl_regulation_away=2)
    assert resolve(mapping(), nhl_game(), res)[0] == Outcome.WIN
    assert resolve(mapping(SettlementRule.NHL_REGULATION_ONLY), nhl_game(), res)[0] == Outcome.LOSS


def test_postponed_is_void_and_suspended_pending():
    assert resolve(mapping(), nhl_game(), FinalResult(0, 0, "POSTPONED"))[0] == Outcome.VOID
    assert resolve(mapping(), nhl_game(), FinalResult(1, 1, "SUSPENDED"))[0] == Outcome.PENDING


def test_mlb_listed_pitcher_void_and_extras():
    g = Game(game_id="G1", sport=Sport.MLB, season="2026", home_team="NYY", away_team="BOS",
             scheduled_start=T0, status="FINAL", doubleheader_game_number=2, source="fixture")
    m = MarketMapping(mapping_id="m", game_id="G1", venue=Venue.KALSHI, contract_id="C",
                      selection_team="NYY", settlement_rule=SettlementRule.MLB_LISTED_PITCHERS,
                      rules_text_hash=None, tick_size=D("0.01"), verified_by="t",
                      listed_pitchers=("Cole", "Bello"))
    scratched = FinalResult(5, 4, "FINAL", mlb_actual_starters=("Rodon", "Bello"),
                            mlb_listed_starters=("Cole", "Bello"))
    assert resolve(m, g, scratched)[0] == Outcome.VOID
    extras = FinalResult(5, 4, "FINAL", mlb_actual_starters=("Cole", "Bello"),
                         mlb_listed_starters=("Cole", "Bello"))
    assert resolve(m, g, extras)[0] == Outcome.WIN


# ----------------------------------------------------------------- order book


def book():
    b = LocalBook(venue=Venue.KALSHI, contract_id="C", source_status=SourceStatus.REPLAY)
    b.apply_snapshot(10, yes=[(D("0.44"), 50)], no=[(D("0.54"), 40), (D("0.53"), 20)],
                     received=T0)
    return b


def test_kalshi_asks_derived_from_no_bids():
    snap = book().snapshot()
    assert snap.best_ask == D("0.46") and snap.best_bid == D("0.44")
    assert [a.price for a in snap.asks] == [D("0.46"), D("0.47")]


def test_duplicate_delta_ignored_and_gap_invalidates():
    b = book()
    b.apply_delta(11, "no", D("0.54"), -10, received=T0)
    b.apply_delta(11, "no", D("0.54"), -10, received=T0)  # duplicate
    assert b.no_bids[D("0.54")] == 30 and b.duplicates_ignored == 1
    with pytest.raises(BookGap):
        b.apply_delta(13, "no", D("0.54"), -5, received=T0)
    assert not b.valid and not b.snapshot().valid
    # resync from a fresh snapshot restores validity
    b.apply_snapshot(20, yes=[], no=[(D("0.50"), 5)], received=T0)
    assert b.valid and b.snapshot().best_ask == D("0.50")


def test_negative_size_invalidates():
    b = book()
    with pytest.raises(BookGap):
        b.apply_delta(11, "no", D("0.54"), -100, received=T0)
    assert not b.valid


# ----------------------------------------------------------------- exposure


def test_team_cap_includes_initial_purchase_and_fees():
    led = ExposureLedger(RiskLimits.example_1000_bankroll_100_cap())
    day = date(2026, 10, 10)
    led.record_purchase("G1", "BOS", day, D("60"), D("1"))
    room, _ = led.remaining("G1", "BOS", day)
    assert room == D("39")  # not $100 more per dip
    led.record_purchase("G1", "BOS", day, D("38.5"), D("0.5"))
    room, binding = led.remaining("G1", "BOS", day)
    assert room == 0 and Reason.TEAM_CAP_REACHED in binding
    with pytest.raises(ValueError):
        led.record_purchase("G1", "BOS", day, D("1"), D("0"))


def test_unsettled_proceeds_cannot_fund_purchases():
    lim = RiskLimits(bankroll=D("100"), per_team_cap=D("100"), per_game_cap=D("100"),
                     portfolio_cap=D("1000"), daily_spend_cap=D("1000"), cash_reserve=D("0"))
    led = ExposureLedger(lim)
    day = date(2026, 10, 10)
    led.record_purchase("G1", "BOS", day, D("100"), D("0"))
    room, binding = led.remaining("G2", "NYR", day)
    assert room == 0 and Reason.INSUFFICIENT_CASH in binding
    led.record_settlement("G1", "BOS", payout=D("180"))
    assert led.remaining("G2", "NYR", day)[0] == D("100")
