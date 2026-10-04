"""Accounts, isolation, entitlements, test-mode billing webhooks, alerts, deletion.

Uses an unreachable database URL so accounts live in memory (the Postgres store is
exercised separately in test_accounts_sql when a local database is available).
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from sports_edge.accounts import hash_password, verify_password
from sports_edge.billing import sign

H = {"X-SE-Request": "1"}
SECRET = "whsec_test_dummy"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    import os
    os.environ["DATABASE_URL"] = "postgresql+psycopg://x:x@127.0.0.1:1/x"
    os.environ["BILLING_WEBHOOK_SECRET_TEST"] = SECRET
    from sports_edge.api.app import create_app
    a = create_app(train_games=150, default_fixture="nhl_synthetic_dip.jsonl",
                   default_mode="mechanics")
    yield a
    os.environ.pop("DATABASE_URL")
    os.environ.pop("BILLING_WEBHOOK_SECRET_TEST")


def user(app, email):
    c = TestClient(app)
    r = c.post("/api/account/signup", json={"email": email, "password": "correct horse 1"})
    assert r.status_code == 200, r.text
    return c, r.json()["user"]["user_id"]


def webhook(c, event: dict, secret=SECRET, t=None):
    body = json.dumps(event).encode()
    return c.post("/api/billing/webhook", content=body,
                  headers={"stripe-signature": sign(secret, body, t),
                           "content-type": "application/json"})


def test_password_hashing_and_signup_validation(app):
    h = hash_password("a long password")
    assert "a long password" not in h and verify_password("a long password", h)
    assert not verify_password("wrong", h)
    c = TestClient(app)
    assert c.post("/api/account/signup", json={"email": "bad", "password": "x" * 12}
                  ).json()["code"] == "BAD_EMAIL"
    assert c.post("/api/account/signup", json={"email": "a@b.co", "password": "short"}
                  ).json()["code"] == "WEAK_PASSWORD"
    user(app, "dup@example.com")
    assert c.post("/api/account/signup", json={"email": "DUP@example.com",
                                               "password": "x" * 12}).status_code == 409
    me = TestClient(app)
    me.post("/api/account/login", json={"email": "dup@example.com",
                                        "password": "correct horse 1"})
    body = me.get("/api/account/me").json()
    assert body["signed_in"] and "password" not in json.dumps(body)


def test_login_is_rate_limited(app):
    c = TestClient(app)
    codes = [c.post("/api/account/login", json={"email": "nobody@example.com",
                                                "password": "wrong password!"}).status_code
             for _ in range(10)]
    assert 401 in codes and codes[-1] == 429
    app.state.server.accounts.limiter.hits.clear()  # the shared test client address


def test_customers_cannot_use_operator_commands(app):
    c, _ = user(app, "cust@example.com")
    assert c.post("/api/session", json={}, headers=H).status_code == 401
    assert c.put("/api/connections/odds_api/secret", json={"name": "ODDS_API_KEY",
                                                          "value": "x"}, headers=H).status_code == 401
    assert c.post("/api/discovery/run", headers=H).status_code == 401
    assert c.put("/api/account/preferences", json={"patch": {"timezone": "UTC"}}
                 ).status_code == 403  # CSRF header required for cookie sessions


def test_paper_portfolios_are_isolated_between_accounts(app):
    srv = app.state.server
    s = srv.session
    a, uid_a = user(app, "alice@example.com")
    b, uid_b = user(app, "bob@example.com")
    while not s.stream.done:
        s.step(1)
        elig = [m.contract_id for m in s.monitor.games[s.stream.game.game_id].mappings
                if s.monitor.preview(s.stream.game.game_id, m.contract_id,
                                     register=False).action.value == "PAPER_ENTRY"]
        if elig:
            break
    gid, cid = s.stream.game.game_id, elig[0]
    pa = a.post("/api/paper/preview", json={"game_id": gid, "contract_id": cid}, headers=H).json()
    pb = b.post("/api/paper/preview", json={"game_id": gid, "contract_id": cid}, headers=H).json()
    assert pa["eligible"] and pb["eligible"]
    assert pa["decision"]["decision_id"] != pb["decision"]["decision_id"]  # scoped ids
    # Bob cannot submit Alice's signal
    r = b.post("/api/paper/orders", headers=H, json={"decision_id": pa["decision"]["decision_id"],
                                                     "idempotency_key": "k1"})
    assert r.status_code == 404 and r.json()["code"] == "UNKNOWN_SIGNAL"
    ra = a.post("/api/paper/orders", headers=H, json={"decision_id": pa["decision"]["decision_id"],
                                                      "idempotency_key": "k1",
                                                      "expected_contract_id": cid})
    assert ra.status_code == 200 and ra.json()["order"]["user_id"] == uid_a
    # Bob may use the same idempotency key without colliding with Alice's order
    rb = b.post("/api/paper/orders", headers=H, json={"decision_id": pb["decision"]["decision_id"],
                                                      "idempotency_key": "k1",
                                                      "expected_contract_id": cid})
    assert rb.status_code == 200 and rb.json()["order"]["order_id"] != ra.json()["order"]["order_id"]
    la, lb = a.get("/api/paper/ledger").json(), b.get("/api/paper/ledger").json()
    assert {o["user_id"] for o in la["orders"]} == {uid_a}
    assert {o["user_id"] for o in lb["orders"]} == {uid_b}
    assert TestClient(app).get("/api/paper/ledger").json()["orders"] == []  # anonymous
    # each portfolio reserves against its own bankroll
    assert la["cash"] == lb["cash"]
    ex = a.get("/api/account/export").json()
    assert all(e["user_id"] == uid_a for e in ex["paper_ledger"])


def test_stream_never_carries_another_accounts_paper_records(app):
    from sports_edge.api.app import stream_visible
    srv = app.state.server
    owners = {e.user_id for e in srv.session.ledger}
    assert len(owners) >= 2  # Alice's and Bob's orders from the previous test
    for _seq, kind, data in srv.session.events.buffer:
        if kind == "ledger":
            assert stream_visible(kind, data, data["user_id"])
            assert not stream_visible(kind, data, "someone-else")
            assert not stream_visible(kind, data, None)  # anonymous
    assert stream_visible("state", {"game_id": "G"}, None)  # market/game data is shared
    assert not stream_visible("order_result", {"owner": "u_a"}, "u_b")


def test_entitlements_billing_webhooks_and_reconciliation(app):
    c, uid = user(app, "dana@example.com")
    rule = {"rule": {"kind": "value_candidate", "scope": {}}}
    assert c.post("/api/alerts/rules", json=rule, headers=H).json()["code"] == "NOT_ENTITLED"
    raw = TestClient(app)
    now = int(time.time())
    ev = {"id": "evt_1", "type": "checkout.session.completed", "created": now,
          "data": {"object": {"client_reference_id": uid, "customer": "cus_1",
                              "subscription": "sub_1", "metadata": {"plan": "pro"}}}}
    assert webhook(raw, ev, secret="wrong").status_code == 400
    assert webhook(raw, ev, t=now - 3600).json()["code"] == "STALE_SIGNATURE"
    assert webhook(raw, ev).json()["status"] == "active"
    assert webhook(raw, ev).json()["duplicate"] is True  # processed at most once
    assert "alerts" in c.get("/api/account/me").json()["entitlements"]
    assert c.post("/api/alerts/rules", json=rule, headers=H).status_code == 200
    old = {"id": "evt_0", "type": "customer.subscription.deleted", "created": now - 50,
           "data": {"object": {"customer": "cus_1", "id": "sub_1"}}}
    assert "ignored" in webhook(raw, old).json()  # older than the current state
    fail = {"id": "evt_2", "type": "invoice.payment_failed", "created": now + 1,
            "data": {"object": {"customer": "cus_1"}}}
    assert webhook(raw, fail).json()["status"] == "past_due"
    ent = c.get("/api/account/me").json()["entitlements"]
    assert "alerts" not in ent and "live_board" in ent  # same safety, fewer conveniences
    assert c.post("/api/billing/checkout", json={"plan": "pro"}, headers=H
                  ).json()["code"] == "PRICE_NOT_APPROVED"
    assert raw.get("/api/billing").json()["live_charges"].startswith("DISABLED")


def test_alerts_are_scoped_deduplicated_and_quiet_hours_suppress_delivery(app):
    srv = app.state.server
    c, uid = user(app, "erin@example.com")
    sub = srv.accounts.subscription(uid) | {"plan": "pro", "status": "active"}
    srv.accounts.store.put_doc(uid, "subscription", sub)
    assert c.post("/api/alerts/rules", headers=H,
                  json={"rule": {"kind": "price_move", "threshold_cents": 5,
                                 "scope": {"sport": "NHL"}}}).status_code == 200
    assert c.post("/api/alerts/rules", headers=H,
                  json={"rule": {"kind": "price_move", "threshold_cents": 1}}).status_code == 422
    c.put("/api/account/preferences", headers=H,
          json={"patch": {"quiet_hours": {"start": "00:00", "end": "23:59"}}})
    from sports_edge.api.app import create_app  # fresh session so prices move after the rule
    srv.session.pause()
    srv.session = srv._new_session("nhl_synthetic_dip.jsonl", "honest")
    srv.alerts.subscribers.add(uid)
    s = srv.session
    while not s.stream.done:
        s.step(25)
    inbox = c.get("/api/alerts").json()["inbox"]
    assert inbox, "price moves in the fixture must produce alerts"
    a = inbox[0]
    assert a["market"]["ask"] and a["expires_at"] and "not a prediction" in a["disclaimer"]
    assert a["delivered"] is False and a["suppressed_reason"] == "quiet hours"
    keys = [(x["rule_id"], x["market"]["contract_id"], x["market"]["ask"], x["created_at"])
            for x in inbox]
    assert len(keys) == len(set(keys))
    assert not any("buy" in x["title"].lower() or "win" in x["title"].lower() for x in inbox)
    del create_app


def test_account_deletion_purges_documents_and_ends_sessions(app):
    srv = app.state.server
    c, uid = user(app, "frank@example.com")
    c.put("/api/account/watchlist", headers=H, json={"items": [{"kind": "game", "id": "G1"}]})
    assert c.request("DELETE", "/api/account", json={"password": "nope nope nope"},
                     headers=H).status_code == 401
    r = c.request("DELETE", "/api/account", json={"password": "correct horse 1"}, headers=H)
    assert r.status_code == 200
    assert srv.accounts.store.docs(uid) == {}
    assert c.get("/api/account/me").json()["signed_in"] is False
    assert TestClient(app).post("/api/account/login", json={
        "email": "frank@example.com", "password": "correct horse 1"}).status_code == 401
    assert any(a["action"] == "DELETED" for a in srv.accounts.store.audit_for(uid))
