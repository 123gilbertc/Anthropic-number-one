"""Injectable clocks.

Live code and replay code share every decision path. The only thing that
differs is *where "now" comes from*: the wall clock in live mode, or the
timestamp of the event being replayed. Nothing in the decision path may call
``datetime.now()`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ReplayClock:
    """A clock that only moves when the replay driver advances it.

    It refuses to move backwards: a replay that tries to do so has a sorting
    bug, and silently allowing it would leak future information.
    """

    def __init__(self, start: datetime) -> None:
        _require_aware(start)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance_to(self, t: datetime) -> None:
        _require_aware(t)
        if t < self._now:
            raise ValueError(f"ReplayClock cannot move backwards: {t} < {self._now}")
        self._now = t


def _require_aware(t: datetime) -> None:
    if t.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
