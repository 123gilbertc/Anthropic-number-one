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
