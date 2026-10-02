import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from helpers import NOW, RULE, book, ctx, engine, game, mapping, prediction, state

from sports_edge.adapters.kalshi import KalshiBookManager, KalshiParseError, ReconnectingFeed
from sports_edge.adapters.odds_api import parse_h2h
from sports_edge.clock import ReplayClock
from sports_edge.domain.enums import Action, ModelStatus, Reason, SettlementRule, SourceStatus
from sports_edge.domain.records import BookLevel
from sports_edge.evaluation.splits import (
    ExperimentLog,
    LeakageError,
    Split,
    assert_no_leakage,
    chronological_split,
)
from sports_edge.features.nhl import build_features
from sports_edge.forecast.synthetic import simulate_season
from sports_edge.forecast.train import train_nhl
from sports_edge.ingest.nhl_state import InvalidEvent, NHLStateReducer, NormalizedGameEvent
from sports_edge.llm.review import EvidenceBundle, EvidenceItem, ShadowReviewer
from sports_edge.paper.broker import PaperBroker
from sports_edge.replay.runner import replay_file
from sports_edge.triggers.strategy import StrategyConfig, provisional_dip_strategy

D = Decimal
FIXTURE = Path(__file__).parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"
T = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)


# ------------------------------------------------------------------ trigger engine


def test_dip_alone_cannot_approve():
    cfg = replace(provisional_dip_strategy(), dip_persistence=timedelta(0))
    eng = engine(cfg)
    st = state()
    # big drop from anchor but the model agrees the team is worse: no buy
    c = ctx(st, book("0.40"), prediction(st, 0.38, lo=0.35), anchor_price=D("0.60"))
    d = eng.evaluate(c, NOW)
    assert Reason.DIP_DETECTED in d.reasons and d.action == Action.NO_ADD


def test_dip_requires_persistence_then_edge():
    cfg = provisional_dip_strategy()
    eng = engine(cfg)
    st = state()
    eng.observe_price("K-BOS", NOW - timedelta(seconds=5), D("0.45"))
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57), anchor_price=D("0.60"))
    assert eng.evaluate(c, NOW).action == Action.CANDIDATE
    later = NOW + timedelta(seconds=25)
    st2 = state()
    c2 = replace(c, book=book("0.45").model_copy(update={"received_time": later}),
                 prediction=prediction(st2, 0.60, lo=0.57).model_copy(
                     update={"valid_until": later + timedelta(seconds=30)}),
                 game_last_seen=later, market_last_seen=later)
    assert eng.evaluate(c2, later).action == Action.PAPER_ENTRY


def test_settlement_mismatch_blocks():
    st = state()
    d = engine().evaluate(ctx(st, book(), prediction(st, 0.7),
                              mapping=mapping(SettlementRule.NHL_REGULATION_ONLY)), NOW)
    assert d.action == Action.DATA_BLOCKED and Reason.SETTLEMENT_MISMATCH in d.reasons


def test_missing_goalie_blocks_adds():
    st = state(pending=("GOALIE_UNKNOWN_BOS",), goalie=None)
    d = engine().evaluate(ctx(st, book(), prediction(st, 0.7)), NOW)
    assert d.action == Action.DATA_BLOCKED and Reason.PENDING_RECONCILIATION in d.reasons


def test_unvalidated_model_never_alerts():
    st = state()
    d = engine().evaluate(ctx(st, book("0.30"), prediction(st, 0.9, status=ModelStatus.UNVALIDATED)),
                          NOW)
    assert d.action == Action.WATCH and Reason.MODEL_NOT_VALIDATED in d.reasons


def test_prediction_for_old_snapshot_is_stale():
    st, old = state(away=1), state(away=0)
    d = engine().evaluate(ctx(st, book(), prediction(old, 0.7)), NOW)
    assert d.action == Action.WATCH and Reason.PREDICTION_STALE in d.reasons


def test_market_settling_after_goal():
    st = state(material_ago=5)
    d = engine().evaluate(ctx(st, book(), prediction(st, 0.7)), NOW)
    assert Reason.MARKET_SETTLING in d.reasons


def test_wide_spread_and_thin_depth():
    st = state()
    d = engine().evaluate(ctx(st, book("0.45", bid="0.30"), prediction(st, 0.7)), NOW)
    assert Reason.SPREAD_TOO_WIDE in d.reasons
    d2 = engine().evaluate(ctx(st, book("0.45", depth=1), prediction(st, 0.7)), NOW)
    assert Reason.DEPTH_INSUFFICIENT in d2.reasons  # haircut leaves 0 of 1 displayed


def test_duplicate_alert_and_cooldown():
    eng = engine()
    st = state()
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    d = eng.evaluate(c, NOW)
    assert d.action == Action.PAPER_ENTRY
    # evaluation alone is side-effect free: a preview does not consume the signal
    assert eng.evaluate(c, NOW).action == Action.PAPER_ENTRY
    eng.note_submitted(d)
    second = eng.evaluate(c, NOW + timedelta(seconds=1))
    assert second.action == Action.HOLD and Reason.COOLDOWN in second.reasons


def test_partial_fill_and_recheck():
    eng = engine()
    broker = PaperBroker(eng)
    st = state()
    c = ctx(st, book("0.45", depth=400), prediction(st, 0.60, lo=0.57))
    d = eng.evaluate(c, NOW)
    broker.submit(d, st)
    later = NOW + timedelta(seconds=3)
    thin = book("0.45", depth=20).model_copy(update={"received_time": later})
    fresh = replace(c, book=thin, game_last_seen=later, market_last_seen=later,
                    prediction=prediction(st, 0.60, lo=0.57))
    [att] = broker.process(later, lambda _d: fresh)
    # 50% haircut of 20 displayed at 0.45 and at 0.46 (limit = modeled avg + 1 tick)
    assert att.filled is not None and att.filled.filled_quantity == 20
    assert att.filled.requested_quantity > 20 and Reason.PARTIAL_FILL in att.reasons
    pos = broker.positions[("G1", "K-BOS")]
    assert pos.average_entry == D("0.455")  # quantity-weighted, not an average of quotes
    assert eng.ledger.cash == D("1000") - att.filled.cost - att.filled.fees


def test_fill_rejected_if_state_changed_before_fill():
    eng = engine()
    broker = PaperBroker(eng)
    st = state()
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    broker.submit(eng.evaluate(c, NOW), st)
    st2 = state(away=2)  # TOR scored during the delay
    [att] = broker.process(NOW + timedelta(seconds=3), lambda _d: replace(c, state=st2))
    assert att.filled is None and Reason.FILL_RECHECK_FAILED in att.reasons


def test_fill_rejected_after_expiry():
    eng = engine()
    broker = PaperBroker(eng)
    st = state()
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    broker.submit(eng.evaluate(c, NOW), st)
    [att] = broker.process(NOW + timedelta(minutes=5), lambda _d: c)
    assert att.filled is None


# ------------------------------------------------------------------ state reducer


def ev(typ, seq, data=None, eid=None, t=0):
    return NormalizedGameEvent(game_id="G1", source="t", source_status=SourceStatus.REPLAY,
                               type=typ, provider_event_id=eid or f"e{seq}", seq=seq,
                               event_time=T + timedelta(seconds=t), published_time=None,
                               received_time=T + timedelta(seconds=t + 1), data=data or {})


def reducer():
    r = NHLStateReducer("G1", "BOS", "TOR")
    r.apply(ev("SNAPSHOT", 1, {"period": 1, "seconds_remaining": 1200, "home_score": 0,
                               "away_score": 0, "home_goalie": "A", "away_goalie": "B"}))
    return r


def test_duplicate_and_correction():
    r = reducer()
    r.apply(ev("GOAL", 2, {"team": "TOR"}))
    res = r.apply(ev("GOAL", 2, {"team": "TOR"}))
    assert not res.applied and r.duplicates == 1 and r.state.away_score == 1
    res = r.apply(ev("GOAL", 2, {"team": "BOS"}, eid="e2"))  # same id, new content
    assert "CORRECTION_UNRECONCILED" in res.state.pending_reconciliation
    r.apply(ev("SNAPSHOT", 3, {"home_score": 1, "away_score": 0}))
    assert r.state.pending_reconciliation == () and r.state.home_score == 1


def test_out_of_order_and_gap_flagged():
    r = reducer()
    r.apply(ev("CLOCK", 3, {"seconds_remaining": 1100}))
    assert "FEED_GAP" in r.state.pending_reconciliation
    res = r.apply(ev("GOAL", 2, {"team": "TOR"}))
    assert not res.applied and "OUT_OF_ORDER" in res.state.pending_reconciliation
    assert r.state.away_score == 0  # late event not applied blindly


def test_invalid_events_rejected():
    r = reducer()
    with pytest.raises(InvalidEvent):
        r.apply(ev("GOAL", 2, {}))
    with pytest.raises(InvalidEvent):
        r.apply(ev("CLOCK", 2, {"seconds_remaining": 5000}))


def test_goalie_unknown_then_identified():
    r = reducer()
    r.apply(ev("GOALIE_CHANGE", 2, {"team": "TOR", "goalie": None}))
    assert "GOALIE_UNKNOWN_TOR" in r.state.pending_reconciliation and r.state.away_goalie is None
    r.apply(ev("GOALIE_CHANGE", 3, {"team": "TOR", "goalie": "C"}))
    assert r.state.pending_reconciliation == ()


def test_features_mark_missing_not_invent():
    r = reducer()
    s = r.state.model_copy(update={"home_skaters": None})
    fv = build_features(s, True, None)
    assert "manpower_diff" in fv.missing and "pregame_logit" in fv.missing
    assert fv.values["manpower_diff"] is None


# ------------------------------------------------------------------ kalshi + transport


def snap_msg(seq, yes, no, sid=1, t="K"):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "yes_dollars_fp": yes, "no_dollars_fp": no}}


def delta_msg(seq, side, price, delta, sid=1, t="K"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": price, "delta_fp": delta, "side": side}}


def test_kalshi_sid_gap_invalidates_and_resync():
    m = KalshiBookManager()
    m.handle(snap_msg(1, [["0.40", "10.00"]], [["0.55", "10.00"]]), T)
    m.handle(delta_msg(2, "no", "0.55", "-5.00"), T)
    assert m.books["K"].snapshot().asks[0] == BookLevel(price=D("0.45"), quantity=5)
    m.handle(delta_msg(2, "no", "0.55", "-5.00"), T)  # duplicate
    assert m.duplicates == 1 and m.books["K"].no_bids[D("0.55")] == 5
    m.handle(delta_msg(4, "no", "0.55", "-1.00"), T)  # gap
    assert not m.books["K"].valid and "K" in m.needs_resync
    m.handle(snap_msg(5, [], [["0.50", "3.00"]]), T)
    assert m.books["K"].valid and "K" not in m.needs_resync


def test_kalshi_rejects_malformed():
    m = KalshiBookManager()
    with pytest.raises(KalshiParseError):
        m.handle(snap_msg(1, [["1.40", "10"]], []), T)
    with pytest.raises(KalshiParseError):
        m.handle({"type": "orderbook_delta", "sid": 1, "seq": 1, "msg": {}}, T)


class FakeConn:
    def __init__(self, msgs, fail_after=None):
        self.msgs, self.sent, self.closed = list(msgs), [], False

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def recv(self):
        if not self.msgs:
            raise ConnectionError("dropped")
        return json.dumps(self.msgs.pop(0))

    async def close(self):
        self.closed = True


def test_reconnect_resubscribes_and_invalidates():
    made = [FakeConn([{"type": "a"}]), FakeConn([{"type": "b"}])]
    conns = list(made)
    disconnects = []

    async def connect():
        return conns.pop(0)

    async def nosleep(_):
        return None

    async def run():
        feed = ReconnectingFeed(connect, lambda: [{"cmd": "subscribe"}],
                                lambda: disconnects.append(1), sleep=nosleep, max_attempts=2)
        got = []
        async for m in feed.messages():
            got.append(m["type"])
            if len(got) == 2:
                break
        return got, feed

    got, feed = asyncio.run(run())
    # one drop mid-stream + one clean close when the consumer stops
    assert got == ["a", "b"] and len(disconnects) == 2
    # every (re)connection resubscribed before reading
    assert made[0].sent == made[1].sent == [{"cmd": "subscribe"}]
    assert made[0].closed and made[1].closed


# ------------------------------------------------------------------ odds api


def test_odds_parse_maps_and_rejects_incomplete():
    events = [{"home_team": "Boston Bruins", "away_team": "Toronto Maple Leafs",
               "bookmakers": [
                   {"key": "pinnacle", "last_update": "2026-10-10T23:00:00Z", "markets": [
                       {"key": "h2h", "last_update": "2026-10-10T23:00:05Z", "outcomes": [
                           {"name": "Boston Bruins", "price": -130},
                           {"name": "Toronto Maple Leafs", "price": 115}]}]},
                   {"key": "other", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "Boston Bruins", "price": -130}]}]}]}]
    codes = {"Boston Bruins": "BOS", "Toronto Maple Leafs": "TOR"}
    qs = parse_h2h(events, codes, {("BOS", "TOR"): "G1"}, T, RULE)
    assert len(qs) == 1 and qs[0].prices_american == {"BOS": -130, "TOR": 115}
    assert qs[0].provider_last_update == datetime(2026, 10, 10, 23, 0, 5, tzinfo=UTC)


# ------------------------------------------------------------------ LLM shadow


class SlowProvider:
    name, model_id = "slow", "slow-1"

    async def complete(self, system, user, timeout_s):
        await asyncio.sleep(5)
        return "{}", D("0")


class BadProvider:
    name, model_id = "bad", "bad-1"

    async def complete(self, system, user, timeout_s):
        return '{"verdict": "BUY NOW"}', D("0.001")


class InventingProvider:
    name, model_id = "inv", "inv-1"

    def __init__(self, snap):
        self.snap = snap

    async def complete(self, system, user, timeout_s):
        return json.dumps({"snapshot_id": self.snap, "evidence_ids": ["injury_report_999"],
                           "supported_concerns": ["star injured"], "missing_information": [],
                           "abstain": False, "reasoning": "x"}), D("0.001")


def bundle(st):
    return EvidenceBundle(bundle_id="b1", decision_id="d1", snapshot_id=st.snapshot_id,
                          created_time=NOW, items=(EvidenceItem(evidence_id="state", kind="state",
                                                                as_of=NOW, content={}),))


def test_llm_timeout_schema_and_invented_evidence():
    st = state()
    rv = ShadowReviewer([SlowProvider(), BadProvider(), InventingProvider(st.snapshot_id)],
                        timeout_s=0.05)
    recs = asyncio.run(rv.review(bundle(st), lambda: st.snapshot_id, lambda: NOW))
    assert [r.status for r in recs] == ["TIMEOUT", "INVALID_SCHEMA", "INVALID_EVIDENCE"]
    assert all(r.decision_weight == 0.0 for r in recs)


def test_llm_budget_exhausted():
    st = state()
    rv = ShadowReviewer([BadProvider()], monthly_budget_usd=D("0"))
    [r] = asyncio.run(rv.review(bundle(st), lambda: st.snapshot_id, lambda: NOW))
    assert r.status == "ERROR" and r.output == {"error": "budget exhausted"}


# ------------------------------------------------------------------ chronology / leakage


def test_chronological_split_no_leakage_and_holdout_logged(tmp_path):
    df = simulate_season(40, seed=1, snapshot_every=600)
    sp = chronological_split(df)
    assert set(sp.train.game_id).isdisjoint(sp.test.game_id)
    assert sp.train.game_start.max() <= sp.validation.game_start.min()
    log = ExperimentLog(tmp_path / "exp.jsonl")
    log.touch_holdout(sp, "unit test")
    log.touch_holdout(sp, "unit test again")
    assert log.holdout_touches() == 2


def test_leakage_guard_detects_shared_game():
    df = pd.DataFrame({"game_id": ["a", "b"], "game_start": [T, T + timedelta(days=1)]})
    bad = Split(df, df.iloc[[1]], df.iloc[0:0], df.iloc[0:0])
    with pytest.raises(LeakageError):
        assert_no_leakage(bad)


def test_training_is_reproducible_and_synthetic_only():
    df = simulate_season(60, seed=2, snapshot_every=600)
    a = train_nhl(df, "SYNTHETIC test", ModelStatus.SYNTHETIC_ONLY, n_bootstrap=3)
    b = train_nhl(df, "SYNTHETIC test", ModelStatus.SYNTHETIC_ONLY, n_bootstrap=3)
    x = df[list(a.forecaster.forecasters[0].model.feature_names)].to_numpy(float)[:20]
    assert np.allclose(a.forecaster.forecasters[0].model.predict_matrix(x)[0],
                       b.forecaster.forecasters[0].model.predict_matrix(x)[0])
    assert a.forecaster.version.status == ModelStatus.SYNTHETIC_ONLY


# ------------------------------------------------------------------ replay


def test_replay_is_deterministic():
    a = replay_file(FIXTURE, forecaster=None)
    b = replay_file(FIXTURE, forecaster=None)
    assert a.digest == b.digest and len(a.sink.decisions) > 0


def test_replay_clock_refuses_to_go_backwards():
    c = ReplayClock(T)
    with pytest.raises(ValueError):
        c.advance_to(T - timedelta(seconds=1))


def test_mechanics_demo_full_path_and_cap():
    from sports_edge.forecast.train import train_synthetic

    rep = train_synthetic(n_games=200)
    r = replay_file(FIXTURE, forecaster=rep.forecaster, mechanics_demo=True)
    s = r.summary()
    for d in r.sink.decisions:
        assert any("MECHANICS DEMO" in n for n in d.notes)
    spent = sum(D(f["cost"]) + D(f["fees"]) for f in s["fills"])
    assert spent <= D("100")  # per-team cap includes every add and fees
    assert {x["outcome"] for x in s["settlements"]} <= {"WIN", "LOSS"}
    # the pre-goal reference quote was excluded from the comparison, with an audit note
    assert any("REFERENCE_PRE_EVENT" in n for d in r.sink.decisions for n in d.notes)


def test_game_and_mapping_helpers_consistent():
    assert mapping().game_id == game().game_id


def test_strategy_thresholds_marked_provisional():
    assert StrategyConfig(strategy_version="x", entry_mode="any_edge", forecast_rule=RULE).provisional
