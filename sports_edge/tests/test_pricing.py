from decimal import Decimal

import pytest

from sports_edge.domain.records import BookLevel, Position
from sports_edge.pricing.ev import expected_value
from sports_edge.pricing.fees import KalshiQuadraticFee, ZeroFee, round_to_tick
from sports_edge.pricing.fills import max_quantity_within_budget, walk_asks
from sports_edge.pricing.odds import (
    OddsError,
    american_to_decimal,
    american_to_implied,
    remove_margin,
)

D = Decimal


def asks(*levels):
    return tuple(BookLevel(price=D(p), quantity=q) for p, q in levels)


def test_american_conversions():
    assert american_to_decimal(150) == pytest.approx(2.5)
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_to_implied(-200) == pytest.approx(2 / 3)
    with pytest.raises(OddsError):
        american_to_decimal(50)


@pytest.mark.parametrize("method", ["proportional", "power"])
def test_remove_margin_sums_to_one(method):
    fair = remove_margin({"A": -150, "B": 130}, {"A", "B"}, method=method)
    assert sum(fair.values()) == pytest.approx(1.0)
    assert fair["A"] > fair["B"]


def test_remove_margin_rejects_missing_outcome():
    # A regulation (3-way) market quoted with only two outcomes is incomplete.
    with pytest.raises(OddsError):
        remove_margin({"A": -150, "B": 130}, {"A", "B", "Draw"})


def test_kalshi_fee_rounds_up_to_cent():
    fee = KalshiQuadraticFee(rate=D("0.07"))
    # 0.07 * 10 * 0.45 * 0.55 = 0.17325 -> 0.18
    assert fee.entry_fee(10, D("0.45")) == D("0.18")
    assert fee.entry_fee(0, D("0.45")) == D("0")


def test_walk_book_partial_fill_and_cost():
    est = walk_asks(asks(("0.45", 5), ("0.47", 3)), 10, ZeroFee())
    assert est.filled == 8 and est.partial
    assert est.cost == D("0.45") * 5 + D("0.47") * 3
    assert est.worst_price == D("0.47")


def test_walk_book_respects_limit_and_haircut():
    est = walk_asks(asks(("0.45", 10), ("0.50", 10)), 10, ZeroFee(), limit_price=D("0.46"),
                    depth_haircut=D("0.5"))
    assert est.filled == 5


def test_max_quantity_within_budget_includes_fees():
    fee = KalshiQuadraticFee()
    q = max_quantity_within_budget(asks(("0.45", 1000)), D("10"), fee)
    est = walk_asks(asks(("0.45", 1000)), q, fee)
    assert est.cost + est.fees <= D("10")
    est2 = walk_asks(asks(("0.45", 1000)), q + 1, fee)
    assert est2.cost + est2.fees > D("10")


def test_ev_formula_and_units():
    est = walk_asks(asks(("0.45", 100)), 10, ZeroFee())
    ev = expected_value(est, 0.57, 0.50, ZeroFee())
    # 10 * 0.57 - 4.50 = 1.20 dollars
    assert ev.ev_point == D("1.2000")
    assert ev.ev_conservative == D("0.5000")
    assert ev.ev_point_cents_per_contract == D("12.0000")
    assert ev.edge_probability_points == pytest.approx(12.0)
    assert ev.ev_point_return_pct == pytest.approx(D("26.6667"), abs=D("0.001"))


def test_ev_does_not_double_count_spread():
    # Walking two levels: cost already includes the worse level; EV must equal q*p - cost.
    est = walk_asks(asks(("0.40", 5), ("0.50", 5)), 10, ZeroFee())
    ev = expected_value(est, 0.60, 0.60, ZeroFee())
    assert ev.ev_point == D("6.00") - D("4.50")


def test_quantity_weighted_average_entry():
    # 10 @ 0.60 then 30 @ 0.40. Naive average of quotes = 0.50; correct = 18/40 = 0.45
    pos = Position(game_id="g", contract_id="c", selection_team="A", contracts=40,
                   total_cost=D("6.00") + D("12.00"), total_fees=D("0.40"))
    assert pos.average_entry == D("0.45")
    assert pos.average_entry_all_in == D("0.46")


def test_round_to_tick():
    assert round_to_tick(D("0.453"), D("0.01"), "up") == D("0.46")
    assert round_to_tick(D("0.453"), D("0.01"), "down") == D("0.45")
