"""The event-driven core shared by live operation and replay.

For every incoming event: validate -> store -> update normalized state ->
re-evaluate the affected forecasts/triggers -> process due paper orders.

* Game events recompute predictions when the event is material or when
  ``recompute_every`` of wall/replay time has passed since the last forecast.
* Price-only updates reuse a still-valid prediction (same snapshot, not
  expired) and only recompute executable edge.
* LLMs are not on this path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sports_edge.adapters.kalshi import KalshiBookManager
from sports_edge.clock import Clock
from sports_edge.contracts.settlement import FinalResult, resolve
from sports_edge.domain.enums import Action, Outcome
from sports_edge.domain.records import (
    Decision,
    Game,
    MarketMapping,
    NHLState,
    PaperFill,
    Prediction,
    RawEvent,
    Settlement,
    SportsbookQuote,
    stable_id,
)
from sports_edge.features.nhl import build_features
from sports_edge.forecast.models import ChainForecaster
from sports_edge.health import SourceHealth
from sports_edge.ingest.nhl_state import InvalidEvent, NHLStateReducer, NormalizedGameEvent
from sports_edge.paper.broker import APPROVING, PaperBroker
from sports_edge.triggers.engine import TriggerContext, TriggerEngine


class SignalError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class Sink(Protocol):
    def raw(self, r: RawEvent) -> None: ...
    def state(self, s: NHLState) -> None: ...
    def quote(self, q: SportsbookQuote) -> None: ...
    def prediction(self, p: Prediction) -> None: ...
    def decision(self, d: Decision) -> None: ...
    def fill(self, f: PaperFill) -> None: ...
    def settlement(self, s: Settlement) -> None: ...


@dataclass
class MemorySink:
    raws: list[RawEvent] = field(default_factory=list)
    states: list[NHLState] = field(default_factory=list)
    quotes: list[SportsbookQuote] = field(default_factory=list)
    predictions: list[Prediction] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)

    def raw(self, r): self.raws.append(r)
    def state(self, s): self.states.append(s)
    def quote(self, q): self.quotes.append(q)
    def prediction(self, p): self.predictions.append(p)
    def decision(self, d): self.decisions.append(d)
    def fill(self, f): self.fills.append(f)
    def settlement(self, s): self.settlements.append(s)


@dataclass
class GameRuntime:
    game: Game
    reducer: NHLStateReducer
    mappings: list[MarketMapping]
    pregame_prob: dict[str, float | None]  # team -> pregame prior, None = unknown
    anchor_price: dict[str, Decimal] = field(default_factory=dict)  # contract -> anchor
    predictions: dict[str, Prediction] = field(default_factory=dict)
    abstentions: dict[str, str] = field(default_factory=dict)
    references: dict[str, SportsbookQuote] = field(default_factory=dict)  # book -> latest
    last_forecast_at: datetime | None = None
    last_decision: dict[str, Decision] = field(default_factory=dict)
    settled: bool = False


@dataclass
class Monitor:
    clock: Clock
    engine: TriggerEngine
    broker: PaperBroker
    forecaster: ChainForecaster | None
    books: KalshiBookManager
    sink: Sink = field(default_factory=MemorySink)
    recompute_every: timedelta = timedelta(seconds=30)
    use_references: bool = True
    games: dict[str, GameRuntime] = field(default_factory=dict)
    health: dict[str, SourceHealth] = field(default_factory=dict)
    rejected_events: int = 0
    alerts: list[Decision] = field(default_factory=list)
    # auto_paper=True: research mode, approvals go straight to the paper broker.
    # auto_paper=False: approvals become signals; only an explicit command creates an order.
    auto_paper: bool = True
    pregame_forecaster: object | None = None  # PregamePriorForecaster, optional
    listeners: list = field(default_factory=list)
    signals: dict[str, Decision] = field(default_factory=dict)  # decision_id -> approval
    decision_state: dict[str, NHLState | None] = field(default_factory=dict)
    _alert_keys: set[str] = field(default_factory=set)

    def _emit(self, kind: str, payload: dict) -> None:
        for fn in self.listeners:
            fn(kind, payload)

    # --------------------------------------------------------------- setup

    def add_game(self, game: Game, mappings: list[MarketMapping],
                 pregame_prob: dict[str, float | None] | None = None,
                 anchor_price: dict[str, Decimal] | None = None) -> None:
        self.games[game.game_id] = GameRuntime(
            game=game,
            reducer=NHLStateReducer(game.game_id, game.home_team, game.away_team),
            mappings=mappings,
            pregame_prob=pregame_prob or {game.home_team: None, game.away_team: None},
            anchor_price=dict(anchor_price or {}),
        )

    def add_source(self, h: SourceHealth) -> None:
        self.health[h.name] = h

    # --------------------------------------------------------------- inputs

    def on_game_event(self, ev: NormalizedGameEvent, payload: dict | None = None,
                      retain: bool = True) -> None:
        now = self.clock.now()
        rt = self.games.get(ev.game_id)
        h = self.health.get(ev.source)
        if h:
            h.observe(ev.received_time, ev.event_time or ev.published_time)
        self.sink.raw(RawEvent(
            raw_id=stable_id("raw", [ev.source, ev.provider_event_id, ev.seq,
                                     ev.received_time.isoformat(), ev.content_hash()]),
            source=ev.source, source_status=ev.source_status, kind=f"game:{ev.type}",
            provider_event_id=ev.provider_event_id, provider_seq=ev.seq,
            event_time=ev.event_time, published_time=ev.published_time,
            received_time=ev.received_time, payload=(payload or ev.data) if retain else {},
            retain_payload=retain))
        if rt is None:
            self.rejected_events += 1
            return
        try:
            res = rt.reducer.apply(ev)
        except InvalidEvent:
            self.rejected_events += 1
            return
        if h:
            h.gaps, h.duplicates = rt.reducer.gaps, rt.reducer.duplicates
        self.sink.state(res.state)
        self._emit("state", {"game_id": ev.game_id, "snapshot_id": res.state.snapshot_id,
                             "applied": res.applied, "note": res.note})
        stale = rt.last_forecast_at is None or now - rt.last_forecast_at >= self.recompute_every
        if res.applied and (res.material or stale) or res.state.pending_reconciliation:
            self._forecast(rt, now)
        if res.state.is_final and not rt.settled:
            self._settle(rt, ev, now)
        self._evaluate(rt, now)

    def on_market_message(self, raw: dict, received: datetime, source: str = "kalshi") -> None:
        now = self.clock.now()
        h = self.health.get(source)
        msg = raw.get("msg") or {}
        ts = msg.get("ts")
        from datetime import UTC
        exch = datetime.fromtimestamp(ts, UTC) if isinstance(ts, int | float) else None
        if h:
            h.observe(received, exch)
        ticker = self.books.handle(raw, received)
        if h:
            h.gaps, h.duplicates = self.books.gaps, self.books.duplicates
        if ticker is None:
            return
        for rt in self.games.values():
            if any(m.contract_id == ticker for m in rt.mappings):
                self._evaluate(rt, now, only_contract=ticker)

    def on_reference(self, q: SportsbookQuote) -> None:
        now = self.clock.now()
        h = self.health.get(q.source)
        if h:
            h.observe(q.received_time, q.provider_last_update)
        self.sink.quote(q)
        rt = self.games.get(q.game_id)
        if rt is None:
            return
        rt.references[q.book] = q
        self._evaluate(rt, now)

    def on_connection_lost(self) -> None:
        self.books.invalidate_all()

    def tick(self) -> None:
        """Periodic housekeeping (replay calls it per event; live on a timer)."""
        now = self.clock.now()
        for att in self.broker.process(now, self._context_for_decision):
            if att.filled:
                self.sink.fill(att.filled)
            self._emit("order_result", {
                "decision_id": att.decision_id,
                "fill": att.filled.model_dump(mode="json") if att.filled else None,
                "reasons": [r.value for r in att.reasons]})

    # --------------------------------------------------------------- internals

    def _forecast(self, rt: GameRuntime, now: datetime) -> None:
        rt.last_forecast_at = now
        state = rt.reducer.state
        if state is None:
            return
        for m in rt.mappings:
            if self.forecaster is None:
                rt.predictions.pop(m.contract_id, None)
                rt.abstentions[m.contract_id] = "NO_MODEL_LOADED"
                continue
            is_home = m.selection_team == rt.game.home_team
            fv = build_features(state, is_home, rt.pregame_prob.get(m.selection_team))
            out = self.forecaster.predict(fv, rt.game.game_id, m.selection_team, now)
            if isinstance(out, Prediction):
                rt.predictions[m.contract_id] = out
                rt.abstentions.pop(m.contract_id, None)
                self.sink.prediction(out)
            else:
                rt.predictions.pop(m.contract_id, None)
                rt.abstentions[m.contract_id] = out

    def context(self, rt: GameRuntime, m: MarketMapping) -> TriggerContext:
        book = self.books.books.get(m.contract_id)
        snap = None
        if book is not None and book.last_received is not None:
            snap = book.snapshot()
        now = self.clock.now()
        pregame = rt.reducer.state is None and now < rt.game.scheduled_start
        pred = rt.predictions.get(m.contract_id)
        if pregame and self.pregame_forecaster is not None:
            pred = self.pregame_forecaster.predict(  # type: ignore[attr-defined]
                rt.game.game_id, m.selection_team, rt.pregame_prob.get(m.selection_team), now)
        return TriggerContext(
            game=rt.game, mapping=m, state=rt.reducer.state, book=snap,
            prediction=pred, is_pregame=pregame,
            references=tuple(rt.references.values()) if self.use_references else (),
            has_position=self.broker.has_position(rt.game.game_id, m.contract_id),
            anchor_price=rt.anchor_price.get(m.contract_id),
            abstention=rt.abstentions.get(m.contract_id),
            market_last_seen=self._last_seen("market"),
            game_last_seen=self._last_seen("game_feed"),
        )

    def _last_seen(self, kind: str) -> datetime | None:
        seen = [h.last_received for h in self.health.values()
                if h.kind == kind and h.last_received is not None]
        return max(seen) if seen else None

    def _context_for_decision(self, d: Decision) -> TriggerContext:
        rt = self.games[d.game_id]
        m = next(m for m in rt.mappings if m.contract_id == d.contract_id)
        # make sure the prediction reflects the *current* state before rechecking
        state = rt.reducer.state
        p = rt.predictions.get(m.contract_id)
        if state is not None and (p is None or p.snapshot_id != state.snapshot_id):
            self._forecast(rt, self.clock.now())
        return self.context(rt, m)

    def _evaluate(self, rt: GameRuntime, now: datetime, only_contract: str | None = None) -> None:
        state = rt.reducer.state
        for m in rt.mappings:
            if only_contract and m.contract_id != only_contract:
                continue
            p = rt.predictions.get(m.contract_id)
            if state is not None and p is not None and (
                    p.snapshot_id != state.snapshot_id or p.valid_until < now):
                # clock moved on (non-material): refresh rather than reuse an invalid forecast
                self._forecast(rt, now)
            d = self.engine.evaluate(self.context(rt, m), now)
            prev = rt.last_decision.get(m.contract_id)
            changed = prev is None or prev.action != d.action or prev.reasons != d.reasons
            if changed or d.action in APPROVING:
                self.sink.decision(d)  # audit log: every change and every approval
                self._emit("decision", {"game_id": rt.game.game_id,
                                        "contract_id": m.contract_id,
                                        "decision_id": d.decision_id, "action": d.action.value})
            rt.last_decision[m.contract_id] = d
            if d.action in APPROVING:
                self._register_signal(d, state)
                if self.auto_paper:
                    self.engine.note_submitted(d)
                    self.broker.submit(d, state)
        self.tick()

    def _register_signal(self, d: Decision, state: NHLState | None) -> None:
        self.signals[d.decision_id] = d
        self.decision_state[d.decision_id] = state
        if d.dedupe_key not in self._alert_keys:  # one alert per state/strategy version
            self._alert_keys.add(d.dedupe_key)
            self.alerts.append(d)
            self._emit("signal", {"decision_id": d.decision_id, "game_id": d.game_id,
                                  "contract_id": d.contract_id,
                                  "expires_at": d.expires_at.isoformat()})

    # --------------------------------------------------------------- commands

    def preview(self, game_id: str, contract_id: str) -> Decision:
        """Fresh, side-effect-free evaluation of one contract right now."""
        rt = self.games[game_id]
        m = next(x for x in rt.mappings if x.contract_id == contract_id)
        now = self.clock.now()
        st = rt.reducer.state
        p = rt.predictions.get(contract_id)
        if st is not None and (p is None or p.snapshot_id != st.snapshot_id
                               or p.valid_until < now):
            self._forecast(rt, now)
        d = self.engine.evaluate(self.context(rt, m), now)
        if d.action in APPROVING:
            self.signals[d.decision_id] = d
            self.decision_state[d.decision_id] = rt.reducer.state
        return d

    def submit_signal(self, decision_id: str, quantity: int | None = None):
        """Queue a paper order for an approved, unexpired signal. Raises SignalError."""
        d = self.signals.get(decision_id)
        if d is None:
            raise SignalError("UNKNOWN_SIGNAL", "no such approved signal")
        now = self.clock.now()
        if now > d.expires_at:
            raise SignalError("SIGNAL_EXPIRED", f"signal expired at {d.expires_at.isoformat()}")
        rt = self.games[d.game_id]
        latest = rt.last_decision.get(d.contract_id or "")
        if latest is not None and latest.decision_time > d.decision_time \
                and latest.action not in APPROVING:
            raise SignalError("SIGNAL_SUPERSEDED",
                              f"newer decision {latest.action.value}: "
                              + ",".join(r.value for r in latest.reasons))
        try:
            order = self.broker.submit(d, self.decision_state.get(decision_id), quantity, now)
        except ValueError as e:
            raise SignalError("INVALID_QUANTITY", str(e)) from e
        self.engine.note_submitted(d)
        return order


    def _settle(self, rt: GameRuntime, ev: NormalizedGameEvent, now: datetime) -> None:
        s = rt.reducer.state
        assert s is not None
        res = FinalResult(
            home_score=s.home_score, away_score=s.away_score, status="FINAL",
            nhl_decided_in=s.final_decided_in,  # type: ignore[arg-type]
            nhl_regulation_home=ev.data.get("regulation_home_score"),
            nhl_regulation_away=ev.data.get("regulation_away_score"),
        )
        for m in rt.mappings:
            pos = self.broker.positions.get((rt.game.game_id, m.contract_id))
            outcome, detail = resolve(m, rt.game, res)
            self.sink.settlement(Settlement(
                settlement_id=stable_id("set", [rt.game.game_id, m.contract_id]),
                game_id=rt.game.game_id, contract_id=m.contract_id,
                selection_team=m.selection_team, outcome=outcome, settled_time=now,
                detail=detail))
            if pos is None or pos.contracts == 0 or outcome == Outcome.PENDING:
                continue
            payout = {Outcome.WIN: Decimal(pos.contracts), Outcome.LOSS: Decimal(0),
                      Outcome.VOID: pos.total_cost + pos.total_fees}[outcome]
            fee = self.engine.fee_model.settlement_fee(pos.contracts, outcome == Outcome.WIN)
            self.engine.ledger.record_settlement(rt.game.game_id, m.selection_team, payout - fee)
            self._emit("settlement", {"game_id": rt.game.game_id, "contract_id": m.contract_id,
                                      "outcome": outcome.value, "payout": str(payout - fee)})
        rt.settled = True

    # --------------------------------------------------------------- views

    def game_view(self, game_id: str) -> dict:
        rt = self.games[game_id]
        now = self.clock.now()
        s = rt.reducer.state
        rows = []
        for m in rt.mappings:
            ctx = self.context(rt, m)
            d = rt.last_decision.get(m.contract_id)
            p = ctx.prediction
            pos = self.broker.positions.get((game_id, m.contract_id))
            room, _ = self.engine.ledger.remaining(game_id, m.selection_team, now.date())
            ref_age = None
            if rt.references:
                newest = max(rt.references.values(), key=lambda q: q.received_time)
                ref_age = (now - newest.received_time).total_seconds()
            rows.append({
                "contract_id": m.contract_id,
                "selection": m.selection_team,
                "settlement_rule": m.settlement_rule.value,
                "probability": p.probability if p else None,
                "probability_low": p.probability_low if p else None,
                "probability_high": p.probability_high if p else None,
                "reliability": p.reliability if p else "NONE",
                "model_status": p.model_status.value if p else "NO_PREDICTION",
                "abstention": rt.abstentions.get(m.contract_id),
                "best_ask": str(ctx.book.best_ask) if ctx.book and ctx.book.best_ask else None,
                "best_bid": str(ctx.book.best_bid) if ctx.book and ctx.book.best_bid else None,
                "ask_depth": sum(lv.quantity for lv in ctx.book.asks) if ctx.book else None,
                "book_valid": ctx.book.valid if ctx.book else False,
                "reference_age_seconds": ref_age,
                "action": d.action.value if d else None,
                "reasons": [r.value for r in d.reasons] if d else [],
                "notes": list(d.notes) if d else [],
                "ev": d.ev.model_dump(mode="json") if d and d.ev else None,
                "remaining_paper_exposure": str(room),
                "position": pos.model_dump(mode="json") | {
                    "average_entry": str(pos.average_entry),
                    "average_entry_all_in": str(pos.average_entry_all_in)} if pos else None,
                "decision_expires_at": d.expires_at.isoformat() if d else None,
                "decision_id": d.decision_id if d else None,
                "signal_eligible": bool(d and d.action in APPROVING and now <= d.expires_at),
                "invalidation": "Decision invalid after expiry, any material game event, "
                                "book gap, or stale data.",
            })
        favored = None
        probs = {r["selection"]: r["probability"] for r in rows if r["probability"] is not None}
        if probs:
            favored = max(probs, key=probs.get)  # type: ignore[arg-type]
        value_side = [r["selection"] for r in rows
                      if r["action"] in (Action.PAPER_ENTRY.value, Action.PAPER_ADD.value)]
        return {
            "game": rt.game.model_dump(mode="json"),
            "state": s.model_dump(mode="json") if s else None,
            "model_favored_team": favored,
            "value_side": value_side[0] if value_side else None,
            "contracts": rows,
        }
