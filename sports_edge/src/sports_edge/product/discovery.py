"""Server-side schedule and market discovery, supervised independently of any browser.

Sources are adapters behind one interface. A source that is not configured says so
(NOT_CONFIGURED) and contributes nothing; a source that fails keeps the previous
catalog and records the real error (FAILED). Neither case is ever shown as "no games".

Provider endpoints are *not* hard-coded from memory: the operator sets the verified
endpoint template and API version per provider after reading the current docs
(``SPORTRADAR_<SPORT>_SCHEDULE_URL`` etc.). Payload parsing for a provider is enabled
only after a recorded sample has been checked (``schema_verified``); until then the
source reports BLOCKED with that reason. This keeps provider facts at their labels.

Cadence: schedules every ``schedule_every`` (default 10 min) over a window of
[now - 12h, now + 36h] in UTC, which covers every local-date boundary for the slate;
market discovery every ``market_every`` (default 2 min).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sports_edge.product.catalog import Catalog, DiscoveredEvent


@dataclass
class SourceResult:
    source: str
    sport: str
    kind: str  # schedule / market
    status: str  # OK / FAILED / NOT_CONFIGURED / BLOCKED / FIXTURE
    checked_at: datetime | None
    found: int | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)
    label: str = "UNKNOWN"

    def view(self) -> dict:
        return {"source": self.source, "sport": self.sport, "kind": self.kind,
                "status": self.status,
                "checked_at": self.checked_at.isoformat() if self.checked_at else None,
                "found": self.found, "error": self.error, "detail": self.detail,
                "label": self.label}


class ScheduleSource(Protocol):
    name: str
    sport: str
    kind: str

    def configured(self) -> tuple[bool, str]: ...

    async def fetch(self, start: datetime, end: datetime) -> list[DiscoveredEvent]: ...


@dataclass
class ProviderSchedule:
    """Sportradar / SportsDataIO schedule adapter contract.

    ``url_template`` (operator-verified) and ``api_key`` are required; ``parser`` turns a
    verified payload into DiscoveredEvents and is registered only after a recorded
    sample has been checked against the provider's docs.
    """

    name: str
    sport: str
    url_template: str | None
    api_key: str | None
    parser: Callable[[Any], list[DiscoveredEvent]] | None = None
    kind: str = "schedule"
    label: str = "SNIPPET"

    def configured(self) -> tuple[bool, str]:
        if not self.url_template:
            return False, "endpoint template not set (operator must verify the current " \
                          "docs and API version first)"
        if not self.api_key:
            return False, "API key not configured"
        if self.parser is None:
            return False, "BLOCKED: payload schema not verified against a recorded sample"
        return True, ""

    async def fetch(self, start: datetime, end: datetime) -> list[DiscoveredEvent]:
        import httpx

        out: list[DiscoveredEvent] = []
        day = start.date()
        async with httpx.AsyncClient(timeout=15) as c:
            while day <= end.date():
                url = self.url_template.format(date=day.isoformat(),  # type: ignore[union-attr]
                                               yyyy=day.year, mm=f"{day.month:02d}",
                                               dd=f"{day.day:02d}")
                r = await c.get(url, headers={"x-api-key": self.api_key or ""})
                r.raise_for_status()
                out += self.parser(r.json())  # type: ignore[misc]
                day += timedelta(days=1)
        return [e for e in out if start <= e.scheduled_start < end or e.status == "LIVE"]


@dataclass
class KalshiMarketSource:
    """Public Kalshi market discovery for one series (endpoint shape: SNIPPET)."""

    name: str
    sport: str
    series: str | None  # e.g. KXNHLGAME (SNIPPET); None = series ticker UNKNOWN
    rest_base: str
    kind: str = "market"
    label: str = "SNIPPET"
    markets: list[dict] = field(default_factory=list)

    def configured(self) -> tuple[bool, str]:
        if not self.series:
            return False, f"Kalshi series ticker for {self.sport} game markets is UNKNOWN"
        return True, ""

    async def fetch(self, start: datetime, end: datetime) -> list[DiscoveredEvent]:
        from sports_edge.adapters.kalshi_rest import list_game_markets

        self.markets = await list_game_markets(self.rest_base, self.series or "")
        return []  # markets are linked to events only through a verified alias table


@dataclass
class FixtureSchedule:
    name: str
    sport: str
    events: list[DiscoveredEvent]
    kind: str = "schedule"
    label: str = "SYNTHETIC"

    def configured(self) -> tuple[bool, str]:
        return True, ""

    async def fetch(self, start: datetime, end: datetime) -> list[DiscoveredEvent]:
        return list(self.events)


@dataclass
class DiscoveryService:
    sources: list[Any]
    catalog: Catalog = field(default_factory=Catalog)
    results: dict[str, SourceResult] = field(default_factory=dict)
    schedule_every: timedelta = timedelta(minutes=10)
    market_every: timedelta = timedelta(minutes=2)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _task: asyncio.Task | None = None
    on_change: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)

    def window(self, now: datetime) -> tuple[datetime, datetime]:
        return now - timedelta(hours=12), now + timedelta(hours=36)

    async def run_source(self, src) -> SourceResult:
        now = self.clock()
        ok, why = src.configured()
        status_if_not = "BLOCKED" if why.startswith("BLOCKED") else "NOT_CONFIGURED"
        if not ok:
            r = SourceResult(src.name, src.sport, src.kind, status_if_not, now, None, why,
                             label=src.label)
            self.results[src.name] = r
            return r
        start, end = self.window(now)
        try:
            found = await src.fetch(start, end)
        except Exception as e:  # the catalog keeps its previous state; the error is shown
            r = SourceResult(src.name, src.sport, src.kind, "FAILED", now, None,
                             f"{type(e).__name__}: {e}"[:300], label=src.label)
            self.results[src.name] = r
            return r
        detail = {}
        if src.kind == "schedule":
            detail = self.catalog.reconcile(src.name, found, now, (start, end))
        else:
            detail = {"markets": len(getattr(src, "markets", [])),
                      "mapped_to_events": 0,
                      "note": "markets are linked to events only via a verified participant "
                              "alias table; none is configured"}
        status = "FIXTURE" if src.label == "SYNTHETIC" else "OK"
        r = SourceResult(src.name, src.sport, src.kind, status, now,
                         len(found) if src.kind == "schedule" else detail["markets"], None,
                         detail, src.label)
        self.results[src.name] = r
        return r

    async def run_once(self) -> list[SourceResult]:
        return [await self.run_source(s) for s in self.sources]

    async def supervise(self) -> None:
        """Runs forever; each source on its own cadence; one failure never stops others."""
        due: dict[str, datetime] = {}
        while True:
            now = self.clock()
            for s in self.sources:
                if due.get(s.name, now) <= now:
                    await self.run_source(s)
                    every = self.schedule_every if s.kind == "schedule" else self.market_every
                    due[s.name] = now + every
            await asyncio.sleep(5)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.supervise())

    def by_sport(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.results.values():
            d = out.setdefault(r.sport, {"sources": [], "schedule_known": False})
            d["sources"].append(r.view())
            if r.kind == "schedule" and r.status in ("OK", "FIXTURE"):
                d["schedule_known"] = True
        return out


def default_sources(settings, store) -> list[Any]:
    """Live sources. Nothing here is configured until the operator supplies verified
    endpoints, keys and schema checks; that is the expected state in development."""
    def get(name: str) -> str | None:
        return store.get(name) or getattr(settings, name.lower(), None)

    srcs: list[Any] = []
    for sport in ("NHL", "NFL", "MLB", "TENNIS"):
        srcs.append(ProviderSchedule(f"sportradar_{sport.lower()}_schedule", sport,
                                     get(f"SPORTRADAR_{sport}_SCHEDULE_URL"),
                                     get("SPORTRADAR_API_KEY")))
        srcs.append(ProviderSchedule(f"sportsdataio_{sport.lower()}_schedule", sport,
                                     get(f"SPORTSDATAIO_{sport}_SCHEDULE_URL"),
                                     get("SPORTSDATAIO_API_KEY")))
    series = {"NHL": "KXNHLGAME", "MLB": "KXMLBGAME", "NFL": None, "TENNIS": None}
    for sport, s in series.items():
        srcs.append(KalshiMarketSource(f"kalshi_{sport.lower()}_markets", sport, s,
                                       settings.kalshi_rest_base))
    return srcs
