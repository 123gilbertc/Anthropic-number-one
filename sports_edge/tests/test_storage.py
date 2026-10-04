import os
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from sports_edge.replay.runner import replay_file
from sports_edge.storage import tables as t
from sports_edge.storage.repository import SqlSink, make_engine

URL = os.environ.get("TEST_DATABASE_URL",
                     "postgresql+psycopg://sports_edge:sports_edge@localhost:5432/sports_edge_test")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"


@pytest.fixture
def engine():
    try:
        eng = make_engine(URL)
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as e:  # pragma: no cover - depends on local Postgres
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL: {type(e).__name__}")
    t.metadata.drop_all(eng)
    t.metadata.create_all(eng)
    yield eng
    t.metadata.drop_all(eng)


def test_replay_persists_audit_trail(engine):
    sink = SqlSink(engine)
    r = replay_file(FIXTURE, forecaster=None, extra_sink=sink)
    with engine.connect() as c:
        n_dec = c.execute(select(func.count()).select_from(t.decisions)).scalar_one()
        n_raw = c.execute(select(func.count()).select_from(t.raw_events)).scalar_one()
    assert n_dec == len(r.sink.decisions) and n_raw == len(r.sink.raws)
    back = sink.decisions_for(r.monitor.games.popitem()[0])
    assert [d.decision_id for d in back] == [d.decision_id for d in r.sink.decisions]


def test_paper_orders_and_ledger_persist_with_mode(engine):
    from sports_edge.forecast.train import train_synthetic
    from sports_edge.session import AppSession

    fc = train_synthetic(n_games=150).forecaster
    s = AppSession.replay(FIXTURE, fc, True, {})
    s.db = SqlSink(engine)
    while not s.monitor.alerts:
        s.step(1)
    o, _ = s.place_order(s.monitor.alerts[-1].decision_id, "k", None)
    s.step(5000)
    with engine.connect() as c:
        rows = c.execute(select(t.ledger_events.c.kind, t.ledger_events.c.mode,
                                t.ledger_events.c.data_label)).all()
        statuses = c.execute(select(t.paper_orders.c.status)
                             .where(t.paper_orders.c.order_id == o.order_id)).scalars().all()
    assert [r[0] for r in rows][0] == "ORDER_ACCEPTED"
    assert {(r[1], r[2]) for r in rows} == {("REPLAY", "SYNTHETIC")}
    assert statuses[0] == "PENDING" and len(statuses) >= 2  # versions appended, not overwritten


def test_worker_restart_abandons_pending_orders_and_keeps_idempotency(engine):
    from datetime import UTC, datetime

    from sports_edge.forecast.train import train_synthetic
    from sports_edge.session import AppSession, recover

    fc = train_synthetic(n_games=150).forecaster
    s = AppSession.replay(FIXTURE, fc, True, {})
    s.db = SqlSink(engine)
    while not s.monitor.alerts:
        s.step(1)
    did = s.monitor.alerts[-1].decision_id
    o, _ = s.place_order(did, "retry-key", None)
    assert o.status == "PENDING"
    # --- worker dies here (no further steps); a new worker starts ---------------
    rec = recover(SqlSink(engine), datetime.now(UTC))
    assert rec["abandoned"] == [o.order_id]
    assert rec["orders"][o.order_id].status == "ABANDONED"
    s2 = AppSession.replay(FIXTURE, fc, True, {})
    s2.db = SqlSink(engine)
    s2.prior_keys = rec["by_key"]
    again, created = s2.place_order(did, "retry-key", None)  # client retries after restart
    assert not created and again.order_id == o.order_id and again.status == "ABANDONED"
    kinds = [e["kind"] for e in SqlSink(engine).ledger_history()]
    assert kinds == ["ORDER_ACCEPTED", "ORDER_ABANDONED"]
    # a second restart does not abandon twice
    assert recover(SqlSink(engine), datetime.now(UTC))["abandoned"] == []


def test_live_exposure_is_rebuilt_but_replay_is_not():
    from datetime import UTC, datetime
    from decimal import Decimal

    from sports_edge.domain.records import PaperFill
    from sports_edge.risk.exposure import ExposureLedger, RiskLimits
    from sports_edge.session import PaperOrder, restore_exposure

    now = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)

    def order(mode, oid):
        f = PaperFill(fill_id="f" + oid, decision_id="d" + oid, contract_id="C", selection_team="BOS",
                      game_id="G", requested_quantity=100, filled_quantity=100,
                      cost=Decimal("45"), fees=Decimal("1.74"), fill_time=now, book_id="b",
                      assumptions=())
        return PaperOrder(order_id=oid, idempotency_key=oid, run_id="r", mode=mode,
                          data_label="X", decision_id="d" + oid, game_id="G", contract_id="C",
                          selection_team="BOS", requested_quantity=100, status="FILLED",
                          created_time=now, not_before=now, fill=f)

    led = ExposureLedger(RiskLimits.example_1000_bankroll_100_cap())
    assert restore_exposure(led, [order("LIVE", "a"), order("REPLAY", "b")]) == 1
    room, _ = led.remaining("G", "BOS", now.date())
    assert room == Decimal("100") - Decimal("46.74")
