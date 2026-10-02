"""Expected value of buying a $1 binary contract.

    expected_net_profit = quantity * p - executable_entry_cost - expected_fees

``executable_entry_cost`` comes from ``walk_asks`` and already includes the
spread and any slippage, so they are not subtracted again. Settlement-time
fees (if a venue charges them on winners only) are weighted by p.

Units are kept separate on purpose:
* dollars                     -> ev_point, ev_conservative
* cents per contract          -> ev_point_cents_per_contract
* percentage return on outlay -> ev_point_return_pct
* probability points          -> edge_probability_points (p minus all-in price, x100)
"""

from __future__ import annotations

from decimal import Decimal

from sports_edge.domain.records import EVBreakdown
from sports_edge.pricing.fees import FeeModel
from sports_edge.pricing.fills import FillEstimate

_Q = Decimal("0.0001")


def expected_value(
    fill: FillEstimate,
    probability: float,
    probability_low: float,
    fee_model: FeeModel,
) -> EVBreakdown:
    if not (0.0 <= probability_low <= probability <= 1.0):
        raise ValueError("need 0 <= probability_low <= probability <= 1")
    q = fill.filled
    if q == 0:
        raise ValueError("cannot value an empty fill")
    p = Decimal(str(probability))
    p_lo = Decimal(str(probability_low))
    win_fee = fee_model.settlement_fee(q, won=True)
    loss_fee = fee_model.settlement_fee(q, won=False)

    def ev_at(pr: Decimal) -> Decimal:
        exp_settle_fee = pr * win_fee + (1 - pr) * loss_fee
        return q * pr - fill.cost - fill.fees - exp_settle_fee

    ev_point = ev_at(p)
    ev_cons = ev_at(p_lo)
    outlay = fill.cost + fill.fees
    all_in_price = outlay / q
    return EVBreakdown(
        quantity=q,
        entry_cost=fill.cost,
        expected_fees=fill.fees + p * win_fee + (1 - p) * loss_fee,
        probability=probability,
        probability_low=probability_low,
        ev_point=ev_point.quantize(_Q),
        ev_conservative=ev_cons.quantize(_Q),
        ev_point_cents_per_contract=(ev_point / q * 100).quantize(_Q),
        ev_point_return_pct=(ev_point / outlay * 100).quantize(_Q) if outlay else Decimal(0),
        edge_probability_points=float((p - all_in_price) * 100),
    )
