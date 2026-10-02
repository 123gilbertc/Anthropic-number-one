"""Paper execution.

A decision is not a fill. Before recording a paper fill the broker:

1. waits the configured decision delay (modelled: the order is matched
   against the first book seen at or after ``decision_time + delay``);
2. re-runs the full trigger engine on the *current* context (state, book,
   prediction, exposure). If that no longer approves, or the game state
   changed, or the decision expired, there is no fill;
3. fills conservatively against the current book: only a haircut fraction
   of displayed depth, never above the decision's modeled average price plus
   one tick (no chasing), partial fills allowed;
4. reserves cash and exposure immediately.

Queue position is unknown, so a touched limit price is never assumed to fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sports_edge.domain.enums import Action, Reason
from sports_edge.domain.records import Decision, NHLState, PaperFill, Position, stable_id
from sports_edge.pricing.fills import walk_asks
from sports_edge.triggers.engine import TriggerContext, TriggerEngine

APPROVING = {Action.PAPER_ENTRY, Action.PAPER_ADD}


@dataclass(frozen=True)
class PendingOrder:
    decision: Decision
    not_before: datetime
    material_key: tuple


def material_key(state: NHLState) -> tuple:
    """What must not change between decision and fill (clock ticks may)."""
    return (state.home_score, state.away_score, state.period >= 4,
            state.last_material_event_time, state.last_material_event_kind,
            state.home_skaters, state.away_skaters, state.home_goalie, state.away_goalie,
            state.home_net_empty, state.away_net_empty)


@dataclass
class FillAttempt:
    decision_id: str
    filled: PaperFill | None
    reasons: tuple[Reason, ...]
    recheck: Decision | None


@dataclass
class PaperBroker:
    engine: TriggerEngine
    pending: list[PendingOrder] = field(default_factory=list)
    positions: dict[tuple[str, str], Position] = field(default_factory=dict)
    fills: list[PaperFill] = field(default_factory=list)
    attempts: list[FillAttempt] = field(default_factory=list)

    def submit(self, decision: Decision, state: NHLState, quantity: int | None = None,
               now: datetime | None = None) -> PendingOrder:
        """Queue an approved decision. ``quantity`` may only shrink the planned size.

        The order is matched no earlier than ``decision_delay`` after ``now``
        (default: the decision time) and only if every gate still passes then.
        """
        if decision.action not in APPROVING:
            raise ValueError("only approving decisions can be submitted")
        if state.snapshot_id != decision.snapshot_id:
            raise ValueError("state does not match the decision")
        if quantity is not None:
            if not 0 < quantity <= decision.planned_quantity:
                raise ValueError("quantity must be between 1 and the eligible size")
            decision = decision.model_copy(update={
                "planned_quantity": quantity,
                "planned_cost": decision.planned_cost * quantity / decision.planned_quantity})
        start = now or decision.decision_time
        order = PendingOrder(decision, start + self.engine.cfg.decision_delay,
                             material_key(state))
        self.pending.append(order)
        return order

    def has_position(self, game_id: str, contract_id: str) -> bool:
        p = self.positions.get((game_id, contract_id))
        return p is not None and p.contracts > 0

    def process(self, now: datetime, context_for) -> list[FillAttempt]:
        """``context_for(decision) -> TriggerContext`` returns the *current* context."""
        due = [o for o in self.pending if o.not_before <= now]
        self.pending = [o for o in self.pending if o.not_before > now]
        out = [self._attempt(o, now, context_for(o.decision)) for o in due]
        self.attempts.extend(out)
        return out

    def _attempt(self, order: PendingOrder, now: datetime, ctx: TriggerContext) -> FillAttempt:
        d = order.decision
        if now > d.expires_at:
            return FillAttempt(d.decision_id, None, (Reason.FILL_RECHECK_FAILED,), None)
        if ctx.state is None or material_key(ctx.state) != order.material_key:
            return FillAttempt(d.decision_id, None, (Reason.FILL_RECHECK_FAILED,
                                                     Reason.PREDICTION_STALE), None)
        # Re-run every gate. The engine's dedupe/cooldown would reject an identical
        # re-approval, so recheck with a scratch engine sharing config and ledger.
        scratch = TriggerEngine(self.engine.cfg, self.engine.fee_model, self.engine.ledger,
                                self.engine.usable_statuses,
                                self.engine.allowed_model_statuses, self.engine.demo_label,
                                _price_hist=self.engine._price_hist)
        recheck = scratch.evaluate(ctx, now)
        if recheck.action not in APPROVING or recheck.planned_quantity <= 0 \
                or d.planned_quantity <= 0:
            return FillAttempt(d.decision_id, None,
                               (Reason.FILL_RECHECK_FAILED, *recheck.reasons), recheck)
        assert ctx.book is not None
        # never pay more than the decision's modeled average price + one tick
        limit = (d.planned_cost / d.planned_quantity) + ctx.mapping.tick_size
        est = walk_asks(ctx.book.asks, min(d.planned_quantity, recheck.planned_quantity),
                        self.engine.fee_model, limit_price=limit,
                        depth_haircut=self.engine.cfg.depth_haircut)
        if est.filled == 0:
            return FillAttempt(d.decision_id, None, (Reason.NO_FILL,), recheck)
        self.engine.ledger.record_purchase(d.game_id, ctx.mapping.selection_team, now.date(),
                                           est.cost, est.fees)
        fill = PaperFill(
            fill_id=stable_id("fill", [d.decision_id, now.isoformat()]),
            decision_id=d.decision_id,
            contract_id=ctx.mapping.contract_id,
            selection_team=ctx.mapping.selection_team,
            game_id=d.game_id,
            requested_quantity=d.planned_quantity,
            filled_quantity=est.filled,
            cost=est.cost,
            fees=est.fees,
            fill_time=now,
            book_id=ctx.book.book_id,
            assumptions=(
                f"delay={self.engine.cfg.decision_delay.total_seconds()}s",
                f"depth_haircut={self.engine.cfg.depth_haircut}",
                f"limit={limit}",
                "no queue priority assumed",
            ),
        )
        key = (d.game_id, ctx.mapping.contract_id)
        prev = self.positions.get(key)
        self.positions[key] = Position(
            game_id=d.game_id,
            contract_id=ctx.mapping.contract_id,
            selection_team=ctx.mapping.selection_team,
            contracts=(prev.contracts if prev else 0) + est.filled,
            total_cost=(prev.total_cost if prev else Decimal(0)) + est.cost,
            total_fees=(prev.total_fees if prev else Decimal(0)) + est.fees,
        )
        self.fills.append(fill)
        reasons = (Reason.PARTIAL_FILL,) if est.filled < d.planned_quantity else ()
        return FillAttempt(d.decision_id, fill, reasons, recheck)
