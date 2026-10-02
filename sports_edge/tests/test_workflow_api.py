"""End-to-end API workflow: readiness -> game -> replay -> signal -> preview -> order
-> ledger -> outcome, plus auth, duplicates, expiry, exposure, providers, resync."""

import asyncio
import os
import stat
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sports_edge.api.app import create_app
from sports_edge.connections import SecretStore
from sports_edge.forecast.train import train_synthetic
from sports_edge.session import AppSession, EventLog

FIXTURE = Path(__file__).parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"
H = {"X-SE-Request": "1"}


@pytest.fixture(scope="module")
def app():
    a = create_app(train_games=150)
    a.state.server.db = None  # keep tests out of the developer's database
    a.state.server.session.db = None
    return a


@pytest.fixture
def client(app, tmp_path):
    app.state.server.store = SecretStore(tmp_path / "secrets.json")
    for p in app.state.server.providers.values():
        p.disabled, p.last_test = False, None
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"token": app.state.server.auth.token}).status_code == 200
    return c


def new_session(c, mode):
    r = c.post("/api/session", json={"mode": mode}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def step_until_signal(c, max_steps=3000):
    for _ in range(max_steps):
        g = c.get("/api/games").json()[0]
        for k in g["contracts"]:
            if k["signal_eligible"]:
                return g, k
        info = c.post("/api/session/step", json={"n": 1}, headers=H).json()
        if info["finished"]:
            break
    return None, None


# ------------------------------------------------------------------ auth


def test_commands_require_auth_and_csrf(app):
    c = TestClient(app)
    assert c.post("/api/session/step", json={"n": 1}).status_code == 401
    assert c.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    c.post("/api/auth/login", json={"token": app.state.server.auth.token})
    assert c.post("/api/session/step", json={"n": 1}).status_code == 403  # no CSRF header
    assert c.post("/api/session/step", json={"n": 1}, headers=H).status_code == 200
    bearer = {"Authorization": f"Bearer {app.state.server.auth.token}"}
    assert TestClient(app).post("/api/session/pause", headers=bearer).status_code == 200


# ------------------------------------------------------------------ honest mode


def test_honest_mode_blocks_and_explains(client):
    new_session(client, "honest")
    client.post("/api/session/step", json={"n": 5000}, headers=H)
    h = client.get("/api/health").json()
    assert "NO MODEL LOADED" in h["banners"] and h["session"]["finished"]
    assert client.get("/api/signals").json() == []
    g = client.get("/api/games").json()[0]
    p = client.post("/api/paper/preview", headers=H,
                    json={"game_id": g["game"]["game_id"], "contract_id": g["contracts"][0][
                        "contract_id"]}).json()
    assert p["eligible"] is False and p["max_quantity"] == 0
    assert any("No authorized live game feed" in x or "No validated model" in x or
               "market is closed" in x for x in p["explanation"])
    assert client.get("/api/paper/ledger").json()["orders"] == []


# ------------------------------------------------------------------ full path


def test_full_paper_workflow_with_duplicates_and_outcome(client):
    new_session(client, "mechanics")
    g, k = step_until_signal(client)
    assert k is not None, "mechanics replay should produce at least one eligible signal"
    gid = g["game"]["game_id"]
    pv = client.post("/api/paper/preview", headers=H,
                     json={"game_id": gid, "contract_id": k["contract_id"]}).json()
    assert pv["eligible"] and pv["max_quantity"] > 0
    did = pv["decision"]["decision_id"]
    body = {"decision_id": did, "idempotency_key": "click-1"}
    r1 = client.post("/api/paper/orders", json=body, headers=H)
    assert r1.status_code == 200 and r1.json()["created"]
    order = r1.json()["order"]
    assert order["status"] == "PENDING" and order["fill"] is None  # a click is not a fill
    # duplicate click, same key -> same order
    r2 = client.post("/api/paper/orders", json=body, headers=H).json()
    assert r2["created"] is False and r2["order"]["order_id"] == order["order_id"]
    # a second key on the same signal is refused
    r3 = client.post("/api/paper/orders", json={**body, "idempotency_key": "click-2"},
                     headers=H)
    assert r3.status_code == 409 and r3.json()["code"] == "ALREADY_ORDERED"
    # quantity above the eligible size is refused
    pv2 = client.post("/api/paper/preview", headers=H,
                      json={"game_id": gid, "contract_id": k["contract_id"]}).json()
    if pv2["eligible"]:
        r4 = client.post("/api/paper/orders", headers=H, json={
            "decision_id": pv2["decision"]["decision_id"], "idempotency_key": "too-big",
            "quantity": pv2["max_quantity"] + 1})
        assert r4.status_code in (409, 422)
    # advance replay: the backend rechecks and fills (or rejects) after the delay
    client.post("/api/session/step", json={"n": 5000}, headers=H)
    led = client.get("/api/paper/ledger").json()
    o = next(x for x in led["orders"] if x["order_id"] == order["order_id"])
    assert o["status"] in ("FILLED", "PARTIAL", "REJECTED")
    kinds = [e["kind"] for e in led["events"] if e["order_id"] == order["order_id"]]
    assert kinds[0] == "ORDER_ACCEPTED" and kinds[1] in ("ORDER_FILLED", "ORDER_PARTIAL",
                                                         "ORDER_REJECTED")
    assert all(e["mode"] == "REPLAY" and e["data_label"] == "SYNTHETIC" for e in led["events"])
    if o["fill"]:
        assert "POSITION_SETTLED" in kinds and o["outcome"] in ("WIN", "LOSS")
        ev = client.get("/api/evaluation").json()
        assert ev["filled_orders"] >= 1 and ev["warning"]
        spent = Decimal(o["fill"]["cost"]) + Decimal(o["fill"]["fees"])
        assert spent <= Decimal("100")


def test_expired_signal_is_refused(client):
    new_session(client, "mechanics")
    g, k = step_until_signal(client)
    assert k is not None
    did = k["decision_id"]
    # move replay time well past the signal's expiry
    client.post("/api/session/step", json={"n": 60}, headers=H)
    r = client.post("/api/paper/orders", json={"decision_id": did, "idempotency_key": "late"},
                    headers=H)
    assert r.status_code in (409, 410)
    assert r.json()["code"] in ("SIGNAL_EXPIRED", "SIGNAL_SUPERSEDED")


def test_exposure_limit_blocks_preview(client, app):
    new_session(client, "mechanics")
    g, k = step_until_signal(client)
    assert k is not None
    led = app.state.server.session.monitor.engine.ledger
    s = app.state.server.session
    led.record_purchase(g["game"]["game_id"], k["selection"], s.clock.now().date(),
                        Decimal("99.50"), Decimal("0.50"))
    pv = client.post("/api/paper/preview", headers=H, json={
        "game_id": g["game"]["game_id"], "contract_id": k["contract_id"]}).json()
    assert not pv["eligible"]
    assert "TEAM_CAP_REACHED" in pv["decision"]["reasons"]


def test_unknown_signal_and_contract(client):
    new_session(client, "honest")
    r = client.post("/api/paper/orders", json={"decision_id": "nope", "idempotency_key": "x"},
                    headers=H)
    assert r.status_code == 404 and r.json()["code"] == "UNKNOWN_SIGNAL"
    assert client.post("/api/paper/preview", json={"game_id": "x", "contract_id": "y"},
                       headers=H).status_code == 404


# ------------------------------------------------------------------ connections


def test_connections_real_statuses_and_secret_hygiene(client, app, tmp_path):
    v = {p["id"]: p for p in client.get("/api/connections").json()["providers"]}
    assert v["nhl_live_feed"]["status"] == "BLOCKED"
    assert v["kalshi_ws"]["status"] == "NOT_CONFIGURED"
    assert client.post("/api/connections/kalshi_ws/test", headers=H).status_code == 409
    assert client.post("/api/connections/nhl_live_feed/test", headers=H).status_code == 409
    r = client.put("/api/connections/odds_api/secret", headers=H,
                   json={"name": "ODDS_API_KEY", "value": "secret-value-12345678"}).json()
    assert r["status"] == "CONFIGURED_UNTESTED"  # saved key != working integration
    assert "secret-value" not in str(r) and r["secrets"][0]["hint"] == "…5678"
    mode = stat.S_IMODE(os.stat(app.state.server.store.path).st_mode)
    assert mode == 0o600
    # a real test either succeeds or reports the real error; never a fake "Connected"
    t = client.post("/api/connections/kalshi_rest/test", headers=H).json()
    assert t["status"] in ("CONNECTED", "FAILED")
    if t["status"] == "FAILED":
        assert t["last_test"]["detail"]
    d = client.post("/api/connections/kalshi_rest/disconnect", headers=H).json()
    assert d["status"] == "DISCONNECTED"
    ready = {x["capability"]: x for x in client.get("/api/connections").json()["readiness"]}
    assert not ready["Live value signals"]["ready"]
    assert ready["Replay research"]["ready"]


# ------------------------------------------------------------------ reconcile + optional LLM


def test_event_log_resync_on_gap():
    log = EventLog(capacity=3)
    for i in range(5):
        log.append("x", {"i": i})
    assert log.since(4) == (False, [(5, "x", {"i": 4})])
    resync, evs = log.since(0)  # client missed events that left the buffer
    assert resync and evs == []
    assert log.since(99)[0]  # cursor from a previous session -> resync, not silence


class FailingReviewer:
    async def review(self, *a, **k):
        raise RuntimeError("provider down")


def test_failed_llm_review_does_not_stop_monitoring():
    fc = train_synthetic(n_games=150).forecaster

    async def run():
        s = AppSession.replay(FIXTURE, fc, True, fc.version.model_dump(mode="json"),
                              reviewer=FailingReviewer())
        while not s.stream.done:
            s.step(50)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        return s

    s = asyncio.run(run())
    assert s.monitor.alerts, "monitoring continued and produced signals"
    assert s.reviews and all(r["status"] == "ERROR" for r in s.reviews)
