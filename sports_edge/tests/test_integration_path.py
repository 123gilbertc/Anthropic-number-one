"""THE acceptance path, through the real API:

provider-shaped input -> normalized game/market state -> forecast -> trigger + risk
-> interface signal -> paper-order preview -> paper ledger -> settlement -> evaluation

Provider shapes used:
* Kalshi WebSocket ``orderbook_snapshot`` / ``orderbook_delta`` (fixture lines);
* The Odds API v4 ``/odds`` JSON (raw event payload below, parsed by the adapter);
* NHL game events in our normalized form. No authorized NHL provider exists yet,
  so its raw shape is UNKNOWN; that adapter stays BLOCKED (DATA_SOURCES.md).

The fixture is isolated and synthetic. Passing this proves engineering behaviour
under these conditions only. It says nothing about live access or strategy value.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from sports_edge.adapters.odds_api import parse_h2h
from sports_edge.api.app import create_app
from sports_edge.domain.enums import SettlementRule

H = {"X-SE-Request": "1"}
CODES = {"Boston Bruins": "BOS", "Toronto Maple Leafs": "TOR"}


def odds_api_payload(last_update_iso: str, bos: int, tor: int) -> list[dict]:
    return [{"id": "evt1", "sport_key": "icehockey_nhl", "commence_time": "2026-10-10T23:00:00Z",
             "home_team": "Boston Bruins", "away_team": "Toronto Maple Leafs",
             "bookmakers": [{"key": "pinnacle", "title": "Pinnacle",
                             "last_update": last_update_iso,
                             "markets": [{"key": "h2h", "last_update": last_update_iso,
                                          "outcomes": [{"name": "Boston Bruins", "price": bos},
                                                       {"name": "Toronto Maple Leafs",
                                                        "price": tor}]}]}]}]


@pytest.fixture(scope="module")
def client():
    app = create_app(train_games=150)
    app.state.server.db = app.state.server.session.db = None
    c = TestClient(app)
    c.post("/api/auth/login", json={"token": app.state.server.auth.token})
    c.post("/api/session", json={"mode": "mechanics"}, headers=H)
    c.app_state = app.state.server  # type: ignore[attr-defined]
    return c


def test_full_path_from_provider_shaped_input_to_evaluation(client):
    srv = client.app_state  # type: ignore[attr-defined]
    s = srv.session
    gid = s.stream.game.game_id

    # 1. provider-shaped input -> normalized state ------------------------------------
    eligible = None
    injected = False
    for _ in range(3000):
        client.post("/api/session/step", json={"n": 1}, headers=H)
        now = s.clock.now()
        if not injected and s.monitor.games[gid].reducer.state is not None:
            st = s.monitor.games[gid].reducer.state
            if st.last_material_event_kind == "GOAL" and st.away_score == 2:
                # raw Odds API JSON, published after the goal: parsed by the real adapter
                quotes = parse_h2h(odds_api_payload((now - timedelta(seconds=2)).isoformat(),
                                                    165, -190),
                                   CODES, {("BOS", "TOR"): gid}, now,
                                   SettlementRule.NHL_INCLUDING_OT_SO)
                assert len(quotes) == 1 and quotes[0].prices_american == {"BOS": 165, "TOR": -190}
                with s.lock:
                    s.monitor.on_reference(quotes[0])
                injected = True
        g = client.get(f"/api/games/{gid}").json()
        eligible = next((k for k in g["contracts"] if k["signal_eligible"]), None)
        if eligible:
            break
    assert injected, "reference quote was injected through the Odds API adapter"
    assert eligible, "the fixture must yield an eligible signal"
    assert g["state"]["game_id"] == gid and g["state_age_seconds"] is not None

    # 2. forecast + trigger/risk -> interface signal -----------------------------------
    assert eligible["model_status"] == "SYNTHETIC_ONLY"
    assert 0 < eligible["probability_low"] <= eligible["probability"] <= eligible[
        "probability_high"] < 1
    sig = next(x for x in client.get("/api/signals").json()
               if x["decision"]["decision_id"] == eligible["decision_id"])
    assert sig["decision"]["action"] in ("PAPER_ENTRY", "PAPER_ADD")
    assert any(e.startswith("ALL_GATES_PASSED") for e in sig["explanation"])

    # 3. preview (fresh backend evaluation) ---------------------------------------------
    pv = client.post("/api/paper/preview", headers=H,
                     json={"game_id": gid, "contract_id": eligible["contract_id"]}).json()
    assert pv["eligible"] and pv["decision"]["contract_id"] == eligible["contract_id"]
    assert Decimal(pv["estimated_cost"]) > 0 and pv["max_quantity"] > 0

    # 4. paper order -> ledger ------------------------------------------------------------
    r = client.post("/api/paper/orders", headers=H, json={
        "decision_id": pv["decision"]["decision_id"], "idempotency_key": "path-1",
        "expected_contract_id": eligible["contract_id"]})
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["status"] == "PENDING" and order["fill"] is None

    # 5. settlement ------------------------------------------------------------------------
    client.post("/api/session/step", json={"n": 5000}, headers=H)
    led = client.get("/api/paper/ledger").json()
    o = next(x for x in led["orders"] if x["order_id"] == order["order_id"])
    assert o["status"] in ("FILLED", "PARTIAL", "REJECTED")
    kinds = [e["kind"] for e in led["events"] if e["order_id"] == o["order_id"]]
    assert kinds[0] == "ORDER_ACCEPTED"

    # 6. evaluation + cross-screen compatibility ------------------------------------------
    ev = client.get("/api/evaluation").json()
    filled = [x for x in led["orders"] if x["fill"]]
    assert ev["filled_orders"] == len(filled)
    spend = sum(Decimal(x["fill"]["cost"]) + Decimal(x["fill"]["fees"]) for x in filled)
    assert Decimal(ev["total_spend_all_in"]) == spend
    assert sum(Decimal(f["cost"]) + Decimal(f["fees"]) for f in led["fills"]) == spend
    if o["fill"]:
        assert "POSITION_SETTLED" in kinds and o["outcome"] in ("WIN", "LOSS")
        assert ev["settled_orders"] >= 1
    # every order refers to a decision the audit log and signal list know about
    audit_ids = {d["decision_id"] for d in client.get("/api/decisions?limit=5000").json()}
    signal_ids = {x["decision"]["decision_id"] for x in client.get("/api/signals").json()}
    known = audit_ids | signal_ids | set(s.monitor.signals)
    assert all(x["decision_id"] in known for x in led["orders"])
    # all screens read the same session
    h = client.get("/api/health").json()
    assert h["session"]["run_id"] == ev["run_id"] == led["events"][0]["run_id"]
    # the reference quote reached the audit trail with its provider timestamp
    assert any(q.source == "the_odds_api" for q in s.monitor.sink.quotes)
