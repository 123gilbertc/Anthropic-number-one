"""The seven synthetic acceptance cases from the specification."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from helpers import NOW, RULE, book, ctx, engine, prediction, quote, state

from sports_edge.domain.enums import Action, Reason
from sports_edge.evaluation.metrics import conditional_win_rate
from sports_edge.llm.review import EvidenceBundle, EvidenceItem, ShadowReviewer
from sports_edge.replay.runner import replay_file
from sports_edge.risk.exposure import RiskLimits
from sports_edge.triggers.strategy import StrategyConfig

FIXTURE = Path(__file__).parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"


def test_case1_estimate_65_to_57_price_45_is_evaluated_not_auto_bought():
    st = state()
    # (a) wide uncertainty: point EV positive but conservative EV fails -> no buy
    d = engine().evaluate(ctx(st, book("0.45"), prediction(st, 0.57, lo=0.44)), NOW)
    assert d.action == Action.NO_ADD
    assert d.ev is not None and d.ev.ev_point > 0
    assert Reason.CONSERVATIVE_EV_NEGATIVE in d.reasons
    # (b) tight uncertainty, fresh data, room available -> approved only after all gates
    st2 = state()
    d2 = engine().evaluate(ctx(st2, book("0.45"), prediction(st2, 0.57, lo=0.53)), NOW)
    assert d2.action == Action.PAPER_ENTRY and Reason.ALL_GATES_PASSED in d2.reasons
    assert d2.ev is not None and d2.ev.ev_conservative > 0
    assert d2.ev.entry_cost + d2.ev.expected_fees <= Decimal("25")  # max order size respected
    # (c) the same numbers with a silent market connection are blocked
    stale = replace(ctx(st2, book("0.45", age=60), prediction(st2, 0.57, lo=0.53)),
                    market_last_seen=NOW - timedelta(seconds=60))
    d3 = engine().evaluate(stale, NOW)
    assert d3.action == Action.DATA_BLOCKED and Reason.BOOK_STALE in d3.reasons


def test_case2_estimate_40_price_45_rejected():
    st = state()
    d = engine().evaluate(ctx(st, book("0.45"), prediction(st, 0.40, lo=0.37)), NOW)
    assert d.action == Action.NO_ADD and Reason.EV_BELOW_MARGIN in d.reasons


def test_case3_reference_from_before_latest_score_blocks_signal():
    cfg = StrategyConfig(strategy_version="ref_baseline", entry_mode="any_edge",
                         forecast_rule=RULE, require_reference=True)
    st = state(material_ago=30)  # goal 30 s ago
    stale_ref = quote(updated_ago=40, received_ago=2)  # received recently, published pre-goal
    d = engine(cfg).evaluate(
        ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57), references=(stale_ref,)), NOW)
    assert d.action == Action.DATA_BLOCKED and Reason.REFERENCE_PRE_EVENT in d.reasons
    # an aligned (post-goal) reference that roughly agrees is accepted
    ok = engine(cfg).evaluate(
        ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57),
            references=(quote(updated_ago=10, home=-140, away=120),)), NOW)
    assert ok.action == Action.PAPER_ENTRY
    # an aligned reference that strongly disagrees is a warning to investigate, not a buy
    warn = engine(cfg).evaluate(
        ctx(st, book("0.45"), prediction(st, 0.60, lo=0.57),
            references=(quote(updated_ago=10, home=110, away=-130),)), NOW)
    assert warn.action == Action.REVIEW and Reason.REFERENCE_DISAGREEMENT in warn.reasons


def test_case4_overall_hit_rate_not_borrowed_for_dip_subset():
    overall = conditional_win_rate([1] * 65 + [0] * 35)
    assert overall["win_rate"] == 0.65
    dipped = conditional_win_rate([])
    assert dipped["status"] == "UNKNOWN" and dipped["win_rate"] is None


def test_case5_team_cap_blocks_regardless_of_llm():
    lim = RiskLimits.example_1000_bankroll_100_cap()
    eng = engine(limits=lim)
    eng.ledger.record_purchase("G1", "BOS", NOW.date(), Decimal("98"), Decimal("2"))
    st = state()
    d = eng.evaluate(ctx(st, book("0.30"), prediction(st, 0.70, lo=0.65), has_position=True), NOW)
    assert d.action == Action.STOP_BUYING and Reason.TEAM_CAP_REACHED in d.reasons
    assert d.max_eligible_addition == 0
    # The engine has no LLM input at all: there is nothing an LLM could pass to override this.
    assert "llm" not in eng.evaluate.__code__.co_varnames


class _AgreeingButObsolete:
    name, model_id = "fake", "fake-model"

    def __init__(self, cited):
        self.cited = cited

    async def complete(self, system, user, timeout_s):
        import json
        return json.dumps({"snapshot_id": self.cited, "evidence_ids": ["state"],
                           "supported_concerns": [], "missing_information": [],
                           "abstain": False, "reasoning": "agree"}), Decimal("0.001")


def test_case6_llm_agrees_but_cites_obsolete_state_is_discarded():
    old, new = state(away=1), state(away=2)
    bundle = EvidenceBundle(bundle_id="b", decision_id="d", snapshot_id=old.snapshot_id,
                            created_time=NOW, items=(EvidenceItem(
                                evidence_id="state", kind="state", as_of=NOW,
                                content=old.model_dump(mode="json")),))
    rv = ShadowReviewer([_AgreeingButObsolete(old.snapshot_id)])
    [rec] = asyncio.run(rv.review(bundle, lambda: new.snapshot_id, lambda: NOW))
    assert rec.status == "STALE" and rec.decision_weight == 0.0
    # Re-evaluation on the current state: TOR scored again; estimate now too low -> no add.
    d = engine().evaluate(ctx(new, book("0.45"), prediction(new, 0.30, lo=0.27)), NOW)
    assert d.action == Action.NO_ADD


def test_case7_no_model_and_no_live_feed_produces_no_alerts():
    r = replay_file(FIXTURE, forecaster=None)
    s = r.summary()
    assert s["alerts"] == 0 and s["fills"] == []
    assert s["source_status"] == "SYNTHETIC_DEMO"
    assert set(s["actions"]) <= {"DATA_BLOCKED", "WATCH"}
    reasons = {x for d in r.sink.decisions for x in d.reasons}
    assert Reason.GAME_FEED_NOT_CONNECTED in reasons  # synthetic feed is not an authorized live feed
