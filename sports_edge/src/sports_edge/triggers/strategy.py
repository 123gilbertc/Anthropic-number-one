"""Strategy configuration.

Every numeric threshold here is PROVISIONAL until estimated on
training/validation data and frozen for a prospective test. The
``provisional`` flag is surfaced in every decision's notes so nobody mistakes
a smoke-test value for an optimized one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from sports_edge.domain.enums import SettlementRule


@dataclass(frozen=True)
class StrategyConfig:
    strategy_version: str
    # dip_conditional: a persistent dip starts evaluation; EV gates decide.
    # dip_unconditional: COMPARISON BASELINE ONLY - adds on any persistent dip, no EV gate.
    # any_edge: evaluate on every update (live-only entries).
    entry_mode: Literal["dip_conditional", "dip_unconditional", "any_edge", "pregame_only"]
    forecast_rule: SettlementRule
    provisional: bool = True
    # freshness / alignment
    max_state_age: timedelta = timedelta(seconds=45)
    max_book_age: timedelta = timedelta(seconds=10)
    max_reference_age: timedelta = timedelta(seconds=90)
    reference_post_event_allowance: timedelta = timedelta(seconds=0)
    require_reference: bool = False
    # After a goal/penalty/etc., resting quotes may not reflect it yet and faster
    # participants will pick them off first. Abstain for this long after the event.
    post_event_settle: timedelta = timedelta(seconds=15)
    reference_disagreement_pp: float = 12.0  # probability points -> REVIEW, not a buy
    # book quality
    max_spread: Decimal = Decimal("0.05")
    depth_haircut: Decimal = Decimal("0.5")  # assume half the displayed size is reachable
    min_contracts: int = 1
    # value
    min_conservative_ev_cents_per_contract: Decimal = Decimal("2")
    max_order_dollars: Decimal = Decimal("25")
    # dip trigger (starts evaluation only; never approves on its own)
    dip_drop_from_anchor: Decimal = Decimal("0.08")
    dip_persistence: timedelta = timedelta(seconds=20)
    cooldown: timedelta = timedelta(minutes=3)
    alert_ttl: timedelta = timedelta(seconds=45)
    # paper execution
    decision_delay: timedelta = timedelta(seconds=2)
    tags: tuple[str, ...] = field(default_factory=tuple)


def provisional_dip_strategy(rule: SettlementRule = SettlementRule.NHL_INCLUDING_OT_SO,
                             ) -> StrategyConfig:
    return StrategyConfig(strategy_version="dip_cond_v0_provisional",
                          entry_mode="dip_conditional", forecast_rule=rule)
