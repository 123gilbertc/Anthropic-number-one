"""FastAPI backend: the single source of truth for every screen.

Reads are open on localhost. Commands (session control, connection config,
paper orders) require an authenticated operator session (see api/auth.py).
The server never reports a status it has not established.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sports_edge.api.auth import COOKIE, Auth
from sports_edge.api.contracts import (
    GameIntel,
    Health,
    Ledger,
    OrderRequest,
    OrderResponse,
    Preview,
    PreviewRequest,
    SessionInfo,
)
from sports_edge.config import ROOT, settings
from sports_edge.connections import SecretStore, build_providers, readiness
from sports_edge.domain.enums import ModelStatus
from sports_edge.domain.explain import explain
from sports_edge.forecast.train import train_synthetic
from sports_edge.paper.broker import APPROVING
from sports_edge.session import AppSession, CommandError, recover
from sports_edge.sources import CHECKED, SOURCES

FIXTURES = ROOT / "fixtures"
WEB_DIST = ROOT / "web" / "dist"


class LoginRequest(BaseModel):
    token: str


class SessionRequest(BaseModel):
    fixture: str = "nhl_synthetic_dip.jsonl"
    mode: Literal["honest", "mechanics"] = "honest"


class RunRequest(BaseModel):
    speed: float = 60.0


class StepRequest(BaseModel):
    n: int = 1


class SecretRequest(BaseModel):
    name: str
    value: str


class Server:
    def __init__(self, *, train_games: int = 300) -> None:
        self.settings = settings()
        self.auth = Auth(self.settings.runs_dir)
        self.store = SecretStore(ROOT / "secrets.local.json")
        self.providers = build_providers(self.settings)
        self.train_games = train_games
        self.db = self._db()
        # Restart recovery: reload persisted orders; never-filled PENDING orders -> ABANDONED.
        self.prior_keys: dict = {}
        self.recovery = {"abandoned": [], "orders_loaded": 0}
        if self.db is not None:
            rec = recover(self.db, datetime.now(UTC))
            self.prior_keys = rec["by_key"]
            self.recovery = {"abandoned": rec["abandoned"], "orders_loaded": len(rec["orders"])}
        self.session: AppSession = self._new_session("nhl_synthetic_dip.jsonl", "honest")

    def _db(self):
        """Persist paper orders + ledger if Postgres is reachable and migrated; else memory."""
        try:
            from sqlalchemy import text

            from sports_edge.storage.repository import SqlSink, make_engine
            eng = make_engine(self.settings.database_url)
            with eng.connect() as c:
                c.execute(text("select 1 from paper_orders limit 1"))
            return SqlSink(eng)
        except Exception:
            return None

    def _new_session(self, fixture: str, mode: str) -> AppSession:
        path = (FIXTURES / fixture).resolve()
        if path.parent != FIXTURES.resolve() or not path.exists():
            raise HTTPException(404, {"code": "UNKNOWN_FIXTURE", "detail": fixture})
        fc, info = None, None
        if mode == "mechanics":
            rep = train_synthetic(n_games=self.train_games)
            fc = rep.forecaster
            info = fc.version.model_dump(mode="json") | {"test_metrics": rep.test_metrics}
        s = AppSession.replay(path, fc, mode == "mechanics", info, reviewer=self._reviewer())
        s.db = self.db
        old = getattr(self, "session", None)
        if old is not None:  # keys from the replaced session stay recognised
            self.prior_keys.update({o.idempotency_key: o for o in old.orders.values()})
        s.prior_keys = self.prior_keys
        return s

    def _reviewer(self):
        p = self.providers["anthropic"]
        if p.status(self.store, datetime.now(UTC)) != "CONNECTED":
            return None  # optional: never required for monitoring
        from sports_edge.llm.anthropic_provider import AnthropicProvider
        from sports_edge.llm.review import ShadowReviewer
        s = self.settings
        if s.llm_anthropic_input_usd_per_mtok is None or s.llm_anthropic_output_usd_per_mtok is None:
            return None
        return ShadowReviewer([AnthropicProvider(
            self.store.get("LLM_ANTHROPIC_API_KEY") or "",
            self.store.get("LLM_ANTHROPIC_MODEL") or "",
            s.llm_anthropic_input_usd_per_mtok, s.llm_anthropic_output_usd_per_mtok)],
            monthly_budget_usd=s.llm_monthly_budget_usd)

    def banners(self) -> list[str]:
        s = self.session
        out = ["SYNTHETIC DEMO" if s.data_label == "SYNTHETIC" else "REPLAY"]
        out.append("LIVE GAME FEED: NOT CONNECTED (BLOCKED)")
        if s.model_info is None:
            out.append("NO MODEL LOADED")
        elif s.model_info.get("status") != ModelStatus.VALIDATED.value:
            out.append(f"UNVALIDATED MODEL ({s.model_info.get('status')})")
        if s.monitor.engine.demo_label:
            out.append("MECHANICS DEMO: signals are not evidence of value")
        out.append("PAPER ONLY: no real-money execution exists")
        if self.db is None:
            out.append("LEDGER NOT PERSISTED (database unavailable): in-memory only")
        if self.recovery["abandoned"]:
            out.append(f"{len(self.recovery['abandoned'])} PAPER ORDER(S) ABANDONED BY A RESTART "
                       "(never filled)")
        return out


def _err(e: CommandError) -> JSONResponse:
    return JSONResponse({"code": e.code, "detail": e.detail}, status_code=e.status)


def create_app(*, train_games: int = 300) -> FastAPI:
    app = FastAPI(title="sports_edge", version="0.2.0")
    srv = Server(train_games=train_games)
    app.state.server = srv

    def authed(request: Request) -> None:
        srv.auth.require(request)

    # ------------------------------------------------------------------ auth

    @app.post("/api/auth/login")
    def login(req: LoginRequest, response: Response):
        sid = srv.auth.login(req.token)
        response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", secure=False)
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        srv.auth.sessions.discard(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE)
        return {"authenticated": False}

    # ------------------------------------------------------------------ status

    @app.get("/api/health", response_model=Health)
    def health(request: Request):
        s = srv.session
        return Health(banners=srv.banners(), session=SessionInfo(**s.summary()),
                      authenticated=srv.auth.is_authenticated(request),
                      event_seq=s.events.seq, server_time=datetime.now(UTC))

    @app.get("/api/connections")
    def connections():
        now = datetime.now(UTC)
        model_status = (srv.session.model_info or {}).get("status")
        return {"providers": [p.view(srv.store, now) for p in srv.providers.values()],
                "readiness": readiness(srv.providers, srv.store, now, model_status),
                "docs_checked": CHECKED, "registry": SOURCES}

    def _provider(pid: str):
        p = srv.providers.get(pid)
        if p is None:
            raise HTTPException(404, {"code": "UNKNOWN_PROVIDER", "detail": pid})
        return p

    @app.post("/api/connections/{pid}/test", dependencies=[Depends(authed)])
    async def test_connection(pid: str):
        p = _provider(pid)
        now = datetime.now(UTC)
        if p.blocked_reason:
            raise HTTPException(409, {"code": "BLOCKED", "detail": p.blocked_reason})
        if p.status(srv.store, now) == "NOT_CONFIGURED":
            raise HTTPException(409, {"code": "NOT_CONFIGURED",
                                      "detail": "set the required secrets first"})
        assert p.test is not None
        p.disabled = False
        p.last_test = await p.test(srv.store)
        return p.view(srv.store, datetime.now(UTC))

    @app.post("/api/connections/{pid}/disconnect", dependencies=[Depends(authed)])
    def disconnect(pid: str):
        p = _provider(pid)
        p.disabled, p.last_test = True, None
        return p.view(srv.store, datetime.now(UTC))

    @app.put("/api/connections/{pid}/secret", dependencies=[Depends(authed)])
    def set_secret(pid: str, req: SecretRequest):
        p = _provider(pid)
        if req.name not in p.required_secrets:
            raise HTTPException(422, {"code": "UNKNOWN_SECRET", "detail": req.name})
        srv.store.set(req.name, req.value)
        p.last_test = None  # a new key is untested until a real test passes
        return p.view(srv.store, datetime.now(UTC))

    @app.delete("/api/connections/{pid}/secret/{name}", dependencies=[Depends(authed)])
    def delete_secret(pid: str, name: str):
        p = _provider(pid)
        if name not in p.required_secrets:
            raise HTTPException(422, {"code": "UNKNOWN_SECRET", "detail": name})
        srv.store.delete(name)
        p.last_test = None
        return p.view(srv.store, datetime.now(UTC))

    # ------------------------------------------------------------------ session control

    @app.get("/api/session", response_model=SessionInfo)
    def session_info():
        return SessionInfo(**srv.session.summary())

    @app.post("/api/session", dependencies=[Depends(authed)], response_model=SessionInfo)
    def new_session(req: SessionRequest):
        srv.session.pause()
        srv.session = srv._new_session(req.fixture, req.mode)
        return SessionInfo(**srv.session.summary())

    @app.post("/api/session/run", dependencies=[Depends(authed)], response_model=SessionInfo)
    async def run(req: RunRequest):
        srv.session.start(req.speed)
        await asyncio.sleep(0)
        return SessionInfo(**srv.session.summary())

    @app.post("/api/session/pause", dependencies=[Depends(authed)], response_model=SessionInfo)
    def pause():
        srv.session.pause()
        return SessionInfo(**srv.session.summary())

    @app.post("/api/session/step", dependencies=[Depends(authed)], response_model=SessionInfo)
    def step(req: StepRequest):
        if srv.session.running:
            raise HTTPException(409, {"code": "RUNNING", "detail": "pause before stepping"})
        srv.session.step(max(1, min(req.n, 5000)))
        return SessionInfo(**srv.session.summary())

    # ------------------------------------------------------------------ games

    def _intel(game_id: str) -> GameIntel:
        s = srv.session
        if game_id not in s.monitor.games:
            raise HTTPException(404, {"code": "UNKNOWN_GAME", "detail": game_id})
        v = s.monitor.game_view(game_id)
        st = s.monitor.games[game_id].reducer.state
        now = s.clock.now()
        return GameIntel(**v, as_of=now, event_seq=s.events.seq,
                         state_age_seconds=None if st is None
                         else (now - st.as_of_received_time).total_seconds())

    @app.get("/api/games", response_model=list[GameIntel])
    def games():
        return [_intel(g) for g in srv.session.monitor.games]

    @app.get("/api/games/{game_id}", response_model=GameIntel)
    def game(game_id: str):
        return _intel(game_id)

    @app.get("/api/decisions")
    def decisions(game_id: str | None = None, limit: int = 500):
        ds = [d for d in srv.session.monitor.sink.decisions  # type: ignore[attr-defined]
              if game_id is None or d.game_id == game_id]
        return [d.model_dump(mode="json") | {"explanation": explain(d.reasons)}
                for d in ds[-limit:]]

    @app.get("/api/signals")
    def signals():
        s = srv.session
        return [s.signal_view(d.decision_id) | {"explanation": explain(d.reasons)}
                for d in s.monitor.alerts]

    # ------------------------------------------------------------------ paper workflow

    @app.post("/api/paper/preview", dependencies=[Depends(authed)], response_model=Preview)
    def preview(req: PreviewRequest):
        s = srv.session
        rt = s.monitor.games.get(req.game_id)
        if rt is None or not any(m.contract_id == req.contract_id for m in rt.mappings):
            raise HTTPException(404, {"code": "UNKNOWN_CONTRACT", "detail": req.contract_id})
        with s.lock:
            d = s.monitor.preview(req.game_id, req.contract_id)
        now = s.clock.now()
        eligible = d.action in APPROVING
        return Preview(decision=d, eligible=eligible, expired=now > d.expires_at,
                       seconds_to_expiry=(d.expires_at - now).total_seconds(),
                       max_quantity=d.planned_quantity if eligible else 0,
                       estimated_cost=str(d.planned_cost),
                       estimated_fees=str(d.ev.expected_fees) if d.ev else None,
                       explanation=explain(d.reasons))

    @app.post("/api/paper/orders", dependencies=[Depends(authed)], response_model=OrderResponse,
              responses={404: {}, 409: {}, 410: {}, 422: {}})
    def place_order(req: OrderRequest):
        try:
            o, created = srv.session.place_order(req.decision_id, req.idempotency_key,
                                                 req.quantity, req.expected_contract_id)
        except CommandError as e:
            return _err(e)
        return OrderResponse(order=o, created=created)

    @app.get("/api/paper/ledger", response_model=Ledger)
    def ledger():
        s = srv.session
        led = s.monitor.engine.ledger
        return Ledger(orders=list(s.orders.values()), events=s.ledger, cash=str(led.cash),
                      open_cost=str(led.open_cost),
                      limits={k: str(v) for k, v in led.limits.__dict__.items()},
                      fills=s.monitor.broker.fills)

    @app.get("/api/paper/history")
    def history(mode: Literal["REPLAY", "LIVE"] | None = None):
        """Persisted ledger across runs and restarts (empty when no database)."""
        if srv.db is None:
            return {"persisted": False, "events": [], "recovery": srv.recovery}
        return {"persisted": True, "events": srv.db.ledger_history(mode),
                "recovery": srv.recovery}

    @app.get("/api/evaluation")
    def evaluation():
        return srv.session.evaluation()

    @app.get("/api/models")
    def models():
        return {"active": srv.session.model_info,
                "note": "Synthetic-only models exercise plumbing; their metrics say nothing "
                        "about real games."}

    @app.get("/api/reviews")
    def reviews():
        return srv.session.reviews

    @app.get("/api/sources")
    def sources():
        s = srv.session
        now = s.clock.now()
        return {"checked": CHECKED, "registry": SOURCES,
                "runtime": [h.summary(now) for h in s.monitor.health.values()]}

    # ------------------------------------------------------------------ live stream

    @app.get("/api/stream")
    async def stream(request: Request, since: int | None = None):
        last = request.headers.get("last-event-id")
        cursor = since if since is not None else int(last) if last and last.isdigit() else None
        sess = srv.session

        async def gen():
            nonlocal cursor
            log = sess.events
            if cursor is None:
                cursor = log.seq
                yield f"id: {cursor}\nevent: hello\ndata: {json.dumps({'seq': cursor})}\n\n"
            while True:
                if await request.is_disconnected() or srv.session is not sess:
                    if srv.session is not sess:
                        yield "event: resync\ndata: {\"reason\": \"session replaced\"}\n\n"
                    return
                # Register for wake-up BEFORE reading the log; otherwise an event appended
                # between the read and the registration would sleep until the heartbeat.
                w = asyncio.Event()
                log.add_waiter(w)
                try:
                    resync, evs = log.since(cursor)
                    if resync:
                        cursor = log.seq
                        yield f"id: {cursor}\nevent: resync\ndata: {{\"reason\": \"gap\"}}\n\n"
                    for seq, kind, data in evs:
                        cursor = seq
                        yield (f"id: {seq}\nevent: {kind}\n"
                               f"data: {json.dumps(data, default=str)}\n\n")
                    if not evs and not resync:
                        try:
                            await asyncio.wait_for(w.wait(), timeout=10)
                        except TimeoutError:
                            yield ": heartbeat\n\n"
                finally:
                    log.discard_waiter(w)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ static UI

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(WEB_DIST / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory entry point
    return create_app()
