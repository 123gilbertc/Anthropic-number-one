from fastapi.testclient import TestClient

from sports_edge.api.app import create_app

H = {"X-SE-Request": "1"}


def test_api_honest_mode_shows_limitations_and_no_alerts():
    app = create_app(train_games=150)
    app.state.server.db = app.state.server.session.db = None
    c = TestClient(app)
    h = c.get("/api/health").json()
    assert "SYNTHETIC DEMO" in h["banners"] and "NO MODEL LOADED" in h["banners"]
    assert any("NOT CONNECTED" in b for b in h["banners"])
    assert any("PAPER ONLY" in b for b in h["banners"])
    assert h["authenticated"] is False and h["session"]["auto_paper"] is False
    assert c.get("/api/signals").json() == []
    live = [s for s in c.get("/api/sources").json()["registry"] if s["role"] == "live game feed"]
    assert live and live[0]["live_use"] == "BLOCKED"


def test_api_mechanics_mode_is_labelled():
    app = create_app(train_games=150)
    app.state.server.db = app.state.server.session.db = None
    c = TestClient(app)
    c.post("/api/auth/login", json={"token": app.state.server.auth.token})
    c.post("/api/session", json={"mode": "mechanics"}, headers=H)
    c.post("/api/session/step", json={"n": 5000}, headers=H)
    h = c.get("/api/health").json()
    assert any("MECHANICS DEMO" in b for b in h["banners"])
    assert any("UNVALIDATED MODEL" in b for b in h["banners"])
    for s in c.get("/api/signals").json():
        assert any("MECHANICS DEMO" in n for n in s["decision"]["notes"])
    assert c.post("/api/session", json={"fixture": "../pyproject.toml"},
                  headers=H).status_code == 404
