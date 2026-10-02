"""The single authoritative runtime the UI talks to.

One ``AppSession`` owns one ``Monitor`` (live or replay), an ordered event log
with sequence numbers for client reconciliation, the paper-order book of
record, and the ledger. The frontend never computes probabilities, fees,
risk or fills: it reads this state and sends commands.

Isolation: every order and ledger event carries ``mode`` (REPLAY / LIVE) and
``data_label`` (e.g. SYNTHETIC) plus a ``run_id``. Replay and demo records are
never mixed with live paper records.

LLM shadow reviews run as detached tasks with deadlines. Their failure is
recorded and never blocks monitoring or orders.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sports_edge.domain.enums import Outcome
from sports_edge.domain.records import PaperFill
from sports_edge.forecast.models import ChainForecaster
from sports_edge.monitor import Monitor, SignalError
from sports_edge.paper.broker import APPROVING
from sports_edge.replay.runner import ReplayStream

# ABANDONED: the worker stopped before the order's fill attempt; it is never filled later.
OrderStatus = Literal["PENDING", "FILLED", "PARTIAL", "REJECTED", "ABANDONED"]


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    idempotency_key: str
    run_id: str
    mode: Literal["REPLAY", "LIVE"]
    data_label: str
    decision_id: str
    game_id: str
    contract_id: str
    selection_team: str
    requested_quantity: int
    status: OrderStatus
    created_time: datetime
    not_before: datetime
    fill: PaperFill | None = None
    reasons: list[str] = []
    outcome: Outcome | None = None
    settled_pnl: Decimal | None = None


class LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ledger_seq: int
    run_id: str
    mode: Literal["REPLAY", "LIVE"]
    data_label: str
    time: datetime
    kind: Literal["ORDER_ACCEPTED", "ORDER_FILLED", "ORDER_PARTIAL", "ORDER_REJECTED",
                  "POSITION_SETTLED", "ORDER_ABANDONED"]
    order_id: str | None
    contract_id: str
    detail: dict[str, Any]


class CommandError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


@dataclass
class EventLog:
    """Monotonic event log with a bounded buffer. Clients resync on gaps.

    Thread-safe: commands run in worker threads while the SSE stream reads on the
    event loop. Waiters are woken with ``call_soon_threadsafe``.
    """

    capacity: int = 2000
    seq: int = 0
    buffer: deque = field(default_factory=deque)
    waiters: set = field(default_factory=set)  # asyncio.Event, created on the event loop
    _lock: Any = field(default_factory=threading.Lock)
    _loops: dict = field(default_factory=dict)  # id(event) -> loop

    def add_waiter(self, ev: asyncio.Event) -> None:
        with self._lock:
            self.waiters.add(ev)
            self._loops[id(ev)] = asyncio.get_running_loop()

    def discard_waiter(self, ev: asyncio.Event) -> None:
        with self._lock:
            self.waiters.discard(ev)
            self._loops.pop(id(ev), None)

    def append(self, kind: str, data: dict) -> int:
        with self._lock:
            self.seq += 1
            seq = self.seq
            self.buffer.append((seq, kind, data))
            while len(self.buffer) > self.capacity:
                self.buffer.popleft()
            wake = [(ev, self._loops.get(id(ev))) for ev in self.waiters]
        for ev, loop in wake:
            if loop is None:
                continue
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:  # loop closed
                pass
        return seq

    def since(self, seq: int) -> tuple[bool, list[tuple[int, str, dict]]]:
        """(needs_resync, events after seq)."""
        with self._lock:
            oldest = self.buffer[0][0] if self.buffer else self.seq + 1
            if seq < oldest - 1 or seq > self.seq:  # gap, or a cursor from a replaced session
                return True, []
            return False, [e for e in self.buffer if e[0] > seq]


@dataclass
class AppSession:
    stream: ReplayStream
    monitor: Monitor
    clock: Any
    mode: Literal["REPLAY", "LIVE"]
    data_label: str
    model_info: dict | None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    events: EventLog = field(default_factory=EventLog)
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    by_decision: dict[str, str] = field(default_factory=dict)
    ledger: list[LedgerEvent] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    speed: float = 60.0
    running: bool = False
    _task: asyncio.Task | None = None
    reviewer: Any = None  # optional ShadowReviewer
    db: Any = None  # optional SqlSink: orders + ledger persisted append-only
    # idempotency keys from earlier runs / before a restart (key -> order), read-only
    prior_keys: dict[str, PaperOrder] = field(default_factory=dict)
    lock: Any = field(default_factory=threading.RLock)  # serializes all state changes

    # ------------------------------------------------------------------ build

    @classmethod
    def replay(cls, path: Path, forecaster: ChainForecaster | None, mechanics_demo: bool,
               model_info: dict | None, reviewer=None) -> AppSession:
        stream = ReplayStream(path)
        clock, mon = stream.build(forecaster=forecaster, mechanics_demo=mechanics_demo)
        mon.auto_paper = False  # orders only from explicit, authenticated commands
        s = cls(stream=stream, monitor=mon, clock=clock, mode="REPLAY",
                data_label=stream.label, model_info=model_info, reviewer=reviewer)
        mon.listeners.append(s._on_monitor_event)
        s.events.append("session", s.summary())
        return s

    # ------------------------------------------------------------------ events

    def _on_monitor_event(self, kind: str, payload: dict) -> None:
        self.events.append(kind, payload)
        if kind == "order_result":
            self._order_result(payload)
        elif kind == "settlement":
            self._settled(payload)
        elif kind == "signal" and self.reviewer is not None:
            self._schedule_review(payload["decision_id"])

    def _ledger(self, kind, contract_id: str, order_id: str | None, detail: dict) -> None:
        ev = LedgerEvent(ledger_seq=len(self.ledger) + 1, run_id=self.run_id, mode=self.mode,
                         data_label=self.data_label, time=self.clock.now(), kind=kind,
                         order_id=order_id, contract_id=contract_id, detail=detail)
        self.ledger.append(ev)
        self.events.append("ledger", ev.model_dump(mode="json"))
        if self.db is not None:
            self.db.ledger_event(ev)
            if order_id is not None:
                self.db.paper_order(self.orders[order_id])

    def _order_result(self, p: dict) -> None:
        oid = self.by_decision.get(p["decision_id"])
        if oid is None:
            return
        o = self.orders[oid]
        fill = PaperFill.model_validate(p["fill"]) if p["fill"] else None
        if fill is None:
            status: OrderStatus = "REJECTED"
            kind = "ORDER_REJECTED"
        elif fill.filled_quantity < o.requested_quantity:
            status, kind = "PARTIAL", "ORDER_PARTIAL"
        else:
            status, kind = "FILLED", "ORDER_FILLED"
        self.orders[oid] = o.model_copy(update={"status": status, "fill": fill,
                                                "reasons": p["reasons"]})
        self._ledger(kind, o.contract_id, oid, {
            "reasons": p["reasons"],
            "filled_quantity": fill.filled_quantity if fill else 0,
            "cost": str(fill.cost) if fill else "0", "fees": str(fill.fees) if fill else "0"})

    def _settled(self, p: dict) -> None:
        outcome = Outcome(p["outcome"])
        for oid, o in list(self.orders.items()):
            if o.contract_id != p["contract_id"] or o.fill is None or o.outcome is not None:
                continue
            outlay = o.fill.cost + o.fill.fees
            pnl = {Outcome.WIN: Decimal(o.fill.filled_quantity) - outlay,
                   Outcome.LOSS: -outlay, Outcome.VOID: Decimal(0)}.get(outcome)
            self.orders[oid] = o.model_copy(update={"outcome": outcome, "settled_pnl": pnl})
            self._ledger("POSITION_SETTLED", o.contract_id, oid,
                         {"outcome": outcome.value, "pnl": None if pnl is None else str(pnl)})

    def _schedule_review(self, decision_id: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (synchronous tests): reviews are optional
        loop.create_task(self._review(decision_id))

    async def _review(self, decision_id: str) -> None:
        from sports_edge.llm.review import EvidenceBundle, EvidenceItem

        d = self.monitor.signals.get(decision_id)
        st = self.monitor.decision_state.get(decision_id)
        if d is None or st is None:
            return
        bundle = EvidenceBundle(
            bundle_id=f"bundle_{decision_id}", decision_id=decision_id,
            snapshot_id=st.snapshot_id, created_time=self.clock.now(),
            items=(EvidenceItem(evidence_id="state", kind="game_state", as_of=st.as_of_event_time,
                                content=st.model_dump(mode="json")),
                   EvidenceItem(evidence_id="decision", kind="decision",
                                as_of=d.decision_time, content=d.model_dump(mode="json"))))
        rt = self.monitor.games[d.game_id]
        try:
            recs = await self.reviewer.review(
                bundle, lambda: rt.reducer.state.snapshot_id if rt.reducer.state else "",
                self.clock.now)
            for r in recs:
                self.reviews.append(r.model_dump(mode="json"))
                self.events.append("llm_review", {"decision_id": decision_id,
                                                  "status": r.status, "provider": r.provider})
        except Exception as e:  # optional path: record and move on
            self.reviews.append({"decision_id": decision_id, "status": "ERROR",
                                 "error": type(e).__name__})

    # ------------------------------------------------------------------ replay control

    def step(self, n: int = 1) -> int:
        with self.lock:
            return self._step(n)

    def _step(self, n: int) -> int:
        k = 0
        while k < n and not self.stream.done:
            self.stream.step(self.clock, self.monitor)
            k += 1
        self.events.append("clock", {"as_of": self.clock.now().isoformat(),
                                     "position": self.stream.position,
                                     "total": len(self.stream.body)})
        return k

    def step_until(self, t: datetime) -> int:
        k = 0
        while not self.stream.done and self.stream.time_at(self.stream.position) <= t:
            self.stream.step(self.clock, self.monitor)
            k += 1
        return k

    async def run(self) -> None:
        """Advance replay time at ``speed`` x real time until paused or finished."""
        self.running = True
        try:
            while self.running and not self.stream.done:
                cur = self.clock.now()
                nxt = self.stream.time_at(self.stream.position)
                wait = max(0.0, (nxt - cur).total_seconds() / self.speed)
                await asyncio.sleep(min(wait, 1.0))
                if wait <= 1.0:
                    self.step(1)
                else:  # advance the replay clock in sub-steps so the UI sees time pass
                    from datetime import timedelta
                    with self.lock:
                        self.clock.advance_to(cur + timedelta(seconds=self.speed))
                        self.monitor.tick()
                    self.events.append("clock", {"as_of": self.clock.now().isoformat(),
                                                 "position": self.stream.position,
                                                 "total": len(self.stream.body)})
        finally:
            self.running = False
            self.events.append("session", self.summary())

    def start(self, speed: float) -> None:
        self.speed = max(1.0, min(speed, 600.0))
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    def pause(self) -> None:
        self.running = False

    # ------------------------------------------------------------------ commands

    def place_order(self, decision_id: str, idempotency_key: str, quantity: int | None,
                    expected_contract_id: str | None = None) -> tuple[PaperOrder, bool]:
        """Returns (order, created). Same key -> same order (duplicate click safe).

        Serialized with every other state change, so concurrent requests cannot race.
        ``expected_contract_id`` is what the UI displayed: a mismatch is refused.
        """
        with self.lock:
            return self._place_order(decision_id, idempotency_key, quantity,
                                     expected_contract_id)

    def _place_order(self, decision_id: str, idempotency_key: str, quantity: int | None,
                     expected_contract_id: str | None) -> tuple[PaperOrder, bool]:
        if not idempotency_key or len(idempotency_key) > 100:
            raise CommandError(422, "BAD_IDEMPOTENCY_KEY", "1-100 characters required")
        if idempotency_key in self.by_key:
            o = self.orders[self.by_key[idempotency_key]]
            if o.decision_id != decision_id:
                raise CommandError(409, "IDEMPOTENCY_KEY_REUSED", "key used for another signal")
            return o, False
        if idempotency_key in self.prior_keys:  # a retry that crosses a restart
            o = self.prior_keys[idempotency_key]
            if o.decision_id != decision_id:
                raise CommandError(409, "IDEMPOTENCY_KEY_REUSED", "key used for another signal")
            return o, False
        if decision_id in self.by_decision:
            raise CommandError(409, "ALREADY_ORDERED",
                               f"order {self.by_decision[decision_id]} exists for this signal")
        sig = self.monitor.signals.get(decision_id)
        if sig is not None and expected_contract_id is not None \
                and sig.contract_id != expected_contract_id:
            raise CommandError(409, "CONTRACT_MISMATCH",
                               f"signal is for {sig.contract_id}, not {expected_contract_id}")
        if sig is not None:
            busy = [o for o in self.orders.values()
                    if o.contract_id == sig.contract_id and o.status == "PENDING"]
            if busy:
                raise CommandError(409, "ORDER_PENDING_FOR_CONTRACT",
                                   f"order {busy[0].order_id} is still pending for this contract")
        try:
            pending = self.monitor.submit_signal(decision_id, quantity)
        except SignalError as e:
            status = 410 if e.code == "SIGNAL_EXPIRED" else 409 if e.code in (
                "SIGNAL_SUPERSEDED", "EXPOSURE_LIMIT") else 404 if e.code == "UNKNOWN_SIGNAL" else 422
            raise CommandError(status, e.code, e.detail) from e
        d = pending.decision
        oid = f"po_{uuid.uuid4().hex[:12]}"
        o = PaperOrder(order_id=oid, idempotency_key=idempotency_key, run_id=self.run_id,
                       mode=self.mode, data_label=self.data_label, decision_id=decision_id,
                       game_id=d.game_id, contract_id=d.contract_id or "",
                       selection_team=d.selection_team or "",
                       requested_quantity=d.planned_quantity, status="PENDING",
                       created_time=self.clock.now(), not_before=pending.not_before)
        self.orders[oid] = o
        self.by_key[idempotency_key] = oid
        self.by_decision[decision_id] = oid
        self._ledger("ORDER_ACCEPTED", o.contract_id, oid,
                     {"requested_quantity": o.requested_quantity,
                      "not_before": pending.not_before.isoformat(),
                      "note": "Fill is simulated only if every gate still passes after the delay."})
        return o, True

    # ------------------------------------------------------------------ views

    def summary(self) -> dict:
        return {"run_id": self.run_id, "mode": self.mode, "data_label": self.data_label,
                "as_of": self.clock.now().isoformat(), "position": self.stream.position,
                "total": len(self.stream.body), "running": self.running, "speed": self.speed,
                "finished": self.stream.done, "model": self.model_info,
                "auto_paper": self.monitor.auto_paper}

    def signal_view(self, decision_id: str) -> dict:
        d = self.monitor.signals[decision_id]
        now = self.clock.now()
        oid = self.by_decision.get(decision_id)
        return {"decision": d.model_dump(mode="json"),
                "expired": now > d.expires_at,
                "seconds_to_expiry": (d.expires_at - now).total_seconds(),
                "order_id": oid}

    def evaluation(self) -> dict:
        from sports_edge.evaluation.metrics import conditional_win_rate

        filled = [o for o in self.orders.values() if o.fill is not None]
        settled = [o for o in filled if o.outcome in (Outcome.WIN, Outcome.LOSS)]
        spend = sum((o.fill.cost + o.fill.fees for o in filled if o.fill), Decimal(0))
        pnl = sum((o.settled_pnl or Decimal(0) for o in settled), Decimal(0))
        approvals = [d for d in self.monitor.signals.values() if d.action in APPROVING]
        return {
            "run_id": self.run_id, "mode": self.mode, "data_label": self.data_label,
            "warning": "Single replay run. Not evidence of an edge."
            if self.mode == "REPLAY" else None,
            "signals": len(approvals),
            "orders": len(self.orders),
            "rejected_orders": sum(1 for o in self.orders.values() if o.status == "REJECTED"),
            "filled_orders": len(filled),
            "settled_orders": len(settled),
            "total_spend_all_in": str(spend),
            "settled_pnl": str(pnl),
            "roi_on_spend_pct": str((pnl / spend * 100).quantize(Decimal("0.01")))
            if spend and settled else None,
            "filled_win_rate": conditional_win_rate(
                [1 if o.outcome == Outcome.WIN else 0 for o in settled]),
        }


# ---------------------------------------------------------------------- restart recovery


def recover(db, now: datetime) -> dict:
    """Reload persisted orders after a worker restart.

    * Orders still PENDING were never filled: they become ABANDONED (new version +
      ledger event). They are never filled retroactively.
    * Returns every order's latest version, keyed by idempotency key, so retried
      requests are recognised instead of creating duplicates.
    """
    latest = db.latest_orders()
    abandoned = []
    for o in latest.values():
        if o.status != "PENDING":
            continue
        a = o.model_copy(update={"status": "ABANDONED",
                                 "reasons": ["WORKER_RESTARTED_BEFORE_FILL_ATTEMPT"]})
        db.paper_order(a)
        seq = db.next_ledger_seq(o.run_id)
        db.ledger_event(LedgerEvent(
            ledger_seq=seq, run_id=o.run_id, mode=o.mode, data_label=o.data_label, time=now,
            kind="ORDER_ABANDONED", order_id=o.order_id, contract_id=o.contract_id,
            detail={"reason": "worker restarted before the fill attempt"}))
        latest[o.order_id] = a
        abandoned.append(o.order_id)
    return {"orders": latest, "abandoned": abandoned,
            "by_key": {o.idempotency_key: o for o in latest.values()}}


def restore_exposure(ledger, orders, mode: str = "LIVE") -> int:
    """Rebuild open exposure from unsettled filled orders of one mode (LIVE in practice).

    Replay runs start from a fresh bankroll and are never mixed with LIVE records.
    """
    n = 0
    for o in orders:
        if o.mode != mode or o.fill is None or o.outcome is not None:
            continue
        ledger.record_purchase(o.game_id, o.selection_team, o.fill.fill_time.date(),
                               o.fill.cost, o.fill.fees)
        n += 1
    return n
