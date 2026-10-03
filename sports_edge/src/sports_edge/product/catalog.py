"""Canonical schedule catalog: competitions, events, participants, market links.

Identity rules:
* A canonical event is created from one provider's event id. The same provider id
  always maps to the same canonical event (stable across reschedules).
* Two providers' events are linked only through an explicit crosswalk that matches
  canonical participant ids, the date and, for doubleheaders, the game number.
  Display names are never used to match (two "Rangers" exist; tennis has duplicate
  surnames).
* An event missing from a later fetch is not deleted: it is marked unconfirmed by that
  source. A failed fetch changes nothing and is reported as a failure, so a broken
  schedule source can never produce a misleadingly empty slate.
* Reschedules keep history; cancellations and postponements keep the event with its
  status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sports_edge.domain.records import stable_id


@dataclass(frozen=True)
class DiscoveredEvent:
    """One provider's view of an event (already normalized by the source adapter)."""

    source: str
    provider_event_id: str
    sport: str
    competition: str
    season: str
    home: str  # canonical participant id (via the source's alias table), never a name
    away: str
    scheduled_start: datetime  # timezone-aware UTC
    status: str  # SCHEDULED / LIVE / FINAL / POSTPONED / CANCELLED / SUSPENDED
    round: str | None = None
    game_number: int | None = None  # doubleheaders
    names: tuple[tuple[str, str], ...] = ()


@dataclass
class CanonicalEvent:
    event_id: str
    sport: str
    competition: str
    season: str
    home: str
    away: str
    scheduled_start: datetime
    status: str
    round: str | None
    game_number: int | None
    provider_ids: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    reschedules: list[dict] = field(default_factory=list)
    confirmed_by: dict[str, datetime] = field(default_factory=dict)  # source -> last fetch
    unconfirmed_by: dict[str, datetime] = field(default_factory=dict)


@dataclass
class Catalog:
    events: dict[str, CanonicalEvent] = field(default_factory=dict)
    by_provider: dict[tuple[str, str], str] = field(default_factory=dict)

    def reconcile(self, source: str, found: list[DiscoveredEvent], fetched_at: datetime,
                  window: tuple[datetime, datetime] | None = None) -> dict:
        created = updated = rescheduled = 0
        seen: set[str] = set()
        for d in found:
            if d.scheduled_start.tzinfo is None:
                raise ValueError("scheduled_start must be timezone-aware")
            key = (source, d.provider_event_id)
            eid = self.by_provider.get(key)
            if eid is None:
                eid = self._crosswalk(d)
            if eid is None:
                eid = stable_id("ev", [source, d.provider_event_id])
                self.events[eid] = CanonicalEvent(
                    eid, d.sport, d.competition, d.season, d.home, d.away, d.scheduled_start,
                    d.status, d.round, d.game_number)
                created += 1
            ev = self.events[eid]
            self.by_provider[key] = eid
            ev.provider_ids[source] = d.provider_event_id
            ev.names.update(dict(d.names))
            if ev.scheduled_start != d.scheduled_start:
                ev.reschedules.append({"from": ev.scheduled_start.isoformat(),
                                       "to": d.scheduled_start.isoformat(),
                                       "source": source, "seen_at": fetched_at.isoformat()})
                ev.scheduled_start = d.scheduled_start
                rescheduled += 1
            if ev.status != d.status:
                ev.status = d.status
                updated += 1
            ev.confirmed_by[source] = fetched_at
            ev.unconfirmed_by.pop(source, None)
            seen.add(eid)
        missing = 0
        for ev in self.events.values():
            if source in ev.provider_ids and ev.event_id not in seen:
                in_window = window is None or window[0] <= ev.scheduled_start < window[1]
                if in_window:
                    ev.unconfirmed_by[source] = fetched_at
                    missing += 1
        return {"created": created, "updated": updated, "rescheduled": rescheduled,
                "missing_from_fetch": missing, "events": len(found)}

    def _crosswalk(self, d: DiscoveredEvent) -> str | None:
        """Link to an existing event from another source only on exact canonical identity."""
        for ev in self.events.values():
            if (ev.sport, ev.home, ev.away, ev.game_number) != (d.sport, d.home, d.away,
                                                                 d.game_number):
                continue
            if d.source in ev.provider_ids:
                continue  # the same source never has two ids for one event
            if abs((ev.scheduled_start - d.scheduled_start).total_seconds()) <= 6 * 3600:
                return ev.event_id
        return None

    def counts(self, sport: str | None = None) -> dict:
        evs = [e for e in self.events.values() if sport is None or e.sport == sport]
        return {"discovered": len(evs),
                "cancelled_or_postponed": sum(e.status in ("CANCELLED", "POSTPONED")
                                              for e in evs)}
