"""Shared machinery for every sport: the event-ordering reducer and the adapter registry.

Each sport supplies its own state record, rules, features, forecaster and display
summary. What is shared is *policy*, not probability:

* duplicates (same provider_event_id, same content) are ignored;
* a correction (same id, different content) flags ``CORRECTION_UNRECONCILED``;
* a sequence gap flags ``FEED_GAP``; a late event flags ``OUT_OF_ORDER``;
* every flag holds until a full ``SNAPSHOT`` from the provider;
* provider timestamps later than our receipt are rejected (``TIMESTAMP_INCONSISTENT``).

Flags block paper entries in the trigger engine; monitoring continues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol

from sports_edge.domain.enums import SettlementRule, SourceStatus, Sport
from sports_edge.domain.records import Game, GameStateBase, Prediction, stable_id
from sports_edge.ingest.nhl_state import (
    CLOCK_TOLERANCE_S,
    ApplyResult,
    InvalidEvent,
    NormalizedGameEvent,
)

COMMON_FIELDS = ("snapshot_id", "game_id", "source", "source_status", "schema_version")


def validate_times(ev: NormalizedGameEvent) -> None:
    if ev.received_time.tzinfo is None:
        raise InvalidEvent("received_time must be timezone-aware")
    for name, t in (("event_time", ev.event_time), ("published_time", ev.published_time)):
        if t is not None and (t - ev.received_time).total_seconds() > CLOCK_TOLERANCE_S:
            raise InvalidEvent(f"TIMESTAMP_INCONSISTENT: {name} is after our receipt time")
    if ev.event_time and ev.published_time and \
            (ev.event_time - ev.published_time).total_seconds() > CLOCK_TOLERANCE_S:
        raise InvalidEvent("TIMESTAMP_INCONSISTENT: published before the event happened")


@dataclass
class BaseReducer:
    """Subclasses set ``state_cls``, ``prefix``, ``material_types`` and implement
    ``initial_fields`` and ``reduce`` (mutating a field dict; raise InvalidEvent)."""

    game_id: str
    home: str
    away: str
    state: Any = None
    seen: dict[str, str] = field(default_factory=dict)
    last_seq: int | None = None
    corrections: int = 0
    gaps: int = 0
    out_of_order: int = 0
    duplicates: int = 0
    last_labels: list[str] = field(default_factory=list)  # annotations from the last event

    state_cls: ClassVar[type[GameStateBase]]
    prefix: ClassVar[str]
    material_types: ClassVar[frozenset[str]]

    # ---------------------------------------------------------------- hooks

    def initial_fields(self) -> dict[str, Any]:
        raise NotImplementedError

    def reduce(self, u: dict[str, Any], ev: NormalizedGameEvent) -> None:
        raise NotImplementedError

    def material(self, ev: NormalizedGameEvent, before: dict, after: dict) -> bool:
        return ev.type in self.material_types

    # ---------------------------------------------------------------- core

    def _make(self, src: str, status: SourceStatus, u: dict[str, Any]):
        body = dict(game_id=self.game_id, source=src, source_status=status, **u)
        return self.state_cls(snapshot_id=stable_id(self.prefix, body), **body)

    def _fields(self, s) -> dict[str, Any]:
        return s.model_dump(exclude=set(COMMON_FIELDS))

    def _flag(self, ev: NormalizedGameEvent, flag: str):
        u = self._fields(self.state)
        u["pending_reconciliation"] = tuple(sorted(set(u["pending_reconciliation"]) | {flag}))
        u["as_of_received_time"] = ev.received_time
        self.state = self._make(ev.source, ev.source_status, u)
        return self.state

    def apply(self, ev: NormalizedGameEvent) -> ApplyResult:
        if ev.game_id != self.game_id:
            raise InvalidEvent("event for a different game")
        validate_times(ev)
        self.last_labels = []
        if self.state is None:
            u0 = dict(self.initial_fields(), as_of_event_time=None,
                      as_of_received_time=ev.received_time, last_material_event_time=None,
                      last_material_event_kind=None, applied_seq=None)
            self.state = self._make(ev.source, ev.source_status, u0)
        saved_seen = (ev.provider_event_id, self.seen.get(ev.provider_event_id or ""))
        saved_seq = self.last_seq
        if ev.provider_event_id is not None and ev.type != "SNAPSHOT":
            h = ev.content_hash()
            prior = self.seen.get(ev.provider_event_id)
            if prior == h:
                self.duplicates += 1
                return ApplyResult(self.state, False, False, "duplicate")
            self.seen[ev.provider_event_id] = h
            if prior is not None:
                self.corrections += 1
                return ApplyResult(self._flag(ev, "CORRECTION_UNRECONCILED"), False, True,
                                   "correction: awaiting snapshot")
        flags = set(self.state.pending_reconciliation)
        if ev.type != "SNAPSHOT" and ev.seq is not None and self.last_seq is not None:
            if ev.seq <= self.last_seq:
                self.out_of_order += 1
                return ApplyResult(self._flag(ev, "OUT_OF_ORDER"), False, True,
                                   "late event: awaiting snapshot")
            if ev.seq > self.last_seq + 1:
                self.gaps += 1
                flags.add("FEED_GAP")
        if ev.seq is not None:
            self.last_seq = ev.seq if self.last_seq is None else max(self.last_seq, ev.seq)
        before = self._fields(self.state)
        u = dict(before)
        if ev.type == "SNAPSHOT":
            flags = set()  # an authoritative full state clears reconciliation flags
        u["pending_reconciliation"] = tuple(sorted(flags))
        try:
            self.reduce(u, ev)  # may add/remove flags itself
        except InvalidEvent:
            # rejected: leave dedupe and sequence bookkeeping as they were
            pid, prior = saved_seen
            if pid is not None:
                if prior is None:
                    self.seen.pop(pid, None)
                else:
                    self.seen[pid] = prior
            self.last_seq = saved_seq
            raise
        material = self.material(ev, before, u)
        u["as_of_event_time"] = ev.event_time or ev.published_time
        u["as_of_received_time"] = ev.received_time
        u["applied_seq"] = ev.seq
        if material:
            u["last_material_event_time"] = ev.event_time or ev.published_time or \
                ev.received_time
            u["last_material_event_kind"] = ev.type
        self.state = self._make(ev.source, ev.source_status, u)
        if not self.state.coherent():
            # keep it, but say so: the engine blocks on an incoherent state
            self.state = self._flag(ev, "STATE_INCOHERENT")
        return ApplyResult(self.state, True, material, "applied")


def add_flag(u: dict[str, Any], flag: str) -> None:
    u["pending_reconciliation"] = tuple(sorted(set(u["pending_reconciliation"]) | {flag}))


def drop_flag(u: dict[str, Any], flag: str) -> None:
    u["pending_reconciliation"] = tuple(sorted(set(u["pending_reconciliation"]) - {flag}))


# ------------------------------------------------------------------ adapters


class SportForecaster(Protocol):
    """Sport-specific probability model behind one interface."""

    model_version: str

    def predict(self, state: GameStateBase, game: Game, selection: str,
                prior: float | None, rule: SettlementRule, now: datetime
                ) -> Prediction | str: ...


@dataclass(frozen=True)
class FinalOutcome:
    """Sport-neutral final result handed to settlement."""

    status: str  # FINAL / POSTPONED / CANCELLED / SUSPENDED
    winner: str | None  # participant id; None for a tie or unknown
    tie: bool = False
    detail: str = ""
    extra: tuple[tuple[str, Any], ...] = ()


class SportAdapter(Protocol):
    sport: Sport
    default_rule: SettlementRule

    def new_reducer(self, game: Game) -> BaseReducer: ...

    def final_outcome(self, state: GameStateBase, game: Game, data: dict) -> FinalOutcome: ...

    def scoreboard(self, state: GameStateBase | None, game: Game) -> dict: ...

    def annotations(self, ev: NormalizedGameEvent, game: Game) -> list[dict]: ...

    def features(self, state: GameStateBase, game: Game, selection: str,
                 prior: float | None) -> dict[str, float | None]: ...


_REGISTRY: dict[Sport, SportAdapter] = {}


def register(adapter: SportAdapter) -> SportAdapter:
    _REGISTRY[adapter.sport] = adapter
    return adapter


def adapter_for(sport: Sport) -> SportAdapter:
    if not _REGISTRY:
        from sports_edge.sports import mlb, nfl, nhl, tennis  # noqa: F401  (registers)
    try:
        return _REGISTRY[sport]
    except KeyError:
        raise KeyError(f"no adapter for {sport}") from None


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
