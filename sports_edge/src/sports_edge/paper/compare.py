"""Matched paper-strategy comparison on the same replay, capital and risk limits.

Reports, per strategy: approvals, fills (incl. partial), unfilled approvals,
skipped signals, abstentions, total spend, settled P&L, and the conditional
win rate of filled positions. Small samples are reported as such; an empty
subset is UNKNOWN, never a borrowed rate.

Strategies that cannot be run honestly on the given data are listed as
NOT_RUN with the reason (e.g. no pregame model, no prospective LLM logs).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sports_edge.domain.enums import Action, Outcome
from sports_edge.evaluation.metrics import conditional_win_rate
from sports_edge.forecast.train import train_synthetic
from sports_edge.replay.runner import replay_file
from sports_edge.triggers.strategy import provisional_dip_strategy


def _summarize(name: str, r) -> dict:
    s = r.sink
    approvals = [d for d in s.decisions if d.action in (Action.PAPER_ENTRY, Action.PAPER_ADD)]
    filled_ids = {f.decision_id for f in s.fills}
    spend = sum((f.cost + f.fees for f in s.fills), Decimal(0))
    outcomes = {(x.game_id, x.contract_id): x.outcome for x in s.settlements}
    pnl = Decimal(0)
    wins = []
    for (gid, cid), pos in r.monitor.broker.positions.items():
        o = outcomes.get((gid, cid))
        cost = pos.total_cost + pos.total_fees
        if o == Outcome.WIN:
            pnl += pos.contracts - cost
            wins.append(1)
        elif o == Outcome.LOSS:
            pnl -= cost
            wins.append(0)
    return {
        "strategy": name,
        "approvals": len(approvals),
        "fills": len(s.fills),
        "partial_fills": sum(1 for f in s.fills if f.filled_quantity < f.requested_quantity),
        "approved_but_unfilled": len([d for d in approvals if d.decision_id not in filled_ids]),
        "no_add_signals": sum(1 for d in s.decisions if d.action == Action.NO_ADD),
        "abstentions_data_blocked": sum(1 for d in s.decisions
                                        if d.action == Action.DATA_BLOCKED),
        "total_spend_all_in": str(spend),
        "settled_pnl": str(pnl),
        "roi_on_spend": str((pnl / spend * 100).quantize(Decimal("0.01"))) if spend else None,
        "filled_position_win_rate": conditional_win_rate(wins),
    }


def compare_strategies(path: Path, mechanics_demo: bool = False) -> dict:
    fc = train_synthetic(n_games=300).forecaster if mechanics_demo else None
    base = provisional_dip_strategy()
    variants = {
        "dip_conditional (model-validated adds)": base,
        "dip_unconditional (baseline: add on any dip)":
            replace(base, strategy_version="dip_uncond_v0", entry_mode="dip_unconditional"),
        "live_only_any_edge (no pregame stake)":
            replace(base, strategy_version="live_any_edge_v0", entry_mode="any_edge"),
        "pregame_only":
            replace(base, strategy_version="pregame_only_v0", entry_mode="pregame_only"),
        "pregame + model-validated dip adds":
            replace(base, strategy_version="pregame_dip_v0", entry_mode="pregame_plus_dip"),
        "pregame + unconditional dip adds (baseline)":
            replace(base, strategy_version="pregame_dip_uncond_v0",
                    entry_mode="pregame_plus_dip_unconditional"),
    }
    results = [_summarize(n, replay_file(path, forecaster=fc, cfg=cfg,
                                         mechanics_demo=mechanics_demo))
               for n, cfg in variants.items()]
    return {
        "data": str(path.name),
        "mechanics_demo": mechanics_demo,
        "warning": "One synthetic game. Nothing here is evidence about real markets."
        if mechanics_demo else "No model loaded: strategies that need a model cannot act.",
        "results": results,
        "not_run": [
            {"strategy": "pregame strategies without --mechanics-demo",
             "reason": "no validated pregame model: they correctly take no positions"},
            {"strategy": "quant-only vs LLM-enhanced",
             "reason": "LLMs have zero decision weight until prospective locked logs exist"},
            {"strategy": "same-time sportsbook baseline",
             "reason": "requires fresh, aligned reference quotes for the whole replay"},
        ],
    }
