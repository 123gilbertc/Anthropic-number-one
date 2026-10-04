"""NHL normalized game-state reducer.

Input: provider-neutral normalized events (see ``fixtures/README.md``).
Output: a new immutable ``NHLState`` for every applied event.

Ordering / correction policy:

* Duplicate (same provider_event_id, same content): ignored.
* Same provider_event_id with *different* content: a correction. It is
  stored, and the state is flagged ``PENDING_RECONCILIATION`` until a full
  ``SNAPSHOT`` event confirms the true state. We do not try to "undo" history.
* Sequence gap (seq jumps forward): flagged ``FEED_GAP`` until a SNAPSHOT.
* Late event (seq below last applied): stored, not applied, flagged
  ``OUT_OF_ORDER`` until a SNAPSHOT.

Flags block new paper entries/additions (the trigger engine checks them),
but monitoring continues.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sports_edge.domain.enums import SourceStatus
from sports_edge.domain.records import NHLState, stable_id

MATERIAL_TYPES = {
    "GOAL",
    "PENALTY",
    "PENALTY_END",
    "GOALIE_CHANGE",
    "EMPTY_NET_START",
    "EMPTY_NET_END",
    "REVIEW_START",
    "REVIEW_END",
    "CORRECTION",
    "SNAPSHOT",
    "GAME_END",
}

REG_PERIOD_SECONDS = 20 * 60
OT_PERIOD_SECONDS = 5 * 60  # regular-season 3v3 OT; playoffs differ (see docs)


class InvalidEvent(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedGameEvent:
    game_id: str
    source: str
    source_status: SourceStatus
    type: str
    provider_event_id: str | None
    seq: int | None
    event_time: datetime | None
    published_time: datetime | None
    received_time: datetime
    data: dict[str, Any]

    def content_hash(self) -> str:
        blob = json.dumps({"t": self.type, "d": self.data}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class ApplyResult:
    state: NHLState
    applied: bool
    material: bool
    note: str


@dataclass
class NHLStateReducer:
    game_id: str
    home: str
    away: str
    state: NHLState | None = None
    seen: dict[str, str] = field(default_factory=dict)  # provider_event_id -> content hash
    last_seq: int | None = None
    corrections: int = 0
    gaps: int = 0
    out_of_order: int = 0
    duplicates: int = 0

    def initial(self, source: str, status: SourceStatus, received: datetime) -> NHLState:
        return self._make(
            source=source,
            status=status,
            as_of_event_time=None,
            received=received,
            last_material_time=None,
            last_material_kind=None,
            applied_seq=None,
            period=1,
            secs=REG_PERIOD_SECONDS,
            hs=0,
            as_=0,
            hsk=5,
            ask=5,
            hg=None,
            ag=None,
            hen=False,
            aen=False,
            pending=(),
        )

    def apply(self, ev: NormalizedGameEvent) -> ApplyResult:
        if ev.game_id != self.game_id:
            raise InvalidEvent("event for a different game")
        _validate(ev)
        if self.state is None:
            self.state = self.initial(ev.source, ev.source_status, ev.received_time)
        s = self.state

        # duplicates and corrections ----------------------------------------
        if ev.provider_event_id is not None and ev.type != "SNAPSHOT":
            prior = self.seen.get(ev.provider_event_id)
            h = ev.content_hash()
            if prior == h:
                self.duplicates += 1
                return ApplyResult(s, False, False, "duplicate")
            if prior is not None:
                self.corrections += 1
                self.seen[ev.provider_event_id] = h
                self.state = self._flag(s, ev, "CORRECTION_UNRECONCILED")
                return ApplyResult(self.state, False, True, "correction: awaiting snapshot")
            self.seen[ev.provider_event_id] = h

        # ordering ---------------------------------------------------------
        if ev.type != "SNAPSHOT" and ev.seq is not None and self.last_seq is not None:
            if ev.seq <= self.last_seq:
                self.out_of_order += 1
                self.state = self._flag(s, ev, "OUT_OF_ORDER")
                return ApplyResult(self.state, False, True, "late event: awaiting snapshot")
            if ev.seq > self.last_seq + 1:
                self.gaps += 1
                s = self._flag(s, ev, "FEED_GAP")
        if ev.seq is not None:
            self.last_seq = ev.seq if self.last_seq is None else max(self.last_seq, ev.seq)

        self.state = self._reduce(s, ev)
        return ApplyResult(self.state, True, ev.type in MATERIAL_TYPES, "applied")

    # ------------------------------------------------------------------ internals

    def _reduce(self, s: NHLState, ev: NormalizedGameEvent) -> NHLState:
        d = ev.data
        u: dict[str, Any] = {
            "period": d.get("period", s.period),
            "secs": d.get("seconds_remaining", s.seconds_remaining_in_period),
            "hs": s.home_score,
            "as_": s.away_score,
            "hsk": s.home_skaters,
            "ask": s.away_skaters,
            "hg": s.home_goalie,
            "ag": s.away_goalie,
            "hen": s.home_net_empty,
            "aen": s.away_net_empty,
            "pending": s.pending_reconciliation,
            "in_review": s.in_review,
            "final": s.is_final,
            "decided": s.final_decided_in,
        }
        side = d.get("team")
        is_home = side == self.home
        t = ev.type
        if t == "GOAL":
            u["hs" if is_home else "as_"] += 1
            if u["period"] == 4:
                # overtime is sudden death (regular season and playoffs)
                u["final"], u["decided"] = True, "OT"
        elif t == "PENALTY":
            key = "hsk" if is_home else "ask"
            u[key] = max(3, (u[key] or 5) - 1)
        elif t == "PENALTY_END":
            key = "hsk" if is_home else "ask"
            u[key] = min(5, (u[key] or 4) + 1)
        elif t == "GOALIE_CHANGE":
            # None = goalie unknown (not "no goalie"): block adds until identified
            goalie = d.get("goalie")
            u["hg" if is_home else "ag"] = goalie
            flag = f"GOALIE_UNKNOWN_{side}"
            pend = set(u["pending"]) - {flag}
            if goalie is None:
                pend.add(flag)
            u["pending"] = tuple(sorted(pend))
        elif t == "EMPTY_NET_START":
            u["hen" if is_home else "aen"] = True
        elif t == "EMPTY_NET_END":
            u["hen" if is_home else "aen"] = False
        elif t == "REVIEW_START":
            u["in_review"] = True
        elif t == "REVIEW_END":
            u["in_review"] = False
        elif t == "SNAPSHOT":
            # Full reconciliation from an authoritative snapshot clears all flags.
            u.update(
                hs=d["home_score"],
                as_=d["away_score"],
                hsk=d.get("home_skaters"),
                ask=d.get("away_skaters"),
                hg=d.get("home_goalie"),
                ag=d.get("away_goalie"),
                hen=d.get("home_net_empty"),
                aen=d.get("away_net_empty"),
                pending=(),
                in_review=d.get("in_review", False),
            )
        elif t == "GAME_END":
            u["final"] = True
            u["decided"] = d.get("decided_in")
        material = t in MATERIAL_TYPES
        return self._make(
            source=ev.source,
            status=ev.source_status,
            as_of_event_time=ev.event_time,
            received=ev.received_time,
            last_material_time=(ev.event_time or ev.received_time)
            if material
            else s.last_material_event_time,
            last_material_kind=t if material else s.last_material_event_kind,
            applied_seq=ev.seq if ev.seq is not None else s.applied_seq,
            period=u["period"],
            secs=u["secs"],
            hs=u["hs"],
            as_=u["as_"],
            hsk=u["hsk"],
            ask=u["ask"],
            hg=u["hg"],
            ag=u["ag"],
            hen=u["hen"],
            aen=u["aen"],
            pending=u["pending"],
            in_review=u["in_review"],
            final=u["final"],
            decided=u["decided"],
        )

    def _flag(self, s: NHLState, ev: NormalizedGameEvent, flag: str) -> NHLState:
        pending = tuple(sorted(set(s.pending_reconciliation) | {flag}))
        data = s.model_dump()
        data.update(pending_reconciliation=pending, as_of_received_time=ev.received_time)
        data["snapshot_id"] = stable_id("nhl", {k: v for k, v in data.items() if k != "snapshot_id"})
        return NHLState(**data)

    def _make(
        self,
        *,
        source: str,
        status: SourceStatus,
        as_of_event_time: datetime | None,
        received: datetime,
        last_material_time: datetime | None,
        last_material_kind: str | None,
        applied_seq: int | None,
        period: int,
        secs: int | None,
        hs: int,
        as_: int,
        hsk: int | None,
        ask: int | None,
        hg: str | None,
        ag: str | None,
        hen: bool | None,
        aen: bool | None,
        pending: tuple[str, ...],
        in_review: bool = False,
        final: bool = False,
        decided: str | None = None,
    ) -> NHLState:
        body = dict(
            game_id=self.game_id,
            source=source,
            source_status=status,
            as_of_event_time=as_of_event_time,
            as_of_received_time=received,
            last_material_event_time=last_material_time,
            last_material_event_kind=last_material_kind,
            applied_seq=applied_seq,
            period=period,
            seconds_remaining_in_period=secs,
            home_score=hs,
            away_score=as_,
            home_skaters=hsk,
            away_skaters=ask,
            home_goalie=hg,
            away_goalie=ag,
            home_net_empty=hen,
            away_net_empty=aen,
            is_final=final,
            final_decided_in=decided,
            in_review=in_review,
            pending_reconciliation=pending,
        )
        return NHLState(snapshot_id=stable_id("nhl", body), **body)


CLOCK_TOLERANCE_S = 5.0  # allowed provider/local clock skew before timestamps are rejected


def _validate(ev: NormalizedGameEvent) -> None:
    d = ev.data
    if ev.received_time.tzinfo is None:
        raise InvalidEvent("received_time must be timezone-aware")
    for name, t in (("event_time", ev.event_time), ("published_time", ev.published_time)):
        if t is not None and (t - ev.received_time).total_seconds() > CLOCK_TOLERANCE_S:
            raise InvalidEvent(f"TIMESTAMP_INCONSISTENT: {name} is after our receipt time")
    if ev.event_time and ev.published_time and \
            (ev.event_time - ev.published_time).total_seconds() > CLOCK_TOLERANCE_S:
        raise InvalidEvent("TIMESTAMP_INCONSISTENT: published before the event happened")
    p = d.get("period")
    if p is not None and not (1 <= p <= 5 or ev.data.get("playoff")):
        raise InvalidEvent(f"invalid period {p}")
    secs = d.get("seconds_remaining")
    if secs is not None and not (0 <= secs <= REG_PERIOD_SECONDS):
        raise InvalidEvent(f"invalid clock {secs}")
    if ev.type in ("GOAL", "PENALTY", "PENALTY_END", "GOALIE_CHANGE") and "team" not in d:
        raise InvalidEvent(f"{ev.type} without team")
    if ev.type == "SNAPSHOT" and not {"home_score", "away_score"} <= set(d):
        raise InvalidEvent("snapshot without score")


def seconds_remaining_regulation(s: NHLState) -> int | None:
    """Seconds left in regulation (0 once in OT/SO). None if the clock is unknown."""
    if s.seconds_remaining_in_period is None:
        return None
    if s.period >= 4:
        return 0
    return (3 - s.period) * REG_PERIOD_SECONDS + s.seconds_remaining_in_period


def coherent(s: NHLState) -> bool:
    if s.home_score < 0 or s.away_score < 0:
        return False
    if s.period >= 4 and not s.is_final and s.home_score != s.away_score:
        return False  # OT continues only while tied
    for sk in (s.home_skaters, s.away_skaters):
        if sk is not None and not 3 <= sk <= 6:
            return False
    return True
