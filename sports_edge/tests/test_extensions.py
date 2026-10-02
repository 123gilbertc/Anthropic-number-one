"""MLB state/features, Polymarket parser + fee, alerts, LLM promotion harness,
model comparison with market baseline, threshold estimation/freeze."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import NOW, book, ctx, engine, prediction, state

from sports_edge.adapters.polymarket import PolymarketBookManager, PolymarketTakerFee
from sports_edge.alerts import format_alert, send_discord
from sports_edge.domain.enums import ModelStatus, SourceStatus
from sports_edge.evaluation.splits import chronological_split
from sports_edge.forecast.synthetic import simulate_season
from sports_edge.forecast.train import compare_models
from sports_edge.ingest.mlb_state import MLBStateReducer, build_mlb_features
from sports_edge.ingest.nhl_state import InvalidEvent, NormalizedGameEvent
from sports_edge.llm.evaluate import PairedRow, promotion_report
from sports_edge.triggers import thresholds

T = datetime(2026, 10, 3, 20, 0, tzinfo=UTC)
D = Decimal


def mev(typ, seq, data=None, eid=None):
    return NormalizedGameEvent(game_id="M1", source="t", source_status=SourceStatus.REPLAY,
                               type=typ, provider_event_id=eid or f"m{seq}", seq=seq,
                               event_time=T, published_time=None,
                               received_time=T + timedelta(seconds=seq), data=data or {})


def test_mlb_reducer_runs_halves_pitchers_and_flags():
    r = MLBStateReducer("M1", "NYY", "BOS")
    r.apply(mev("SNAPSHOT", 1, {"home_pitcher": "Cole", "away_pitcher": "Bello",
                                "home_pitcher_pitches": 0, "away_pitcher_pitches": 0}))
    r.apply(mev("PITCH", 2, {"balls": 1, "strikes": 0}))
    assert r.state.home_pitcher_pitches == 1  # top: home pitcher throwing
    r.apply(mev("PLAY", 3, {"outs": 0, "runners": [0, 0, 0], "runs": 1, "pa_complete": True}))
    assert r.state.away_runs == 1
    r.apply(mev("HALF_END", 4))
    assert r.state.half == "BOTTOM" and r.state.outs == 0
    r.apply(mev("PITCHER_CHANGE", 5, {"team": "BOS", "pitcher": None}))
    assert "PITCHER_UNKNOWN_BOS" in r.state.pending_reconciliation
    r.apply(mev("PLAY", 7, {"outs": 1, "runners": [1, 0, 0]}))
    assert "FEED_GAP" in r.state.pending_reconciliation
    with pytest.raises(InvalidEvent):
        r.apply(mev("PLAY", 8, {"outs": 4, "runners": [0, 0, 0]}))
    fv = build_mlb_features(r.state, selection_is_home=True, pregame_prob=None)
    assert fv.values["run_diff"] == -1 and "pregame_logit" in fv.missing
    assert fv.feature_version == "mlb_v1"


def test_polymarket_book_and_fee():
    m = PolymarketBookManager()
    m.handle({"event_type": "book", "asset_id": "A", "bids": [{"price": "0.44", "size": "100"}],
              "asks": [{"price": "0.46", "size": "50"}, {"price": "0.47", "size": "20"}]}, T)
    m.handle({"event_type": "price_change", "price_changes": [
        {"asset_id": "A", "price": "0.46", "size": "0", "side": "SELL"}]}, T)
    snap = m.snapshot("A")
    assert snap.best_ask == D("0.47") and snap.best_bid == D("0.44") and snap.valid
    m.invalidate_all()
    assert not m.snapshot("A").valid  # after reconnect: wait for a fresh book
    assert PolymarketTakerFee().entry_fee(10, D("0.5")) == D("0.13")  # 0.125 rounded up


def test_discord_disabled_by_default_and_labelled():
    st = state()
    d = engine().evaluate(ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57)), NOW)
    sent = []

    async def fake_post(url, payload):
        sent.append(payload)
        return 204

    assert asyncio.run(send_discord(d, "REPLAY", "SYNTHETIC", enabled=False,
                                    webhook_url="https://x", post=fake_post)) == "DISABLED"
    assert asyncio.run(send_discord(d, "REPLAY", "SYNTHETIC", enabled=True, webhook_url=None,
                                    post=fake_post)) == "NOT_CONFIGURED"
    assert asyncio.run(send_discord(d, "REPLAY", "SYNTHETIC", enabled=True,
                                    webhook_url="https://x", post=fake_post)) == "SENT"
    text = format_alert(d, "REPLAY", "SYNTHETIC")
    assert "REPLAY · SYNTHETIC · PAPER ONLY" in text and d.snapshot_id in text
    assert sent and "expires" in sent[0]["content"]


def test_llm_promotion_requires_prospective_evidence():
    few = [PairedRow(f"g{i}", 0.6, 0.6, 1, True, 0.01) for i in range(10)]
    assert promotion_report(few)["status"] == "INSUFFICIENT_PROSPECTIVE_DATA"
    hist = [PairedRow(f"g{i}", 0.6, 0.9, 1, False, 0.01) for i in range(500)]
    r = promotion_report(hist)
    assert r["prospective_rows"] == 0 and r["decision_weight"] == 0.0
    same = [PairedRow(f"g{i}", 0.6, 0.6, i % 2, True, 0.01) for i in range(300)]
    assert promotion_report(same)["status"] == "NO_DEMONSTRATED_VALUE"


def test_compare_models_includes_market_baseline():
    df = simulate_season(80, seed=3, snapshot_every=600)
    rep = compare_models(df, "SYNTHETIC test", ModelStatus.SYNTHETIC_ONLY)
    assert {"logistic", "gbm", "market_implied_baseline"} <= set(rep["models"])
    assert rep["models"]["market_implied_baseline"]["brier"]["n_clusters"] == rep["test_games"]


def test_thresholds_estimated_on_validation_and_frozen(tmp_path):
    df = simulate_season(200, seed=4, snapshot_every=300)
    df = df.assign(p_low=df["market_p"] + 0.03, ask=(df["market_p"] + 0.01).clip(0.02, 0.98))
    sp = chronological_split(df)
    ft = thresholds.estimate(sp.validation, "SYNTHETIC_ONLY", min_games=10)
    assert ft.min_conservative_ev_cents_per_contract in thresholds.CANDIDATE_MARGINS_CENTS
    assert ft.data_status == "SYNTHETIC_ONLY" and len(ft.config_sha256) == 64
    assert str(sp.test["game_start"].min()) not in ft.estimated_on  # no test data used
    path = thresholds.save(ft, tmp_path)
    assert path.exists()
    assert "test_games_selected" in thresholds.evaluate_frozen(ft, sp.test)
