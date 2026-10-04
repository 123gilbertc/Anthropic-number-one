"""Dangerous integration failure cases (integration-validation pass).

Each test names the failure it guards against. Fixtures are isolated and
synthetic; none of this is evidence about live providers or strategy value.
"""

import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import NOW, book, ctx, engine, prediction, state

from sports_edge.domain.enums import Action, Reason
from sports_edge.forecast.train import train_synthetic
from sports_edge.session import AppSession, CommandError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"


@pytest.fixture(scope="module")
def forecaster():
    return train_synthetic(n_games=150).forecaster


def session_at_signal(fc) -> AppSession:
    s = AppSession.replay(FIXTURE, fc, True, {})
    while not s.monitor.alerts:
        s.step(1)
    return s


# ------------------------------------------------------------------ gap 1: competing orders


def test_concurrent_duplicate_requests_create_exactly_one_order(forecaster):
    s = session_at_signal(forecaster)
    did = s.monitor.alerts[-1].decision_id
    results, errors = [], []
    barrier = threading.Barrier(8)

    def hit():
        barrier.wait()
        try:
            results.append(s.place_order(did, "same-key", None))
        except CommandError as e:
            errors.append(e.code)

    ts = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(s.orders) == 1 and not errors
    assert sum(1 for _, created in results if created) == 1
    assert len({o.order_id for o, _ in results}) == 1
    assert [e.kind for e in s.ledger].count("ORDER_ACCEPTED") == 1


def test_competing_orders_on_same_contract_are_refused_while_one_is_pending(forecaster):
    s = AppSession.replay(FIXTURE, forecaster, True, {})

    def live_signals():
        now = s.clock.now()
        return [a for a in s.monitor.alerts if now <= a.expires_at]

    # advance until two distinct unexpired signals exist for the same contract
    while not s.stream.done:
        s.step(1)
        ls = live_signals()
        if len(ls) >= 2 and ls[-1].contract_id == ls[-2].contract_id:
            break
    a, b = live_signals()[-2:]
    assert a.decision_id != b.decision_id and a.contract_id == b.contract_id
    s.place_order(a.decision_id, "k1", None)
    with pytest.raises(CommandError) as e:
        s.place_order(b.decision_id, "k2", None)
    assert e.value.code == "ORDER_PENDING_FOR_CONTRACT"
    assert len(s.orders) == 1


def test_pending_order_reserves_exposure():
    eng = engine()
    st = state()
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    d = eng.evaluate(c, NOW)
    room_before, _ = eng.ledger.remaining("G1", "BOS", NOW.date())
    from sports_edge.paper.broker import PaperBroker
    PaperBroker(eng).submit(d, st)
    room_after, _ = eng.ledger.remaining("G1", "BOS", NOW.date())
    assert room_after < room_before  # pending cost + fees are reserved immediately
    assert room_before - room_after >= d.planned_cost


def test_reservation_released_when_fill_rechecks_fail():
    from sports_edge.paper.broker import PaperBroker
    eng = engine()
    st = state()
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    br = PaperBroker(eng)
    br.submit(eng.evaluate(c, NOW), st)
    [att] = br.process(NOW + timedelta(minutes=10), lambda _d: c)  # expired
    assert att.filled is None
    assert eng.ledger.remaining("G1", "BOS", NOW.date())[0] == Decimal("100")


def test_order_for_a_different_contract_than_displayed_is_refused(forecaster):
    s = session_at_signal(forecaster)
    d = s.monitor.alerts[-1]
    with pytest.raises(CommandError) as e:
        s.place_order(d.decision_id, "k", None, expected_contract_id="SOME-OTHER-CONTRACT")
    assert e.value.code == "CONTRACT_MISMATCH" and not s.orders


# ------------------------------------------------------------------ stale / mismatched data


def test_stale_game_data_blocks_signal():
    st = state(received_ago=120)
    c = ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57))
    c = c.__class__(**{**c.__dict__, "game_last_seen": NOW - timedelta(seconds=120)})
    d = engine().evaluate(c, NOW)
    assert d.action == Action.DATA_BLOCKED and Reason.GAME_STATE_STALE in d.reasons


def test_mismatched_timestamps_are_rejected_not_trusted():
    from sports_edge.domain.enums import SourceStatus
    from sports_edge.ingest.nhl_state import InvalidEvent, NHLStateReducer, NormalizedGameEvent

    r = NHLStateReducer("G1", "BOS", "TOR")
    ev = NormalizedGameEvent(game_id="G1", source="t", source_status=SourceStatus.REPLAY,
                             type="GOAL", provider_event_id="e1", seq=1,
                             event_time=NOW + timedelta(seconds=60), published_time=None,
                             received_time=NOW, data={"team": "TOR"})
    with pytest.raises(InvalidEvent, match="TIMESTAMP_INCONSISTENT"):
        r.apply(ev)
    from helpers import RULE, quote

    from sports_edge.triggers.strategy import StrategyConfig
    cfg = StrategyConfig(strategy_version="ref", entry_mode="any_edge", forecast_rule=RULE,
                         require_reference=True)
    st = state(material_ago=60)
    future = quote(updated_ago=-120, received_ago=1)  # claims an update 2 min in the future
    d = engine(cfg).evaluate(ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57),
                                 references=(future,)), NOW)
    assert d.action == Action.DATA_BLOCKED and Reason.TIMESTAMP_INCONSISTENT in d.reasons


def test_event_log_wakes_waiter_registered_before_append_from_another_thread():
    import asyncio

    from sports_edge.session import EventLog

    log = EventLog()

    async def run():
        w = asyncio.Event()
        log.add_waiter(w)  # register first, then read: no lost wake-up
        assert log.since(0) == (False, [])
        t = threading.Thread(target=lambda: log.append("x", {}))
        t.start()
        await asyncio.wait_for(w.wait(), timeout=2)  # woken thread-safely, well before 10 s
        t.join()
        log.discard_waiter(w)
        return log.since(0)

    assert asyncio.run(run()) == (False, [(1, "x", {})])


def test_kalshi_private_key_from_environment_is_written_privately(tmp_path, monkeypatch):
    """Cloud environments inject variables, not files: PEM text becomes a 0600 file."""
    import os
    import stat

    from sports_edge.connections import materialize_kalshi_key
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY",
                       "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----")
    p = materialize_kalshi_key(tmp_path)
    assert p is not None and os.environ["KALSHI_PRIVATE_KEY_PATH"] == str(p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_text().splitlines()[1] == "abc"  # single-line "\n" escapes restored
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "not a key")
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH")
    with pytest.raises(ValueError):
        materialize_kalshi_key(tmp_path)
