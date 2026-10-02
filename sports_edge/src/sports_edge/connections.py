"""Provider connections: configuration, real connection tests, capability checks.

A saved key is not a working integration. Status is CONNECTED only after a
real test succeeded recently. Secrets live server-side (environment or
``secrets.local.json``, mode 0600, git-ignored) and are never returned to the
browser: views expose only "configured" and the last four characters.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

TEST_TTL = timedelta(minutes=15)


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def get(self, name: str) -> str | None:
        return os.environ.get(name) or self._load().get(name) or None

    def source(self, name: str) -> str | None:
        if os.environ.get(name):
            return "environment"
        return "server secrets file" if self._load().get(name) else None

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self.path.write_text(json.dumps(data))
        os.chmod(self.path, 0o600)

    def delete(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self.path.write_text(json.dumps(data))
        os.chmod(self.path, 0o600)


@dataclass
class TestResult:
    ok: bool
    time: datetime
    latency_ms: int | None
    detail: str
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass
class Provider:
    id: str
    name: str
    role: str
    required_secrets: tuple[str, ...]
    blocked_reason: str | None
    test: Callable[[SecretStore], Awaitable[TestResult]] | None
    optional: bool = False
    disabled: bool = False
    last_test: TestResult | None = None

    def status(self, store: SecretStore, now: datetime) -> str:
        if self.blocked_reason:
            return "BLOCKED"
        if self.disabled:
            return "DISCONNECTED"
        if any(store.get(s) is None for s in self.required_secrets):
            return "NOT_CONFIGURED"
        if self.last_test is None:
            return "CONFIGURED_UNTESTED"
        if not self.last_test.ok:
            return "FAILED"
        if now - self.last_test.time > TEST_TTL:
            return "TEST_STALE"
        return "CONNECTED"

    def view(self, store: SecretStore, now: datetime) -> dict:
        def mask(s: str) -> dict:
            v = store.get(s)
            return {"name": s, "configured": v is not None, "source": store.source(s),
                    "hint": f"…{v[-4:]}" if v and len(v) > 8 else None}

        lt = self.last_test
        return {
            "id": self.id, "name": self.name, "role": self.role, "optional": self.optional,
            "status": self.status(store, now), "blocked_reason": self.blocked_reason,
            "secrets": [mask(s) for s in self.required_secrets],
            "last_test": None if lt is None else {
                "ok": lt.ok, "time": lt.time.isoformat(), "latency_ms": lt.latency_ms,
                "detail": lt.detail, "capabilities": lt.capabilities,
                "age_seconds": (now - lt.time).total_seconds()},
        }


def _err(e: Exception) -> str:
    msg = f"{type(e).__name__}: {e}"
    if "403" in msg and "CONNECT" in msg.upper():
        msg += " (outbound host blocked by this environment's network policy)"
    return msg[:500]


async def _timed(fn) -> TestResult:
    t0 = time.monotonic()
    try:
        ok, detail, caps = await fn()
    except Exception as e:  # every failure is reported with its real error
        return TestResult(False, datetime.now(UTC), int((time.monotonic() - t0) * 1000), _err(e))
    return TestResult(ok, datetime.now(UTC), int((time.monotonic() - t0) * 1000), detail, caps)


def build_providers(settings) -> dict[str, Provider]:
    async def t_postgres(store: SecretStore) -> TestResult:
        async def go():
            from sqlalchemy import text

            from sports_edge.storage.repository import make_engine
            eng = make_engine(settings.database_url)
            with eng.connect() as c:
                v = c.execute(text("select version()")).scalar_one()
                head = c.execute(text("select version_num from alembic_version")).scalar()
            return True, v.split(",")[0], {"migration_head": head}
        return await _timed(go)

    async def t_kalshi_rest(store: SecretStore) -> TestResult:
        async def go():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{settings.kalshi_rest_base}/exchange/status")
                r.raise_for_status()
                caps: dict[str, Any] = {"exchange_status": r.json()}
                s = await c.get(f"{settings.kalshi_rest_base}/series/KXNHLGAME")
                caps["nhl_series_found"] = s.status_code == 200
                if s.status_code == 200:
                    caps["nhl_series"] = {k: v for k, v in s.json().get("series", {}).items()
                                          if "fee" in k or k in ("ticker", "title")}
            return True, "public market data reachable", caps
        return await _timed(go)

    async def t_kalshi_ws(store: SecretStore) -> TestResult:
        async def go():
            import websockets

            from sports_edge.adapters.kalshi import WS_PATH, sign_headers
            pem = Path(store.get("KALSHI_PRIVATE_KEY_PATH") or "").read_bytes()
            headers = sign_headers(store.get("KALSHI_KEY_ID") or "", pem, "GET", WS_PATH)
            async with websockets.connect(settings.kalshi_ws_url, additional_headers=headers,
                                          open_timeout=10):
                pass
            return True, "authenticated WebSocket handshake succeeded", {}
        return await _timed(go)

    async def t_odds(store: SecretStore) -> TestResult:
        async def go():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.the-odds-api.com/v4/sports",
                                params={"apiKey": store.get("ODDS_API_KEY")})
                r.raise_for_status()
            keys = {s.get("key") for s in r.json()}
            caps = {"icehockey_nhl_listed": "icehockey_nhl" in keys,
                    "baseball_mlb_listed": "baseball_mlb" in keys,
                    "requests_remaining": r.headers.get("x-requests-remaining")}
            return True, "key accepted", caps
        return await _timed(go)

    async def t_anthropic(store: SecretStore) -> TestResult:
        async def go():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.anthropic.com/v1/models",
                                headers={"x-api-key": store.get("LLM_ANTHROPIC_API_KEY") or "",
                                         "anthropic-version": "2023-06-01"})
                r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", [])]
            want = store.get("LLM_ANTHROPIC_MODEL")
            ok = want in ids
            return ok, ("configured model available" if ok
                        else f"configured model {want!r} not in account's model list"), \
                {"models": ids[:20]}
        return await _timed(go)

    return {p.id: p for p in [
        Provider("postgres", "PostgreSQL", "storage of record", (), None, t_postgres),
        Provider("kalshi_rest", "Kalshi market data (REST)", "contract discovery, rules, fees",
                 (), None, t_kalshi_rest),
        Provider("kalshi_ws", "Kalshi order-book stream (WebSocket)", "live executable prices",
                 ("KALSHI_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"), None, t_kalshi_ws),
        Provider("odds_api", "The Odds API", "sportsbook reference quotes", ("ODDS_API_KEY",),
                 None, t_odds, optional=True),
        Provider("nhl_live_feed", "Live NHL game state", "live game feed", (),
                 "No licensed live NHL play-by-play source has passed coverage, freshness and "
                 "licensing checks (see DATA_SOURCES.md). Live value signals stay blocked.",
                 None),
        Provider("anthropic", "Anthropic (LLM shadow review)", "optional review, zero weight",
                 ("LLM_ANTHROPIC_API_KEY", "LLM_ANTHROPIC_MODEL"), None, t_anthropic,
                 optional=True),
    ]}


def readiness(providers: dict[str, Provider], store: SecretStore, now: datetime,
              model_status: str | None) -> list[dict]:
    st = {k: p.status(store, now) for k, p in providers.items()}

    def cap(name: str, needs: list[tuple[str, bool]], note: str = "") -> dict:
        missing = [n for n, ok in needs if not ok]
        return {"capability": name, "ready": not missing, "blocked_by": missing, "note": note}

    return [
        cap("Replay research", [("replay fixture", True)],
            "Uses recorded or synthetic files only. Labelled REPLAY / SYNTHETIC."),
        cap("Live market recording", [("kalshi_ws CONNECTED", st["kalshi_ws"] == "CONNECTED")]),
        cap("Live game monitoring", [("nhl_live_feed CONNECTED", st["nhl_live_feed"] == "CONNECTED")]),
        cap("Live value signals", [
            ("nhl_live_feed CONNECTED", st["nhl_live_feed"] == "CONNECTED"),
            ("kalshi_ws CONNECTED", st["kalshi_ws"] == "CONNECTED"),
            ("VALIDATED model", model_status == "VALIDATED")]),
        cap("Sportsbook reference comparison", [("odds_api CONNECTED", st["odds_api"] == "CONNECTED")],
            "Optional."),
        cap("LLM shadow review", [("anthropic CONNECTED", st["anthropic"] == "CONNECTED")],
            "Optional; zero decision weight; never blocks monitoring."),
    ]
