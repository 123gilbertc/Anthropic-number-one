"""Deterministic trigger engine.

    WATCH -> CANDIDATE -> REVIEW -> PAPER_ENTRY / PAPER_ADD / HOLD / NO_ADD /
                                    STOP_BUYING / DATA_BLOCKED

Gates run in a fixed order and every gate that fails is recorded as a reason
code. A price drop can move a contract to CANDIDATE, but only a positive
*conservative* EV above the configured margin, with all data, model and risk
gates passing, can produce PAPER_ENTRY / PAPER_ADD.

STOP_BUYING means "no further additions". It never means an exit was executed.

LLM output has no input here: LLMs run in shadow mode elsewhere and cannot
change risk limits or approve anything.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sports_edge.contracts.settlement import MappingError, validate_mapping
from sports_edge.domain.enums import Action, ModelStatus, Reason, SourceStatus
from sports_edge.domain.records import (
    Decision,
    Game,
    MarketMapping,
    NHLState,
    OrderBookSnapshot,
    Prediction,
    SportsbookQuote,
    stable_id,
)
from sports_edge.pricing.ev import expected_value
from sports_edge.pricing.fees import FeeModel
from sports_edge.pricing.fills import max_quantity_within_budget, walk_asks
from sports_edge.pricing.odds import OddsError, remove_margin
from sports_edge.risk.exposure import ExposureLedger
from sports_edge.triggers.alignment import check_book, check_reference, check_state
from sports_edge.triggers.strategy import StrategyConfig

USABLE_STATUSES = frozenset({SourceStatus.LIVE, SourceStatus.REPLAY})
ZERO = Decimal("0")


@dataclass(frozen=True)
class TriggerContext:
    game: Game
    mapping: MarketMapping
    state: NHLState | None
    book: OrderBookSnapshot | None
    prediction: Prediction | None
    references: tuple[SportsbookQuote, ...] = ()
    has_position: bool = False
    anchor_price: Decimal | None = None  # e.g. pregame ask when the team was selected
    is_pregame: bool = False
    abstention: str | None = None  # why the forecaster produced no prediction
    # connection liveness (any message incl. heartbeats), not "time since last change"
    market_last_seen: datetime | None = None
    game_last_seen: datetime | None = None


@dataclass
class TriggerEngine:
    cfg: StrategyConfig
    fee_model: FeeModel
    ledger: ExposureLedger
    usable_statuses: frozenset[SourceStatus] = USABLE_STATUSES
    allowed_model_statuses: frozenset[ModelStatus] = frozenset({ModelStatus.VALIDATED})
    demo_label: str | None = None  # set only for synthetic mechanics demos; added to every note
    _price_hist: dict[str, deque[tuple[datetime, Decimal]]] = field(default_factory=dict)
    _dedupe: set[str] = field(default_factory=set)
    _last_approval: dict[str, datetime] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers

    def observe_price(self, contract_id: str, t: datetime, best_ask: Decimal | None) -> None:
        if best_ask is None:
            return
        h = self._price_hist.setdefault(contract_id, deque(maxlen=5000))
        h.append((t, best_ask))

    def _dip(self, ctx: TriggerContext, now: datetime) -> tuple[bool, bool]:
        """(dip_detected, dip_persistent) relative to the anchor price."""
        hist = self._price_hist.get(ctx.mapping.contract_id)
        if not hist or ctx.anchor_price is None:
            return False, False
        threshold = ctx.anchor_price - self.cfg.dip_drop_from_anchor
        if hist[-1][1] > threshold:
            return False, False
        start = hist[-1][0]
        for t, px in reversed(hist):
            if px > threshold:
                break
            start = t
        return True, now - start >= self.cfg.dip_persistence

    def _decision(self, ctx: TriggerContext, now: datetime, action: Action,
                  reasons: list[Reason], ev=None, room: Decimal = ZERO,
                  notes: tuple[str, ...] = (), fill=None) -> Decision:
        snap = ctx.state.snapshot_id if ctx.state else None
        pred = ctx.prediction
        key = stable_id("dk", [ctx.mapping.contract_id, snap, self.cfg.strategy_version,
                               action.value, sorted(r.value for r in reasons)])
        expires = now + self.cfg.alert_ttl
        if pred is not None and pred.valid_until < expires:
            expires = pred.valid_until
        if self.cfg.provisional:
            notes = notes + ("PROVISIONAL thresholds: not optimized",)
        if self.demo_label:
            notes = notes + (self.demo_label,)
        return Decision(
            decision_id=stable_id("dec", [key, now.isoformat()]),
            dedupe_key=key,
            game_id=ctx.game.game_id,
            venue=ctx.mapping.venue,
            contract_id=ctx.mapping.contract_id,
            selection_team=ctx.mapping.selection_team,
            settlement_rule=ctx.mapping.settlement_rule,
            snapshot_id=snap,
            book_id=ctx.book.book_id if ctx.book else None,
            reference_quote_ids=tuple(q.quote_id for q in ctx.references),
            prediction_id=pred.prediction_id if pred else None,
            feature_version=pred.feature_version if pred else None,
            model_version=pred.model_version if pred else None,
            strategy_version=self.cfg.strategy_version,
            action=action,
            reasons=tuple(dict.fromkeys(reasons)),
            ev=ev,
            planned_quantity=fill.filled if fill is not None else 0,
            planned_cost=fill.cost if fill is not None else ZERO,
            max_eligible_addition=room,
            decision_time=now,
            expires_at=expires,
            notes=notes,
        )

    # ------------------------------------------------------------------ evaluate

    def evaluate(self, ctx: TriggerContext, now: datetime) -> Decision:
        ref_notes: list[str] = []
        d = self._evaluate(ctx, now, ref_notes)
        if ref_notes:
            d = d.model_copy(update={"notes": d.notes + tuple(ref_notes)})
        return d

    def _evaluate(self, ctx: TriggerContext, now: datetime, ref_notes: list[str]) -> Decision:
        cfg = self.cfg
        if ctx.book is not None:
            self.observe_price(ctx.mapping.contract_id, ctx.book.received_time, ctx.book.best_ask)

        # 1. data gates -------------------------------------------------------
        blocked: list[Reason] = []
        try:
            validate_mapping(ctx.mapping, ctx.game, cfg.forecast_rule)
        except MappingError:
            blocked.append(Reason.SETTLEMENT_MISMATCH
                           if ctx.mapping.settlement_rule != cfg.forecast_rule
                           else Reason.MAPPING_INVALID)
        if not ctx.is_pregame:
            if ctx.state is None or ctx.state.source_status not in self.usable_statuses:
                blocked.append(Reason.GAME_FEED_NOT_CONNECTED)
            if ctx.state is not None:
                blocked += check_state(ctx.state, now, cfg, ctx.game_last_seen)
                ev_t = ctx.state.last_material_event_time
                if ev_t is not None and now - ev_t < cfg.post_event_settle:
                    blocked.append(Reason.MARKET_SETTLING)
                if ctx.state.is_final:
                    blocked.append(Reason.MARKET_INACTIVE)
        if ctx.book is not None and ctx.book.source_status not in self.usable_statuses:
            blocked.append(Reason.BOOK_INVALID)
        blocked += check_book(ctx.book, now, cfg, ctx.market_last_seen)

        fair_ref: float | None = None
        for q in ctx.references:
            if ctx.state is not None:
                r = check_reference(q, ctx.state, now, cfg)
                if r is not None:
                    # A strategy that relies on the reference must abstain; otherwise the
                    # misaligned quote is simply excluded from the comparison.
                    if cfg.require_reference:
                        blocked.append(r)
                    else:
                        ref_notes.append(f"{r.value}: excluded {q.book} quote {q.quote_id}")
                    continue
            if q.settlement_rule != ctx.mapping.settlement_rule or q.game_id != ctx.game.game_id:
                blocked.append(Reason.SETTLEMENT_MISMATCH)
                continue
            expected = {ctx.game.home_team, ctx.game.away_team}
            if q.market == "h2h_3_way":
                expected.add("Draw")
            try:
                fair_ref = remove_margin(q.prices_american, expected)[ctx.mapping.selection_team]
            except OddsError:
                blocked.append(Reason.MAPPING_INVALID)
        if cfg.require_reference and fair_ref is None:
            blocked.append(Reason.REFERENCE_STALE)
        if blocked:
            return self._decision(ctx, now, Action.DATA_BLOCKED, blocked)

        # 2. model gates ------------------------------------------------------
        pred = ctx.prediction
        if pred is None and ctx.abstention and ctx.abstention.startswith("CRITICAL"):
            return self._decision(ctx, now, Action.WATCH, [Reason.CRITICAL_FEATURE_MISSING],
                                  notes=(ctx.abstention,))
        mode = cfg.entry_mode
        pregame_capable = mode in ("pregame_only", "pregame_plus_dip",
                                   "pregame_plus_dip_unconditional")
        dip_mode = mode in ("dip_conditional", "dip_unconditional", "pregame_plus_dip",
                            "pregame_plus_dip_unconditional")
        unconditional = (mode in ("dip_unconditional", "pregame_plus_dip_unconditional")
                         and not ctx.is_pregame)
        expected_snapshot = (ctx.state.snapshot_id if ctx.state is not None
                             else f"pregame:{ctx.game.game_id}" if ctx.is_pregame else None)
        usable_pred = (
            pred is not None and pred.model_status in self.allowed_model_statuses
            and pred.valid_until >= now and expected_snapshot is not None
            and pred.snapshot_id == expected_snapshot
            and pred.selection_team == ctx.mapping.selection_team
            and pred.settlement_rule == ctx.mapping.settlement_rule)
        if ctx.is_pregame and not pregame_capable:
            return self._decision(ctx, now, Action.WATCH, [])
        if not unconditional:
            if pred is None or pred.model_status not in self.allowed_model_statuses:
                return self._decision(ctx, now, Action.WATCH, [Reason.MODEL_NOT_VALIDATED])
            if not usable_pred:
                return self._decision(ctx, now, Action.WATCH, [Reason.PREDICTION_STALE])

        # 3. risk room --------------------------------------------------------
        room, binding = self.ledger.remaining(ctx.game.game_id, ctx.mapping.selection_team,
                                              now.date())
        if room <= ZERO:
            act = Action.STOP_BUYING if ctx.has_position else Action.NO_ADD
            return self._decision(ctx, now, act, list(binding))

        # 4. dip trigger (only starts evaluation) ------------------------------
        reasons: list[Reason] = []
        if mode == "pregame_only" and not ctx.is_pregame:
            return self._decision(ctx, now, Action.WATCH, [], room=room)
        if dip_mode and not ctx.is_pregame:
            detected, persistent = self._dip(ctx, now)
            if not detected:
                return self._decision(ctx, now, Action.WATCH, [], room=room)
            reasons.append(Reason.DIP_DETECTED)
            if not persistent:
                return self._decision(ctx, now, Action.CANDIDATE,
                                      reasons + [Reason.DIP_NOT_PERSISTENT], room=room)
        # 5. book quality and size --------------------------------------------
        book = ctx.book
        assert book is not None
        if book.spread is not None and book.spread > cfg.max_spread:
            return self._decision(ctx, now, Action.NO_ADD, reasons + [Reason.SPREAD_TOO_WIDE],
                                  room=room)
        budget = min(room, cfg.max_order_dollars)
        qty = max_quantity_within_budget(book.asks, budget, self.fee_model,
                                         depth_haircut=cfg.depth_haircut)
        if qty < max(cfg.min_contracts, ctx.mapping.min_quantity):
            return self._decision(ctx, now, Action.NO_ADD,
                                  reasons + [Reason.DEPTH_INSUFFICIENT], room=room)
        fill = walk_asks(book.asks, qty, self.fee_model, depth_haircut=cfg.depth_haircut)

        # 6. value ------------------------------------------------------------
        if unconditional:
            # Baseline for comparison: buys the dip with no value test at all.
            ev_u = (expected_value(fill, pred.probability, pred.probability_low, self.fee_model)
                    if usable_pred and pred is not None else None)
            last = self._last_approval.get(ctx.mapping.contract_id)
            if last is not None and now - last < cfg.cooldown:
                return self._decision(ctx, now, Action.HOLD, reasons + [Reason.COOLDOWN], ev_u,
                                      room)
            act = Action.PAPER_ADD if ctx.has_position else Action.PAPER_ENTRY
            return self._decision(ctx, now, act, reasons, ev_u, room,
                                  notes=("UNCONDITIONAL DIP BASELINE: no EV gate",), fill=fill)
        assert pred is not None
        ev = expected_value(fill, pred.probability, pred.probability_low, self.fee_model)
        margin = cfg.min_conservative_ev_cents_per_contract / 100 * fill.filled
        if ev.ev_point <= ZERO:
            return self._decision(ctx, now, Action.NO_ADD, reasons + [Reason.EV_BELOW_MARGIN],
                                  ev, room)
        if ev.ev_conservative <= margin:
            r = Reason.CONSERVATIVE_EV_NEGATIVE if ev.ev_conservative <= 0 else \
                Reason.EV_BELOW_MARGIN
            return self._decision(ctx, now, Action.NO_ADD, reasons + [r], ev, room)

        # 7. reference disagreement: a warning to investigate, not a buy ----------
        if fair_ref is not None and abs(pred.probability - fair_ref) * 100 > \
                cfg.reference_disagreement_pp:
            return self._decision(ctx, now, Action.REVIEW,
                                  reasons + [Reason.REFERENCE_DISAGREEMENT], ev, room)

        # 8. cooldown and dedupe --------------------------------------------------
        last = self._last_approval.get(ctx.mapping.contract_id)
        if last is not None and now - last < cfg.cooldown:
            return self._decision(ctx, now, Action.HOLD, reasons + [Reason.COOLDOWN], ev, room)

        action = Action.PAPER_ADD if ctx.has_position else Action.PAPER_ENTRY
        d = self._decision(ctx, now, action, reasons + [Reason.ALL_GATES_PASSED], ev, room,
                           fill=fill)
        if d.dedupe_key in self._dedupe:
            return self._decision(ctx, now, Action.HOLD, [Reason.DUPLICATE_ALERT], ev, room)
        return d

    def note_submitted(self, d: Decision) -> None:
        """Start cooldown / dedupe only when a paper order is actually submitted.

        ``evaluate`` itself is side-effect free (apart from price history), so
        previews and repeated evaluations never consume a signal.
        """
        if d.contract_id is None:
            return
        self._dedupe.add(d.dedupe_key)
        self._last_approval[d.contract_id] = d.decision_time
