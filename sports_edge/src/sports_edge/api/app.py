"""FastAPI backend for the dashboard.

The server never claims a status it has not established: banners are derived
from actual source statuses and model statuses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sports_edge.config import ROOT, settings
from sports_edge.domain.enums import ModelStatus
from sports_edge.forecast.train import train_synthetic
from sports_edge.paper.broker import APPROVING
from sports_edge.replay.runner import ReplayResult, replay_file
from sports_edge.sources import CHECKED, SOURCES

FIXTURES = ROOT / "fixtures"
WEB_DIST = ROOT / "web" / "dist"


class ReplayRequest(BaseModel):
    fixture: str = "nhl_synthetic_dip.jsonl"
    mode: Literal["honest", "mechanics"] = "honest"


class AppState:
    def __init__(self) -> None:
        self.result: ReplayResult | None = None
        self.mode = "honest"
        self.model_metrics: dict | None = None
        self.model_version: dict | None = None

    def run(self, fixture: str, mode: str) -> None:
        path = (FIXTURES / fixture).resolve()
        if path.parent != FIXTURES.resolve() or not path.exists():
            raise HTTPException(404, "unknown fixture")
        forecaster = None
        if mode == "mechanics":
            rep = train_synthetic(n_games=300)
            forecaster = rep.forecaster
            self.model_metrics = rep.test_metrics
            self.model_version = forecaster.version.model_dump(mode="json")
        else:
            self.model_metrics, self.model_version = None, None
        self.result = replay_file(path, forecaster=forecaster, mechanics_demo=mode == "mechanics")
        self.mode = mode


def create_app() -> FastAPI:
    app = FastAPI(title="sports_edge research", version="0.1.0")
    st = AppState()
    st.run("nhl_synthetic_dip.jsonl", "honest")

    def res() -> ReplayResult:
        assert st.result is not None
        return st.result

    @app.get("/api/health")
    def health():
        r = res()
        now = r.monitor.clock.now()
        banners = []
        if r.status.value == "SYNTHETIC_DEMO":
            banners.append("SYNTHETIC DEMO")
        elif r.status.value == "REPLAY":
            banners.append("REPLAY")
        banners.append("LIVE GAME FEED: NOT CONNECTED (BLOCKED)")
        if st.model_version is None:
            banners.append("NO MODEL LOADED")
        elif st.model_version["status"] != ModelStatus.VALIDATED.value:
            banners.append(f"UNVALIDATED MODEL ({st.model_version['status']})")
        if st.mode == "mechanics":
            banners.append("MECHANICS DEMO: alerts below are not evidence of value")
        return {"mode": st.mode, "as_of": now.isoformat(), "banners": banners,
                "server_time": datetime.now(UTC).isoformat(),
                "discord_enabled": settings().discord_enabled}

    @app.get("/api/sources")
    def sources():
        r = res()
        now = r.monitor.clock.now()
        return {"checked": CHECKED, "registry": SOURCES,
                "runtime": [h.summary(now) for h in r.monitor.health.values()]}

    @app.get("/api/games")
    def games():
        r = res()
        return [r.monitor.game_view(gid) for gid in r.monitor.games]

    @app.get("/api/games/{game_id}")
    def game(game_id: str):
        r = res()
        if game_id not in r.monitor.games:
            raise HTTPException(404, "unknown game")
        return r.monitor.game_view(game_id)

    @app.get("/api/decisions")
    def decisions(game_id: str | None = None, limit: int = 500):
        ds = [d for d in res().sink.decisions if game_id is None or d.game_id == game_id]
        return [d.model_dump(mode="json") for d in ds[-limit:]]

    @app.get("/api/alerts")
    def alerts():
        r = res()
        out = []
        fills = {f.decision_id: f for f in r.sink.fills}
        attempts = {a.decision_id: a for a in r.monitor.broker.attempts}
        for d in r.sink.decisions:
            if d.action not in APPROVING:
                continue
            a = attempts.get(d.decision_id)
            out.append({
                "decision": d.model_dump(mode="json"),
                "fill": fills[d.decision_id].model_dump(mode="json")
                if d.decision_id in fills else None,
                "fill_outcome": [x.value for x in a.reasons] if a else ["PENDING"],
            })
        return out

    @app.get("/api/positions")
    def positions():
        r = res()
        led = r.monitor.engine.ledger
        return {
            "positions": [p.model_dump(mode="json") | {
                "average_entry": str(p.average_entry),
                "average_entry_all_in": str(p.average_entry_all_in)}
                for p in r.monitor.broker.positions.values()],
            "cash": str(led.cash), "open_cost": str(led.open_cost),
            "limits": {k: str(v) for k, v in led.limits.__dict__.items()},
            "settlements": [s.model_dump(mode="json") for s in r.sink.settlements],
        }

    @app.get("/api/models")
    def models():
        return {"active": st.model_version, "test_metrics": st.model_metrics,
                "note": "Synthetic-only models exercise plumbing; their metrics say nothing "
                        "about real games."}

    @app.get("/api/experiments")
    def experiments():
        p = settings().runs_dir / "experiments.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines()[-200:]]

    @app.post("/api/replay")
    def replay(req: ReplayRequest):
        st.run(req.fixture, req.mode)
        return res().summary()

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(WEB_DIST / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory entry point
    return create_app()

