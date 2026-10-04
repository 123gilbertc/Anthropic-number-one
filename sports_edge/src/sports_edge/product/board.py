"""Live Board data: every discovered game, with separate coverage counts.

Two data modes, never mixed:
* ``live``: the server-side catalog from configured discovery sources and live monitors.
  With no configured source the board says so per sport ("schedule unavailable: game
  count unknown"); it does not substitute fixtures.
* ``demo``: the replay workspace (synthetic or recorded). Every response carries the
  data label and banners so the UI can keep a persistent DEMO / REPLAY marker.

Counts are separate on purpose: discovered (schedule knows it), monitored (a runtime
is following it), forecast-ready (a model produced a usable estimate) and unavailable.
Unknown coverage is reported as unknown, not as zero games.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SPORTS = ("NHL", "NFL", "TENNIS", "MLB")


def local_window(day: str | None, tz: str, now: datetime) -> tuple[date, datetime, datetime]:
    try:
        z = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        z = ZoneInfo("UTC")
    d = date.fromisoformat(day) if day else now.astimezone(z).date()
    start = datetime.combine(d, time(0), z)
    return d, start, start + timedelta(days=1)


def _in_day(row_start: datetime, status: str, start: datetime, end: datetime) -> bool:
    return start <= row_start < end or status in ("LIVE", "AWAITING DATA")


def demo_board(session, assessor, day: str | None, tz: str) -> dict:
    now = session.clock.now()
    d, start, end = local_window(day, tz, now)
    rows = []
    for gid in session.monitor.games:
        r = assessor.board_row(gid, now)
        st = datetime.fromisoformat(r["event"]["scheduled_start"])
        if _in_day(st, r["event"]["status"], start, end):
            rows.append(r)
    sports = {}
    for sp in SPORTS:
        mine = [r for r in rows if r["event"]["sport"] == sp]
        all_games = [g for g in session.stream.games if g.sport.value == sp]
        sports[sp] = {
            "schedule_known": True, "source": "replay fixture",
            "discovered": len(mine), "monitored": len(mine),
            "forecast_ready": sum(1 for r in mine if r["forecast_ready"]),
            "unavailable": sum(1 for r in mine if r["assessment"] == "UNAVAILABLE"),
            "in_fixture": len(all_games),
            "note": None if all_games else "No games of this sport in the replay fixture.",
        }
    return {"mode": "demo", "data_label": session.data_label, "as_of": now.isoformat(),
            "date": d.isoformat(), "tz": tz, "rows": rows, "sports": sports,
            "clock": "REPLAY CLOCK" if session.mode == "REPLAY" else "SYSTEM CLOCK"}


def live_board(discovery, now: datetime, day: str | None, tz: str) -> dict:
    d, start, end = local_window(day, tz, now)
    by_sport = discovery.by_sport() if discovery else {}
    rows = []
    for ev in (discovery.catalog.events.values() if discovery else []):
        if not _in_day(ev.scheduled_start, ev.status, start, end):
            continue
        names = {k: ev.names.get(k, k) for k in (ev.away, ev.home)}
        rows.append({
            "game_id": ev.event_id,
            "event": {"game_id": ev.event_id, "sport": ev.sport, "competition": ev.competition,
                      "participants": [ev.away, ev.home], "names": names,
                      "home": ev.home, "away": ev.away, "round": ev.round,
                      "scheduled_start": ev.scheduled_start.isoformat(),
                      "status": ev.status, "participant_kind": "PLAYER"
                      if ev.sport == "TENNIS" else "TEAM",
                      "unconfirmed_by": sorted(ev.unconfirmed_by),
                      "reschedules": ev.reschedules},
            "scoreboard": {"kind": ev.sport.lower(), "status": ev.status},
            "likely_winner": {"available": False,
                              "reason": "Live game feed not connected for this sport"},
            "value_side": None, "assessment": "UNAVAILABLE", "selected_market": None,
            "sparkline": [], "freshness": None, "model_status": "NONE",
            "validated_net_edge": None, "probability": None, "has_market": False,
            "forecast_ready": False,
        })
    sports = {}
    for sp in SPORTS:
        info = by_sport.get(sp, {"sources": [], "schedule_known": False})
        mine = [r for r in rows if r["event"]["sport"] == sp]
        known = info["schedule_known"]
        sports[sp] = {
            "schedule_known": known,
            "discovered": len(mine) if known else None,
            "monitored": 0, "forecast_ready": 0,
            "unavailable": len(mine) if known else None,
            "sources": info["sources"],
            "note": None if known else "Schedule unavailable: no configured schedule source "
                                       "has succeeded, so the number of games is unknown.",
        }
    return {"mode": "live", "data_label": "LIVE", "as_of": now.isoformat(),
            "date": d.isoformat(), "tz": tz, "rows": rows, "sports": sports,
            "clock": "SYSTEM CLOCK"}
