"""Shared builders for engine tests. Test doubles are explicit and named as such."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sports_edge.domain.enums import ModelStatus, SettlementRule, SourceStatus, Sport, Venue
from sports_edge.domain.records import (
    BookLevel,
    Game,
    MarketMapping,
    NHLState,
    OrderBookSnapshot,
    Prediction,
    SportsbookQuote,
    stable_id,
)
from sports_edge.pricing.fees import KalshiQuadraticFee
from sports_edge.risk.exposure import ExposureLedger, RiskLimits
from sports_edge.triggers.engine import TriggerContext, TriggerEngine
from sports_edge.triggers.strategy import StrategyConfig

D = Decimal
NOW = datetime(2026, 10, 10, 23, 30, tzinfo=UTC)
RULE = SettlementRule.NHL_INCLUDING_OT_SO


def game() -> Game:
    return Game(game_id="G1", sport=Sport.NHL, season="2026-27", home_team="BOS",
                away_team="TOR", scheduled_start=NOW - timedelta(minutes=30), status="LIVE",
                source="test")


def mapping(rule=RULE) -> MarketMapping:
    return MarketMapping(mapping_id="m1", game_id="G1", venue=Venue.KALSHI,
                         contract_id="K-BOS", selection_team="BOS", settlement_rule=rule,
                         rules_text_hash="abc", tick_size=D("0.01"), verified_by="test")


def state(*, home=0, away=1, material_ago=120, received_ago=1, pending=(), goalie="Swayman",
          status=SourceStatus.REPLAY) -> NHLState:
    body = dict(game_id="G1", source="test", source_status=status,
                as_of_event_time=NOW - timedelta(seconds=received_ago + 2),
                as_of_received_time=NOW - timedelta(seconds=received_ago),
                last_material_event_time=NOW - timedelta(seconds=material_ago),
                last_material_event_kind="GOAL", applied_seq=10, period=2,
                seconds_remaining_in_period=600, home_score=home, away_score=away,
                home_skaters=5, away_skaters=5, home_goalie=goalie, away_goalie="Woll",
                home_net_empty=False, away_net_empty=False, pending_reconciliation=pending)
    return NHLState(snapshot_id=stable_id("nhl", body), **body)


def book(ask="0.45", depth=400, bid=None, status=SourceStatus.REPLAY, age=1) -> OrderBookSnapshot:
    a = D(ask)
    b = D(bid) if bid else a - D("0.02")
    return OrderBookSnapshot(
        book_id=f"b-{ask}", venue=Venue.KALSHI, contract_id="K-BOS", source_status=status,
        received_time=NOW - timedelta(seconds=age), exchange_time=None, seq=5, valid=True,
        market_open=True, bids=(BookLevel(price=b, quantity=depth),),
        asks=(BookLevel(price=a, quantity=depth), BookLevel(price=a + D("0.01"), quantity=depth)))


def prediction(st: NHLState, p: float, lo: float | None = None,
               status=ModelStatus.VALIDATED) -> Prediction:
    """TEST DOUBLE: a hypothetical calibrated estimate injected directly."""
    lo = p - 0.03 if lo is None else lo
    return Prediction(prediction_id=f"p-{p}", game_id="G1", snapshot_id=st.snapshot_id,
                      selection_team="BOS", settlement_rule=RULE, model_version="TEST_DOUBLE",
                      model_status=status, feature_version="nhl_v1", probability=p,
                      probability_low=lo, probability_high=min(1.0, p + 0.03),
                      reliability="MEDIUM", created_time=NOW, valid_until=NOW + timedelta(seconds=60))


def quote(updated_ago: int, received_ago: int = 1, home=-120, away=105) -> SportsbookQuote:
    return SportsbookQuote(quote_id=f"q{updated_ago}", game_id="G1", book="pinnacle",
                           market="h2h", settlement_rule=RULE,
                           prices_american={"BOS": home, "TOR": away},
                           provider_last_update=NOW - timedelta(seconds=updated_ago),
                           received_time=NOW - timedelta(seconds=received_ago), source="test")


def engine(cfg: StrategyConfig | None = None, limits: RiskLimits | None = None) -> TriggerEngine:
    cfg = cfg or StrategyConfig(strategy_version="test_any_edge", entry_mode="any_edge",
                                forecast_rule=RULE, reference_disagreement_pp=100)
    return TriggerEngine(cfg, KalshiQuadraticFee(),
                         ExposureLedger(limits or RiskLimits.example_1000_bankroll_100_cap()))


def ctx(st, bk, pred, **kw) -> TriggerContext:
    return TriggerContext(game=game(), mapping=kw.pop("mapping", mapping()), state=st, book=bk,
                          prediction=pred, game_last_seen=NOW, market_last_seen=NOW, **kw)
