"""Per-source health and latency measurement.

Latency is measured, not claimed: for each message with a provider event or
publication time we record ``received - event_time`` and report percentiles.
Missing provider timestamps are counted, not filled with receipt time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from sports_edge.domain.enums import SourceStatus


@dataclass
class SourceHealth:
    name: str
    kind: str  # game_feed | market | reference | news
    status: SourceStatus
    stale_after: timedelta
    last_received: datetime | None = None
    messages: int = 0
    missing_provider_time: int = 0
    gaps: int = 0
    duplicates: int = 0
    reconnects: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=5000))
    note: str = ""

    def observe(self, received: datetime, provider_time: datetime | None) -> None:
        self.messages += 1
        self.last_received = received
        if provider_time is None:
            self.missing_provider_time += 1
        else:
            self.latencies_ms.append((received - provider_time).total_seconds() * 1000)

    def effective_status(self, now: datetime) -> SourceStatus:
        if self.status in (SourceStatus.LIVE,) and (
            self.last_received is None or now - self.last_received > self.stale_after
        ):
            return SourceStatus.STALE
        return self.status

    def summary(self, now: datetime) -> dict:
        lat = np.array(self.latencies_ms) if self.latencies_ms else None
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.effective_status(now).value,
            "last_received": self.last_received.isoformat() if self.last_received else None,
            "age_seconds": (now - self.last_received).total_seconds()
            if self.last_received else None,
            "messages": self.messages,
            "missing_provider_time": self.missing_provider_time,
            "gaps": self.gaps,
            "duplicates": self.duplicates,
            "reconnects": self.reconnects,
            "latency_ms_p50": float(np.percentile(lat, 50)) if lat is not None else None,
            "latency_ms_p95": float(np.percentile(lat, 95)) if lat is not None else None,
            "note": self.note,
        }
