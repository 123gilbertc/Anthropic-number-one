"""Kelly sizing for $1-payout prediction-market shares.

Conventions
-----------
* A Polymarket share costs ``price`` = q in (0, 1) and pays $1 if the outcome happens.
* ``fee`` = f is the taker fee charged **on winnings** (the (1 - q) profit of a winning share),
  so the net odds of a share are ``b = (1 - q) * (1 - f) / q`` and the decimal odds ``1 + b``.
* Full Kelly for net odds ``b`` and win probability ``p``: ``f* = (p * b - (1 - p)) / b``,
  clipped at 0 (never bet a negative-EV position).
* ``expected_value`` is profit per $1 staked: ``p * b - (1 - p)``. It is zero exactly at the
  breakeven probability ``q / (q + (1 - q) * (1 - f))`` (see :func:`breakeven_prob`).
* ``growth_rate`` is the expected log growth ``p * ln(1 + f b) + (1 - p) * ln(1 - f)``, which is
  maximised at full Kelly; the tests check that numerically.

:func:`size_positions` turns a list of :class:`~nfl_edge.strategy.edge.Opportunity` into
capped stakes: greedy by edge, honouring the per-position, per-game and weekly exposure caps
of :class:`~nfl_edge.config.StrategyConfig` and never staking more than the book depth
(``liquidity_usd``) that the opportunity's edge and effective price were computed against.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Iterable

from ..config import DEFAULT_STRATEGY, StrategyConfig

if TYPE_CHECKING:  # pragma: no cover - import only for type hints, no runtime dependency
    from .edge import Opportunity

__all__ = [
    "net_odds",
    "breakeven_prob",
    "kelly_fraction",
    "kelly_share",
    "fractional_kelly",
    "expected_value",
    "growth_rate",
    "exposure_key",
    "size_positions",
]


# ------------------------------------------------------------------ validation
def _check_prob(p: float, name: str = "p") -> float:
    p = float(p)
    if math.isnan(p) or not (0.0 <= p <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {p}")
    return p


def _check_price(price: float) -> float:
    price = float(price)
    if math.isnan(price) or not (0.0 < price < 1.0):
        raise ValueError(f"price must be in (0, 1), got {price}")
    return price


def _check_fee(fee: float) -> float:
    fee = float(fee)
    if math.isnan(fee) or not (0.0 <= fee < 1.0):
        raise ValueError(f"fee must be in [0, 1), got {fee}")
    return fee


# ------------------------------------------------------------------ share math
def net_odds(price: float, fee: float = 0.0) -> float:
    """Net odds ``b`` of a $1-payout share bought at ``price`` with ``fee`` on winnings.

    ``b = (1 - price) * (1 - fee) / price``: profit per $1 staked when the share wins.
    """
    q = _check_price(price)
    f = _check_fee(fee)
    return (1.0 - q) * (1.0 - f) / q


def breakeven_prob(price: float, fee: float = 0.0) -> float:
    """Win probability at which a share bought at ``price`` has zero expected value.

    ``breakeven = price / (price + (1 - price) * (1 - fee))`` = ``1 / (1 + b)``.
    With ``fee == 0`` this is simply ``price``.
    """
    q = _check_price(price)
    f = _check_fee(fee)
    return q / (q + (1.0 - q) * (1.0 - f))


def kelly_fraction(p: float, decimal_odds: float) -> float:
    """Full Kelly stake fraction for win probability ``p`` at ``decimal_odds``, clipped at 0.

    Textbook form with net odds ``b = decimal_odds - 1``: ``f* = (p * b - (1 - p)) / b``.
    """
    p = _check_prob(p)
    b = float(decimal_odds) - 1.0
    if math.isnan(b) or b <= 0.0:
        raise ValueError(f"decimal_odds must exceed 1.0, got {decimal_odds}")
    f_star = (p * b - (1.0 - p)) / b
    return max(0.0, f_star)


def kelly_share(p: float, price: float, fee: float = 0.0) -> float:
    """Full Kelly fraction for a $1-payout share bought at ``price`` with ``fee`` on winnings.

    Uses ``b = (1 - price) * (1 - fee) / price`` and ``f* = (p * b - (1 - p)) / b``, clipped at 0.
    With ``fee == 0`` this equals ``kelly_fraction(p, 1 / price)``.
    """
    return kelly_fraction(_check_prob(p), 1.0 + net_odds(price, fee))


def fractional_kelly(p: float, price: float, fraction: float, cap: float, fee: float = 0.0) -> float:
    """``min(cap, fraction * kelly_share(p, price, fee))``: scaled-down Kelly with a hard cap."""
    fraction = float(fraction)
    cap = float(cap)
    if math.isnan(fraction) or fraction < 0.0:
        raise ValueError(f"fraction must be >= 0, got {fraction}")
    if math.isnan(cap) or cap < 0.0:
        raise ValueError(f"cap must be >= 0, got {cap}")
    return min(cap, fraction * kelly_share(p, price, fee))


def expected_value(p: float, price: float, fee: float = 0.0) -> float:
    """Expected profit per $1 staked: ``p * (1 - price) * (1 - fee) / price - (1 - p)``.

    Zero at ``p == breakeven_prob(price, fee)``; positive above it.
    """
    p = _check_prob(p)
    return p * net_odds(price, fee) - (1.0 - p)


def growth_rate(p: float, price: float, f: float, fee: float = 0.0) -> float:
    """Expected log growth of bankroll per bet when staking fraction ``f``.

    ``g(f) = p * ln(1 + f * b) + (1 - p) * ln(1 - f)`` with ``b = net_odds(price, fee)``.
    ``f`` must be in ``[0, 1)``; the maximiser is ``kelly_share(p, price, fee)``.
    """
    p = _check_prob(p)
    b = net_odds(price, fee)
    f = float(f)
    if math.isnan(f) or not (0.0 <= f < 1.0):
        raise ValueError(f"stake fraction f must be in [0, 1), got {f}")
    return p * math.log1p(f * b) + (1.0 - p) * math.log1p(-f)


# ------------------------------------------------------------------ portfolio sizing
def _num(x: object, default: float) -> float:
    """``float(x)`` or ``default`` when ``x`` is None / NaN / unparsable."""
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def exposure_key(opp: "Opportunity") -> str:
    """Correlation key an opportunity is grouped under for ``cfg.max_game_exposure``.

    ``opp.exposure_key`` when :func:`~nfl_edge.strategy.edge.find_edges` set one (game id, else
    the market's event slug, else its team pair), else ``game:<game_id>`` when the game is known,
    else ``market:<market_id>`` so the two sides of one market are never double-counted.
    """
    key = getattr(opp, "exposure_key", None)
    if key:
        return str(key)
    if opp.game_id is not None:
        return f"game:{opp.game_id}"
    return f"market:{opp.market_id}"


def size_positions(
    opps: Iterable["Opportunity"],
    bankroll: float,
    cfg: StrategyConfig = DEFAULT_STRATEGY,
    max_liquidity_fraction: float = 1.0,
) -> list["Opportunity"]:
    """Assign capped stakes to opportunities, greedy by edge (largest first).

    For each opportunity the desired stake is ``min(cfg.max_stake_fraction,
    cfg.kelly_fraction * opp.kelly)`` (``opp.kelly`` is the full-Kelly share fraction at the
    effective price). It is then reduced so that

    * ``stake_usd`` never exceeds ``max_liquidity_fraction * opp.liquidity_usd`` -- the depth
      within the slippage buffer that the edge and ``effective_price`` were computed against;
      beyond it the fill price is unknown. (A NaN ``liquidity_usd`` means "unknown" and is not
      capped; ``find_edges`` always fills it.)
    * the sum of stakes sharing a correlation key (:func:`exposure_key`: the caller's game id,
      else the market's event slug / team pair, else the market itself) stays
      <= ``cfg.max_game_exposure``, and
    * the sum of all stakes stays <= ``cfg.max_weekly_exposure``.

    Returns **new** ``Opportunity`` objects (inputs are not mutated) sorted by edge descending,
    with ``stake_fraction`` / ``stake_usd`` filled in. Opportunities that receive no stake are
    kept with ``stake_usd == 0`` and a ``reason`` explaining which cap bound (``capped by
    liquidity`` / ``max_game_exposure`` / ``max_weekly_exposure``); filter on ``stake_usd > 0``
    for the executable list.
    """
    bankroll = float(bankroll)
    if math.isnan(bankroll) or bankroll <= 0.0:
        raise ValueError(f"bankroll must be positive, got {bankroll}")
    for name in ("max_stake_fraction", "max_game_exposure", "max_weekly_exposure", "kelly_fraction"):
        if getattr(cfg, name) < 0.0:
            raise ValueError(f"cfg.{name} must be >= 0")
    max_liquidity_fraction = float(max_liquidity_fraction)
    if math.isnan(max_liquidity_fraction) or max_liquidity_fraction < 0.0:
        raise ValueError(f"max_liquidity_fraction must be >= 0, got {max_liquidity_fraction}")

    ordered = sorted(
        opps,
        key=lambda o: (-_num(o.edge, -math.inf), -_num(o.liquidity_usd, 0.0), str(o.market_id), str(o.side)),
    )
    weekly_used = 0.0
    group_used: dict[str, float] = {}
    out: list["Opportunity"] = []
    for opp in ordered:
        key = exposure_key(opp)
        desired = min(cfg.max_stake_fraction, cfg.kelly_fraction * max(0.0, _num(opp.kelly, 0.0)))
        liquidity = _num(opp.liquidity_usd, math.nan)
        liquidity_usd_cap = math.inf if math.isnan(liquidity) else max(0.0, max_liquidity_fraction * liquidity)
        room_liquidity = liquidity_usd_cap / bankroll
        room_game = max(0.0, cfg.max_game_exposure - group_used.get(key, 0.0))
        room_week = max(0.0, cfg.max_weekly_exposure - weekly_used)
        rooms = (
            ("liquidity", room_liquidity),
            ("max_game_exposure", room_game),
            ("max_weekly_exposure", room_week),
        )
        stake = max(0.0, min(desired, room_liquidity, room_game, room_week))

        notes: list[str] = []
        if desired <= 0.0:
            notes.append("no stake: zero kelly")
        elif stake < desired - 1e-12:
            binding = min(rooms, key=lambda r: r[1])[0]
            if stake <= 0.0:
                notes.append(f"no stake: {binding} exhausted")
            elif binding == "liquidity":
                notes.append(f"capped by liquidity (${liquidity_usd_cap:,.2f} within slippage buffer)")
            else:
                notes.append(f"capped by {binding}")

        reason = opp.reason
        if notes:
            reason = f"{reason} | {'; '.join(notes)}" if reason else "; ".join(notes)
        out.append(
            replace(
                opp,
                stake_fraction=stake,
                stake_usd=round(stake * bankroll, 2),
                reason=reason,
            )
        )
        group_used[key] = group_used.get(key, 0.0) + stake
        weekly_used += stake
    return out
