from fastapi.testclient import TestClient

from sports_edge.api.app import create_app


def test_api_honest_mode_shows_limitations_and_no_alerts():
    c = TestClient(create_app())
    h = c.get("/api/health").json()
    assert "SYNTHETIC DEMO" in h["banners"] and "NO MODEL LOADED" in h["banners"]
    assert any("NOT CONNECTED" in b for b in h["banners"])
    assert c.get("/api/alerts").json() == []
    g = c.get("/api/games").json()[0]
    assert g["value_side"] is None
    assert all(r["model_status"] == "NO_PREDICTION" for r in g["contracts"])
    live = [s for s in c.get("/api/sources").json()["registry"] if s["role"] == "live game feed"]
    assert live and live[0]["live_use"] == "BLOCKED"


def test_api_mechanics_mode_is_labelled():
    c = TestClient(create_app())
    c.post("/api/replay", json={"mode": "mechanics"})
    h = c.get("/api/health").json()
    assert any("MECHANICS DEMO" in b for b in h["banners"])
    assert any("UNVALIDATED MODEL" in b for b in h["banners"])
    for a in c.get("/api/alerts").json():
        assert any("MECHANICS DEMO" in n for n in a["decision"]["notes"])
    assert c.post("/api/replay", json={"fixture": "../pyproject.toml"}).status_code == 404
