"""Customer product layer: catalog, discovery, Live Board, workspace, evidence, history.

All on deterministic synthetic fixtures. These prove engineering behaviour only.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sports_edge.api.app import create_app
from sports_edge.product.catalog import Catalog, DiscoveredEvent
from sports_edge.product.discovery import DiscoveryService, FixtureSchedule, ProviderSchedule

T = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
LIVE_MOMENT = datetime(2026, 10, 11, 18, 15, tzinfo=UTC)
H = {"X-SE-Request": "1"}


def de(pid, home, away, start, *, source="srcA", status="SCHEDULED", game_number=None,
       sport="MLB"):
    return DiscoveredEvent(source, pid, sport, sport, "2026", home, away, start, status,
                           game_number=game_number)


# ------------------------------------------------------------------ catalog


def test_reschedule_cancellation_and_missing_events_are_kept_with_history():
    c = Catalog()
    c.reconcile("srcA", [de("1", "NYY", "BOS", T), de("2", "LAD", "SF", T)], T)
    eid = c.by_provider[("srcA", "1")]
    r = c.reconcile("srcA", [de("1", "NYY", "BOS", T + timedelta(hours=2)),
                             de("2", "LAD", "SF", T, status="POSTPONED")], T + timedelta(hours=1))
    assert r["rescheduled"] == 1 and c.events[eid].reschedules[0]["to"].startswith("2026-10-11T19")
    assert c.by_provider[("srcA", "1")] == eid  # same provider id -> same canonical event
    assert c.events[c.by_provider[("srcA", "2")]].status == "POSTPONED"
    c.reconcile("srcA", [], T + timedelta(hours=2), (T - timedelta(hours=1), T + timedelta(days=1)))
    assert len(c.events) == 2  # an event missing from one fetch is not deleted
    assert all("srcA" in e.unconfirmed_by for e in c.events.values())


def test_doubleheaders_and_same_names_are_never_merged():
    c = Catalog()
    c.reconcile("srcA", [de("g1", "NYY", "BOS", T, game_number=1),
                         de("g2", "NYY", "BOS", T + timedelta(hours=4), game_number=2)], T)
    assert len(c.events) == 2
    # another provider's game 2 links to game 2 only (canonical ids + game number)
    c.reconcile("srcB", [de("x2", "NYY", "BOS", T + timedelta(hours=4), source="srcB",
                            game_number=2)], T)
    assert len(c.events) == 2
    assert c.by_provider[("srcB", "x2")] == c.by_provider[("srcA", "g2")]
    # a different team that shares a display name has a different canonical id: no merge
    c.reconcile("srcB", [de("x3", "NYR", "BOS", T, source="srcB")], T)
    assert len(c.events) == 3


def test_failed_or_unconfigured_sources_never_produce_an_empty_slate():
    class Boom:
        name, sport, kind, label = "boom", "NHL", "schedule", "SNIPPET"

        def configured(self):
            return True, ""

        async def fetch(self, a, b):
            raise ConnectionError("egress blocked")

    good = FixtureSchedule("fx", "NHL", [de("1", "NRT", "GLV", T, sport="NHL")])
    svc = DiscoveryService([good, Boom(), ProviderSchedule("sr_nhl", "NHL", None, None)],
                           clock=lambda: T)
    asyncio.run(svc.run_once())
    assert len(svc.catalog.events) == 1
    st = {r.source: r for r in svc.results.values()}
    assert st["boom"].status == "FAILED" and "egress blocked" in st["boom"].error
    assert st["sr_nhl"].status == "NOT_CONFIGURED"
    good.events = []  # the next good fetch is empty, then the failing source fails again
    asyncio.run(svc.run_once())
    assert len(svc.catalog.events) == 1


# ------------------------------------------------------------------ API on the demo slate


@pytest.fixture(scope="module")
def client():
    app = create_app(train_games=120, nfl_games=150)
    srv = app.state.server
    srv.session.step_until(LIVE_MOMENT)
    c = TestClient(app)
    c.post("/api/auth/login", json={"token": srv.auth.token})
    return c


def test_live_board_reports_unknown_coverage_not_zero_games(client):
    b = client.get("/api/board?mode=live").json()
    assert b["mode"] == "live" and b["rows"] == []
    for sp in ("NHL", "NFL", "TENNIS", "MLB"):
        assert b["sports"][sp]["schedule_known"] is False
        assert b["sports"][sp]["discovered"] is None  # unknown, not 0
        assert "unknown" in b["sports"][sp]["note"]
    assert "SYNTHETIC" not in str(b["meta"])  # live mode never mixes in demo data


def test_demo_board_rows_counts_and_unavailable_games_are_shown(client):
    b = client.get("/api/board?mode=demo&tz=America/New_York").json()
    assert b["meta"]["data_label"] == "SYNTHETIC"
    assert any("SYNTHETIC" in x or "MECHANICS" in x for x in b["meta"]["banners"])
    rows = {r["game_id"]: r for r in b["rows"]}
    assert {"SYN-NHL-1", "SYN-NFL-1", "SYN-TEN-1", "SYN-MLB-1"} <= set(rows)
    assert rows["SYN-TEN-4"]["assessment"] == "UNAVAILABLE" and not rows["SYN-TEN-4"]["has_market"]
    assert rows["SYN-TEN-3"]["assessment"] == "UNAVAILABLE"  # doubles model not enabled
    assert rows["SYN-TEN-1"]["assessment"] == "FINAL"
    tennis = b["sports"]["TENNIS"]
    assert tennis["discovered"] == 4 and tennis["forecast_ready"] < tennis["discovered"]
    for r in b["rows"]:
        lw = r["likely_winner"]
        assert lw["available"] or lw["reason"]
        assert r["validated_net_edge"] is None  # no VALIDATED model: no edge ranking
    # a date outside the slate in this timezone has no rows (but the board still answers)
    other = client.get("/api/board?mode=demo&tz=UTC&date=2026-10-13").json()
    assert all(r["event"]["status"] in ("LIVE", "AWAITING DATA") for r in other["rows"])


def test_workspace_answers_three_questions_with_evidence(client):
    w = client.get("/api/events/SYN-NHL-1/workspace").json()
    assert w["likely_winner"]["available"] and 0 < w["likely_winner"]["probability"] < 1
    assert w["likely_winner"]["status"] == "SYNTHETIC_ONLY"
    assert w["value_side"] is None or w["value_side"]["participant"] in w["event"]["participants"]
    for c in w["contracts"]:
        assert c["settlement_text"] and c["state"] in (
            "WATCH", "VALUE CANDIDATE", "PAPER ENTRY ELIGIBLE", "HOLD", "NO ADD",
            "DATA UNAVAILABLE")
        if c["ev"]:
            assert "not a promised profit" in c["ev"]["plain_english"]
            assert c["entry"]["stake"] == "25"
        assert {r["check"] for r in c["readiness"]} >= {"Order book valid and current",
                                                       "Contract rules match the forecast"}
    ev = w["evidence"]
    for d in ev["all_drivers"]:
        if d["available"]:
            assert "Not proof of causation" in d["note"] and d["kind"] == "MODEL"
    assert ev["invalidation"] and ev["missing"]
    assert ev["ai_review"]["status"] == "NOT_CONFIGURED"


def test_what_if_is_a_labelled_simulation_with_no_side_effects(client):
    srv = client.app.state.server
    s = srv.session
    rt = s.monitor.games["SYN-NHL-1"]
    before = (rt.reducer.state.snapshot_id, len(s.monitor.sink.decisions),
              len(s.history.contracts["SYN-NHL-1-NRT"].preds), len(s.orders))
    r = client.post("/api/events/SYN-NHL-1/whatif", headers=H,
                    json={"edits": {"home_score": rt.reducer.state.home_score + 2}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["label"] == "SIMULATION" and out["estimates"]["NRT"]["available"]
    after = (rt.reducer.state.snapshot_id, len(s.monitor.sink.decisions),
             len(s.history.contracts["SYN-NHL-1-NRT"].preds), len(s.orders))
    assert before == after
    bad = client.post("/api/events/SYN-NHL-1/whatif", headers=H,
                      json={"edits": {"home_skaters": 9}})
    assert bad.status_code == 422
    bad = client.post("/api/events/SYN-TEN-2/whatif", headers=H,
                      json={"edits": {"games_p1": 9, "games_p2": 0}})
    assert bad.status_code == 422  # not a valid tennis score
    unauth = TestClient(client.app).post("/api/events/SYN-NHL-1/whatif", headers=H,
                                         json={"edits": {}})
    assert unauth.status_code == 401


def test_line_history_vocabulary_and_render_limits(client):
    h = client.get("/api/events/SYN-NHL-1/history?window=all&max_points=60").json()
    c = h["contracts"][0]
    assert c["total_points"] > 60 and c["downsampled"] and len(c["points"]) <= 80
    s = c["summary"]
    assert s["provider_open"] is None and "UNKNOWN" in s["provider_open_note"]
    assert s["first_observed"]["ask"] is not None
    assert s["last_trade"] is None and "never inferred" in s["last_trade_note"]
    p = next(x for x in c["points"] if x["entry"] is not None)
    assert p["entry"] >= p["ask"]  # size-aware entry price is never below the best ask
    assert h["references"] and all("compatible" in r for r in h["references"])
    assert any(a["kind"] == "GOAL" for a in h["annotations"])
    short = client.get("/api/events/SYN-NHL-1/history?window=5m").json()
    assert short["contracts"][0]["total_points"] < c["total_points"]


def test_gap_and_pre_event_reference_are_recorded_on_the_nhl_fixture():
    app = create_app(train_games=120, default_fixture="nhl_synthetic_dip.jsonl",
                     default_mode="mechanics")
    s = app.state.server.session
    while not s.stream.done:
        s.step(50)
    cs = s.history.contracts["SYN-NHLGAME-BOS"]
    assert cs.gaps and cs.gaps[0][1] is not None  # the seq gap opened and closed
    refs = s.history.refs["SYN-2026-10-10-TOR-BOS"]
    assert any(not r.compatible and "BEFORE" in r.note for r in refs)
