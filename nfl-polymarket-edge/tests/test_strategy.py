"""Tests for nfl_edge.strategy (kelly + edge). Offline; markets are lightweight stand-ins."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import pytest

from nfl_edge.config import StrategyConfig
from nfl_edge.strategy import edge as edge_mod
from nfl_edge.strategy import kelly
from nfl_edge.strategy.edge import (
    OPPORTUNITY_COLUMNS,
    Opportunity,
    consistency_checks,
    find_arbitrage,
    find_edges,
    futures_category,
    market_category,
    opportunities_table,
)
from nfl_edge.teams import CURRENT_TEAMS, DIVISIONS, FULL_NAMES


# ------------------------------------------------------------------ stand-ins
@dataclass
class FakeBook:
    """Mirror of the CONTRACT.md OrderBook surface used by the strategy layer."""

    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size) best-first
    asks: list[tuple[float, float]] = field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def depth_usd(self, side: str, max_price_impact: float) -> float:
        levels = self.asks if side == "ask" else self.bids
        if not levels:
            return 0.0
        touch = levels[0][0]
        return sum(p * s for p, s in levels if abs(p - touch) <= max_price_impact + 1e-12)


@dataclass
class FakeMarket:
    market_id: str
    question: str
    kind: str
    outcomes: list[str] = field(default_factory=lambda: ["Yes", "No"])
    token_ids: list[str] = field(default_factory=list)
    prices: list[float] = field(default_factory=lambda: [0.5, 0.5])
    liquidity: float = 10_000.0
    home: str | None = None
    away: str | None = None
    team: str | None = None
    yes_index: int = 0
    book: FakeBook | None = None
    raw: dict = field(default_factory=dict)
    condition_id: str = "0xcond"
    event_slug: str | None = None

    def __post_init__(self) -> None:
        if not self.token_ids:
            self.token_ids = [f"{self.market_id}-yes", f"{self.market_id}-no"]


def book(bid: float, ask: float, size: float = 5_000.0) -> FakeBook:
    """Two-level book with `size` shares at the touch and the same one cent further out."""
    return FakeBook(
        bids=[(bid, size), (round(bid - 0.01, 4), size)],
        asks=[(ask, size), (round(ask + 0.01, 4), size)],
    )


def make_opp(market_id: str, edge: float, kelly_f: float, game_id: str | None = None, **kw) -> Opportunity:
    base = dict(
        market_id=market_id,
        question=f"Q {market_id}",
        kind="moneyline",
        side="YES",
        token_id=f"{market_id}-yes",
        fair_prob=0.55,
        price=0.50,
        effective_price=0.51,
        edge=edge,
        ev=0.05,
        kelly=kelly_f,
        stake_fraction=0.0,
        stake_usd=0.0,
        liquidity_usd=5_000.0,
        game_id=game_id,
        teams=["KC", "BUF"],
        reason="book",
        breakeven=0.51,
        fee=0.0,
        event_slug=None,
    )
    base.update(kw)
    return Opportunity(**base)


# ------------------------------------------------------------------ kelly.py
def test_kelly_fraction_matches_textbook():
    for p, dec in [(0.55, 2.0), (0.6, 1.8), (0.3, 4.0), (0.9, 1.2)]:
        b = dec - 1.0
        expected = max(0.0, (p * b - (1 - p)) / b)
        assert math.isclose(kelly.kelly_fraction(p, dec), expected, rel_tol=1e-12)
    assert kelly.kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    assert kelly.kelly_fraction(0.40, 2.0) == 0.0  # negative EV clipped
    assert kelly.kelly_fraction(1.0, 1.5) == 1.0
    with pytest.raises(ValueError):
        kelly.kelly_fraction(0.5, 1.0)
    with pytest.raises(ValueError):
        kelly.kelly_fraction(1.2, 2.0)


def test_kelly_share_equals_kelly_fraction_at_decimal_price_odds():
    for p, price in [(0.55, 0.5), (0.7, 0.6), (0.2, 0.1), (0.65, 0.7)]:
        assert math.isclose(kelly.kelly_share(p, price), kelly.kelly_fraction(p, 1 / price), rel_tol=1e-12)
    # fee on winnings lowers net odds: b = (1-q)(1-f)/q
    p, q, f = 0.55, 0.5, 0.02
    b = (1 - q) * (1 - f) / q
    assert math.isclose(kelly.kelly_share(p, q, f), (p * b - (1 - p)) / b, rel_tol=1e-12)
    assert kelly.kelly_share(p, q, f) < kelly.kelly_share(p, q, 0.0)
    with pytest.raises(ValueError):
        kelly.kelly_share(0.5, 1.0)
    with pytest.raises(ValueError):
        kelly.kelly_share(0.5, 0.5, fee=1.0)


def test_fractional_kelly_scales_and_caps():
    full = kelly.kelly_share(0.7, 0.5)  # = 0.4
    assert full == pytest.approx(0.4)
    assert kelly.fractional_kelly(0.7, 0.5, 0.25, cap=1.0) == pytest.approx(0.1)
    assert kelly.fractional_kelly(0.7, 0.5, 0.5, cap=0.03) == 0.03  # cap binds
    assert kelly.fractional_kelly(0.4, 0.5, 0.5, cap=0.03) == 0.0  # no edge
    with pytest.raises(ValueError):
        kelly.fractional_kelly(0.7, 0.5, -0.1, cap=0.03)


def test_expected_value_zero_at_breakeven_and_reduces_without_fee():
    for q, f in [(0.5, 0.0), (0.3, 0.02), (0.8, 0.05), (0.05, 0.1)]:
        be = kelly.breakeven_prob(q, f)
        assert abs(kelly.expected_value(be, q, f)) < 1e-12
        assert kelly.expected_value(min(1.0, be + 0.05), q, f) > 0
        assert kelly.expected_value(max(0.0, be - 0.05), q, f) < 0
    assert kelly.breakeven_prob(0.42, 0.0) == pytest.approx(0.42)
    assert kelly.expected_value(0.55, 0.5) == pytest.approx(0.10)  # p*b - (1-p) with b = 1


def test_growth_rate_is_maximised_at_full_kelly():
    for p, q, f in [(0.6, 0.5, 0.0), (0.55, 0.4, 0.02), (0.3, 0.2, 0.0)]:
        f_star = kelly.kelly_share(p, q, f)
        grid = np.linspace(0.0, 0.95, 1901)
        g = np.array([kelly.growth_rate(p, q, x, f) for x in grid])
        assert abs(grid[int(np.argmax(g))] - f_star) <= 0.0006
        assert kelly.growth_rate(p, q, f_star, f) >= kelly.growth_rate(p, q, f_star * 0.5, f)
        assert kelly.growth_rate(p, q, f_star, f) >= kelly.growth_rate(p, q, min(0.95, f_star * 1.5), f)
    assert kelly.growth_rate(0.5, 0.5, 0.0) == 0.0
    with pytest.raises(ValueError):
        kelly.growth_rate(0.5, 0.5, 1.0)


def test_size_positions_respects_every_cap():
    cfg = StrategyConfig(
        kelly_fraction=0.5, max_stake_fraction=0.03, max_game_exposure=0.05, max_weekly_exposure=0.10
    )
    opps = [
        make_opp("m1", edge=0.10, kelly_f=0.20, game_id="g1"),  # desired 0.10 -> per-position cap 0.03
        make_opp("m2", edge=0.08, kelly_f=0.06, game_id="g1"),  # desired 0.03, game room 0.02
        make_opp("m3", edge=0.06, kelly_f=0.04, game_id="g2"),  # desired 0.02
        make_opp("m4", edge=0.04, kelly_f=0.05, game_id="g3"),  # desired 0.025, weekly room 0.03
        make_opp("m5", edge=0.02, kelly_f=0.01, game_id="g4"),  # desired 0.005, weekly room 0.005
        make_opp("m6", edge=0.01, kelly_f=0.02, game_id="g5"),  # weekly exhausted -> 0
        make_opp("m7", edge=0.005, kelly_f=0.0, game_id="g6"),  # zero kelly -> 0
    ]
    sized = kelly.size_positions(list(reversed(opps)), bankroll=10_000.0, cfg=cfg)
    by_id = {o.market_id: o for o in sized}
    assert [o.market_id for o in sized] == ["m1", "m2", "m3", "m4", "m5", "m6", "m7"]  # edge desc
    assert by_id["m1"].stake_fraction == pytest.approx(0.03)
    assert by_id["m2"].stake_fraction == pytest.approx(0.02)
    assert "max_game_exposure" in by_id["m2"].reason
    assert by_id["m3"].stake_fraction == pytest.approx(0.02)
    assert by_id["m4"].stake_fraction == pytest.approx(0.025)
    assert by_id["m5"].stake_fraction == pytest.approx(0.005)
    assert by_id["m6"].stake_fraction == 0.0 and "max_weekly_exposure" in by_id["m6"].reason
    assert by_id["m7"].stake_fraction == 0.0 and "zero kelly" in by_id["m7"].reason
    assert sum(o.stake_fraction for o in sized) == pytest.approx(cfg.max_weekly_exposure)
    assert sum(o.stake_fraction for o in sized if o.game_id == "g1") <= cfg.max_game_exposure + 1e-12
    assert max(o.stake_fraction for o in sized) <= cfg.max_stake_fraction + 1e-12
    assert by_id["m1"].stake_usd == pytest.approx(300.0)
    # inputs are not mutated
    assert all(o.stake_usd == 0.0 for o in opps)
    # positions without a game share a per-market key, so one side never exceeds the game cap
    with pytest.raises(ValueError):
        kelly.size_positions(opps, bankroll=0.0, cfg=cfg)


# ------------------------------------------------------------------ edge.py
CFG = StrategyConfig(
    kelly_fraction=0.25,
    max_stake_fraction=0.03,
    max_weekly_exposure=0.15,
    max_game_exposure=0.05,
    min_edge=0.03,
    min_liquidity_usd=500.0,
    fee_rate=0.0,
    slippage_buffer=0.01,
    min_prob=0.05,
    max_prob=0.95,
)


def test_edge_reduces_to_fair_minus_price_without_costs():
    cfg = replace(CFG, slippage_buffer=0.0, fee_rate=0.0, min_edge=0.0)
    m = FakeMarket("m1", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.55, 0.57))
    opps = find_edges([m], {"m1": 0.62}, cfg=cfg)
    assert len(opps) == 1
    o = opps[0]
    assert o.side == "YES"
    assert o.effective_price == pytest.approx(0.57)
    assert o.breakeven == pytest.approx(0.57)
    assert o.edge == pytest.approx(0.62 - 0.57)


def test_breakeven_formula_with_fee_and_slippage():
    cfg = replace(CFG, slippage_buffer=0.01, fee_rate=0.02, min_edge=0.0)
    m = FakeMarket("m1", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.55, 0.57))
    (o,) = find_edges([m], {"m1": 0.70}, cfg=cfg)
    eff = 0.58
    be = eff / (eff + (1 - eff) * (1 - 0.02))
    assert o.effective_price == pytest.approx(eff)
    assert o.breakeven == pytest.approx(be)
    assert o.edge == pytest.approx(0.70 - be)
    assert o.fee == 0.02
    assert o.kelly == pytest.approx(kelly.kelly_share(0.70, eff, 0.02))
    assert o.ev == pytest.approx(kelly.expected_value(0.70, eff, 0.02))


def test_find_edges_picks_yes_and_no_with_correct_effective_prices():
    markets = [
        # YES is cheap relative to the model: buy YES at the ask 0.52 -> effective 0.53
        FakeMarket("kc", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.50, 0.52)),
        # YES is rich: buy NO at 1 - best_bid = 1 - 0.70 = 0.30 -> effective 0.31
        FakeMarket("phi", "Eagles vs Cowboys", "moneyline", home="PHI", away="DAL", book=book(0.70, 0.72)),
        # no edge either way
        FakeMarket("det", "Lions vs Bears", "moneyline", home="DET", away="CHI", book=book(0.60, 0.62)),
    ]
    fair = {"kc": 0.60, "phi": 0.62, "det": 0.61}
    opps = find_edges(markets, fair, cfg=CFG, bankroll=1_000.0, game_ids={"kc": "g_kc", "phi": "g_phi"})
    # both edges are 0.07; the tie is broken by liquidity: KC's YES ask depth ($5,250) beats
    # PHI's NO depth measured in NO dollars (0.30 * 5000 + 0.31 * 5000 = $3,050)
    assert [o.market_id for o in opps] == ["kc", "phi"]
    no = opps[1]
    assert no.side == "NO"
    assert no.token_id == "phi-no"
    assert no.price == pytest.approx(0.30)
    assert no.effective_price == pytest.approx(0.31)
    assert no.fair_prob == pytest.approx(0.38)
    assert no.edge == pytest.approx(0.38 - 0.31)
    assert no.game_id == "g_phi"
    assert no.exposure_key == "game:g_phi"
    assert no.teams == ["PHI", "DAL"]
    assert no.liquidity_usd == pytest.approx(0.30 * 5000 + 0.31 * 5000)
    yes = opps[0]
    assert yes.side == "YES"
    assert yes.token_id == "kc-yes"
    assert yes.price == pytest.approx(0.52)
    assert yes.effective_price == pytest.approx(0.53)
    assert yes.edge == pytest.approx(0.60 - 0.53)
    assert yes.liquidity_usd == pytest.approx(0.52 * 5000 + 0.53 * 5000)
    for o in opps:
        assert o.stake_fraction == pytest.approx(min(CFG.max_stake_fraction, CFG.kelly_fraction * o.kelly))
        assert o.stake_usd == pytest.approx(round(o.stake_fraction * 1000.0, 2))
        assert o.reason.startswith("book")
        assert 0 < o.stake_usd <= 30.0


def test_find_edges_filters_thin_books_out_of_range_probs_and_missing_fair():
    thin = FakeMarket("thin", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.50, 0.52, size=100))
    longshot = FakeMarket("long", "Jets win Super Bowl?", "futures", team="NYJ", book=book(0.01, 0.02))
    heavy = FakeMarket("heavy", "Chiefs vs Panthers", "moneyline", home="KC", away="CAR", book=book(0.90, 0.91))
    nofair = FakeMarket("nofair", "Lions vs Bears", "moneyline", home="DET", away="CHI", book=book(0.40, 0.42))
    nanfair = FakeMarket("nanfair", "Lions vs Bears", "moneyline", home="DET", away="CHI", book=book(0.40, 0.42))
    fair = {"thin": 0.65, "long": 0.04, "heavy": 0.97, "nanfair": float("nan")}
    assert find_edges([thin, longshot, heavy, nofair, nanfair], fair, cfg=CFG) == []
    # the same books with enough size / in-range probabilities do produce edges
    thin.book = book(0.50, 0.52, size=5000)
    assert len(find_edges([thin], fair, cfg=CFG)) == 1
    with pytest.raises(ValueError):
        find_edges([thin], {"thin": 1.5}, cfg=CFG)


def test_find_edges_without_book_uses_price_and_market_liquidity():
    m = FakeMarket("nb", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", prices=[0.40, 0.60], liquidity=2_000.0)
    (o,) = find_edges([m], {"nb": 0.50}, cfg=CFG)
    assert o.side == "YES" and o.price == pytest.approx(0.40)
    assert o.liquidity_usd == 2_000.0
    assert "no book" in o.reason
    m.liquidity = 100.0
    assert find_edges([m], {"nb": 0.50}, cfg=CFG) == []


def test_find_edges_yes_index_and_one_sided_book():
    # YES token is index 1 and the book quotes only asks: NO cannot be priced, YES can.
    m = FakeMarket(
        "yi", "Bills vs Chiefs", "moneyline", outcomes=["Chiefs", "Bills"], token_ids=["t-kc", "t-buf"],
        home="BUF", away="KC", yes_index=1, book=FakeBook(bids=[], asks=[(0.45, 3000)]),
    )
    (o,) = find_edges([m], {"yi": 0.55}, cfg=CFG)
    assert o.side == "YES" and o.token_id == "t-buf" and o.price == pytest.approx(0.45)
    # a NO edge on the same market maps to the other token
    m.book = FakeBook(bids=[(0.60, 3000)], asks=[(0.62, 3000)])
    (o,) = find_edges([m], {"yi": 0.50}, cfg=CFG)
    assert o.side == "NO" and o.token_id == "t-kc" and o.price == pytest.approx(0.40)


def test_find_edges_applies_game_and_weekly_caps():
    cfg = replace(CFG, max_game_exposure=0.04, max_weekly_exposure=0.05)
    ml = FakeMarket("ml", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.40, 0.42))
    sp = FakeMarket("sp", "Chiefs -3.5", "spread", home="KC", away="BUF", book=book(0.40, 0.42))
    other = FakeMarket("ot", "Eagles vs Cowboys", "moneyline", home="PHI", away="DAL", book=book(0.40, 0.42))
    fair = {"ml": 0.60, "sp": 0.58, "ot": 0.57}
    opps = find_edges([ml, sp, other], fair, cfg=cfg, game_ids={"ml": "g1", "sp": "g1"})
    by_id = {o.market_id: o for o in opps}
    assert by_id["ml"].stake_fraction == pytest.approx(0.03)
    assert by_id["sp"].stake_fraction == pytest.approx(0.01)  # game cap 0.04
    assert by_id["ot"].stake_fraction == pytest.approx(0.01)  # weekly cap 0.05
    assert sum(o.stake_fraction for o in opps) == pytest.approx(0.05)


def test_find_arbitrage_intra_market():
    crossed = FakeMarket("x", "Chiefs vs Bills", "moneyline", home="KC", away="BUF",
                         book=FakeBook(bids=[(0.60, 100)], asks=[(0.38, 100)]))
    normal = FakeMarket("n", "Eagles vs Cowboys", "moneyline", home="PHI", away="DAL", book=book(0.55, 0.57))
    arbs = find_arbitrage([crossed, normal])
    assert len(arbs) == 1
    a = arbs[0]
    assert a["type"] == "intra_market" and a["market_id"] == "x" and a["source"] == "yes_book"
    assert a["yes_ask"] == pytest.approx(0.38) and a["no_ask"] == pytest.approx(0.40)
    assert a["cost"] == pytest.approx(0.78)
    assert a["profit_per_usd"] == pytest.approx((1 - 0.78) / 0.78)
    # two token books: NO ask comes from the NO token's own book
    books = {"n-yes": FakeBook(asks=[(0.50, 100)]), "n-no": FakeBook(asks=[(0.45, 100)])}
    (b,) = find_arbitrage([normal], books=books)
    assert b["source"] == "two_books" and b["cost"] == pytest.approx(0.95)
    # a fee on winnings can erase a thin arbitrage; epsilon raises the bar
    assert find_arbitrage([normal], books=books, fee=0.2) == []
    assert find_arbitrage([crossed, normal], epsilon=0.25) == []


def _division_event(prices: dict[str, tuple[float, float]], slug: str = "afc-east-2026") -> list[FakeMarket]:
    return [
        FakeMarket(f"div-{t}", f"Will the {t} win the AFC East?", "futures", team=t, event_slug=slug,
                   book=book(bid, ask, size=1000))
        for t, (bid, ask) in prices.items()
    ]


def test_find_arbitrage_cross_market_event():
    cheap = _division_event({"BUF": (0.40, 0.42), "MIA": (0.20, 0.22), "NYJ": (0.10, 0.12), "NE": (0.14, 0.16)})
    arbs = find_arbitrage(cheap)
    assert [a["type"] for a in arbs] == ["event_buy_all"]
    a = arbs[0]
    assert a["sum_asks"] == pytest.approx(0.92) and a["complete"] is True
    assert a["profit_per_usd"] == pytest.approx((1 - 0.92) / 0.92)
    assert sorted(a["teams"]) == ["BUF", "MIA", "NE", "NYJ"]

    rich = _division_event({"BUF": (0.50, 0.52), "MIA": (0.25, 0.27), "NYJ": (0.15, 0.17), "NE": (0.20, 0.22)})
    (s,) = find_arbitrage(rich)
    assert s["type"] == "event_sell_all"
    assert s["sum_bids"] == pytest.approx(1.10) and s["cost"] == pytest.approx(4 - 1.10)
    assert s["profit_per_usd"] == pytest.approx((3 - (4 - 1.10)) / (4 - 1.10))

    # incomplete event (one team missing): buy-all is NOT guaranteed, sell-all still is
    partial = _division_event({"BUF": (0.40, 0.42), "MIA": (0.20, 0.22), "NYJ": (0.10, 0.12)})
    assert find_arbitrage(partial) == []
    partial_rich = _division_event({"BUF": (0.60, 0.62), "MIA": (0.30, 0.32), "NYJ": (0.20, 0.22)})
    (s2,) = find_arbitrage(partial_rich)
    assert s2["type"] == "event_sell_all" and s2["complete"] is False

    # fairly priced event -> nothing
    fair = _division_event({"BUF": (0.44, 0.46), "MIA": (0.24, 0.26), "NYJ": (0.09, 0.11), "NE": (0.19, 0.21)})
    assert find_arbitrage(fair) == []
    # win-total style event (same team repeated) is never treated as mutually exclusive
    totals = [
        FakeMarket(f"wt{n}", f"Chiefs win {n}+ games?", "futures", team="KC", event_slug="kc-wins",
                   book=book(0.10, 0.12, size=1000)) for n in (9, 10, 11, 12)
    ]
    assert find_arbitrage(totals) == []
    # one win-total market per team in a single event: distinct teams but NOT mutually exclusive
    cross_totals = [
        FakeMarket(f"wt-{t}", f"Will the {t} win 10+ games?", "futures", team=t, event_slug="win-totals-2026",
                   book=book(0.60, 0.62, size=1000)) for t in ("KC", "PHI", "BUF")
    ]
    assert find_arbitrage(cross_totals) == []
    # "make the playoffs" per team: 14 teams qualify, so sum of bids > 1 is not an arbitrage either
    playoffs = [
        FakeMarket(f"po-{t}", f"Will the {t} make the playoffs?", "futures", team=t, event_slug="playoffs-2026",
                   book=book(0.80, 0.82, size=1000)) for t in ("BUF", "MIA", "NYJ", "NE")
    ]
    assert find_arbitrage(playoffs) == []


def test_futures_category():
    assert futures_category("Will the Chiefs win Super Bowl LXI?") == "super_bowl"
    assert futures_category("Will the Chiefs win the AFC?") == "conference"
    assert futures_category("Will the Chiefs win the AFC Championship?") == "conference"
    assert futures_category("Will the Chiefs win the AFC West?") == "division"
    assert futures_category("Will the Chiefs make the playoffs?") == "playoffs"
    assert futures_category("Will the Chiefs clinch the #1 seed in the AFC playoffs?") == "top_seed"
    assert futures_category("Will the Chiefs be the AFC's No. 1 seed?") == "top_seed"
    assert futures_category("Will the Chiefs win 12+ games?") is None
    assert edge_mod.EXCLUSIVE_FUTURES == {"super_bowl", "conference", "division", "top_seed"}


def _futures(team: str, cat: str, mid: float, slug: str | None = None) -> FakeMarket:
    q = {
        "super_bowl": f"Will the {team} win the Super Bowl?",
        "conference": f"Will the {team} win the AFC?",
        "playoffs": f"Will the {team} make the playoffs?",
        "division": f"Will the {team} win the AFC West?",
    }[cat]
    return FakeMarket(f"{cat}-{team}", q, "futures", team=team, event_slug=slug or f"{cat}-2026",
                      book=book(round(mid - 0.01, 4), round(mid + 0.01, 4), size=1000))


def test_consistency_checks_flags_ordering_and_division_sums():
    good = [_futures("KC", "super_bowl", 0.15), _futures("KC", "conference", 0.30), _futures("KC", "playoffs", 0.80)]
    assert consistency_checks(good, None) == []
    bad = [_futures("DEN", "super_bowl", 0.20), _futures("DEN", "conference", 0.15), _futures("DEN", "playoffs", 0.10)]
    flags = consistency_checks(bad, None)
    assert [(f["check"], f["lower"], f["upper"]) for f in flags] == [
        ("ordering", "super_bowl", "conference"),
        ("ordering", "conference", "playoffs"),
    ]
    assert flags[0]["team"] == "DEN" and flags[0]["gap"] == pytest.approx(0.05)
    # division sums
    ok_div = [_futures(t, "division", p, slug="afc-west") for t, p in [("KC", 0.45), ("DEN", 0.25), ("LAC", 0.20), ("LV", 0.10)]]
    assert consistency_checks(ok_div, None) == []
    hot_div = [_futures(t, "division", p, slug="afc-west") for t, p in [("KC", 0.55), ("DEN", 0.35), ("LAC", 0.25), ("LV", 0.10)]]
    (f,) = consistency_checks(hot_div, None)
    assert f["check"] == "division_sum" and f["sum_mids"] == pytest.approx(1.25)
    assert f["division"] == "AFC West"
    # an incomplete division group is not judged
    assert consistency_checks(hot_div[:3], None) == []


def test_consistency_checks_with_sim():
    markets = [_futures("KC", "super_bowl", 0.15), _futures("KC", "conference", 0.30), _futures("KC", "playoffs", 0.80)]
    sim = pd.DataFrame(
        {
            "team": ["KC", "DEN", "LAC", "LV"],
            "p_super_bowl": [0.14, 0.05, 0.03, 0.01],
            "p_conference": [0.28, 0.10, 0.06, 0.02],
            "p_playoffs": [0.78, 0.50, 0.40, 0.20],
            "p_division": [0.50, 0.25, 0.18, 0.07],
        }
    )
    assert consistency_checks(markets, sim) == []
    # model far from the market -> divergence flag; broken sim ordering -> sim_ordering flag
    sim2 = sim.copy()
    sim2.loc[sim2.team == "KC", "p_playoffs"] = 0.20  # below p_conference/p_division and 0.6 from market
    sim2.loc[sim2.team == "LV", "p_division"] = 0.30  # above LV p_playoffs; division now sums to 1.23
    flags = consistency_checks(markets, sim2)
    kinds = sorted(f["check"] for f in flags)
    assert kinds == ["divergence", "sim_division_sum", "sim_ordering", "sim_ordering", "sim_ordering"]
    orderings = sorted((f["team"], f["lower"], f["upper"]) for f in flags if f["check"] == "sim_ordering")
    assert orderings == [("KC", "conference", "playoffs"), ("KC", "division", "playoffs"), ("LV", "division", "playoffs")]
    div = next(f for f in flags if f["check"] == "divergence")
    assert div["team"] == "KC" and div["category"] == "playoffs" and div["gap"] == pytest.approx(0.20 - 0.80)
    dsum = next(f for f in flags if f["check"] == "sim_division_sum")
    assert dsum["division"] == "AFC West" and dsum["sum_p"] == pytest.approx(1.23)
    # sim indexed by team code instead of a column also works
    assert consistency_checks(markets, sim.set_index("team")) == []


def test_opportunities_table_columns_and_order():
    assert list(opportunities_table([]).columns) == OPPORTUNITY_COLUMNS
    opps = [make_opp("a", edge=0.04, kelly_f=0.1, game_id="g1"), make_opp("b", edge=0.09, kelly_f=0.2, side="NO")]
    df = opportunities_table(opps)
    assert list(df.columns) == OPPORTUNITY_COLUMNS
    assert df["market_id"].tolist() == ["b", "a"]
    assert df.loc[0, "teams"] == "KC/BUF"
    assert df.loc[1, "game_id"] == "g1"
    for col in ("market_id", "question", "kind", "side", "token_id", "fair_prob", "price", "effective_price",
                "edge", "ev", "kelly", "stake_fraction", "stake_usd", "liquidity_usd", "game_id", "teams", "reason"):
        assert col in df.columns


def test_market_mid_and_effective_price_helpers():
    m = FakeMarket("m", "Q", "moneyline", book=book(0.40, 0.44))
    assert edge_mod.market_mid(m) == pytest.approx(0.42)
    m.book = FakeBook(bids=[(0.40, 10)], asks=[])
    assert edge_mod.market_mid(m) == pytest.approx(0.40)
    m.book = None
    m.prices = [0.30, 0.70]
    assert edge_mod.market_mid(m) == pytest.approx(0.30)
    assert edge_mod.effective_price(0.995, CFG) == pytest.approx(0.999)
    assert edge_mod.effective_price(0.50, replace(CFG, slippage_buffer=0.02)) == pytest.approx(0.52)


# ------------------------------------------------------------------ review regressions
def _game_event(slug: str = "nfl-kc-mia-2026-09-27") -> list[FakeMarket]:
    """Moneyline + spread + total of ONE Polymarket event (shaped like the real fixture), no game_ids."""
    return [
        FakeMarket("ml", "Chiefs vs. Dolphins", "moneyline", home="MIA", away="KC", event_slug=slug, book=book(0.40, 0.41)),
        FakeMarket("sp", "Spread: Chiefs (-10.5)", "spread", home="MIA", away="KC", event_slug=slug, book=book(0.40, 0.41)),
        FakeMarket("tot", "Chiefs vs. Dolphins: O/U 45.5", "total", home="MIA", away="KC", event_slug=slug, book=book(0.40, 0.41)),
    ]


def test_same_event_markets_share_game_cap_without_game_ids():
    """CONTRACT signature (no game_ids): ML + spread + total of one event are jointly capped."""
    markets = _game_event()
    fair = {"ml": 0.60, "sp": 0.60, "tot": 0.60}
    opps = find_edges(markets, fair, cfg=CFG, bankroll=1_000.0)
    assert len(opps) == 3
    assert {o.exposure_key for o in opps} == {"event:nfl-kc-mia-2026-09-27"}
    assert all(o.game_id is None for o in opps)  # no nflverse id was supplied, none is invented
    assert sum(o.stake_fraction for o in opps) == pytest.approx(CFG.max_game_exposure)
    assert sorted(o.stake_fraction for o in opps) == pytest.approx([0.0, 0.02, 0.03])
    assert sum("max_game_exposure" in o.reason for o in opps) == 2
    # a prop on the same event (kind "other" but home/away set) joins the group ...
    prop = FakeMarket("ot", "Will the game go to overtime?", "other", home="MIA", away="KC",
                      event_slug="nfl-kc-mia-2026-09-27", book=book(0.40, 0.41))
    opps = find_edges(markets + [prop], {**fair, "ot": 0.60}, cfg=CFG, bankroll=1_000.0)
    assert len(opps) == 4 and sum(o.stake_fraction for o in opps) == pytest.approx(CFG.max_game_exposure)
    # ... while another event is capped on its own
    other = FakeMarket("o2", "Eagles vs. Bears", "moneyline", home="CHI", away="PHI",
                       event_slug="nfl-phi-chi-2026-09-28", book=book(0.40, 0.41))
    opps = find_edges(markets + [other], {**fair, "o2": 0.60}, cfg=CFG, bankroll=1_000.0)
    by_id = {o.market_id: o for o in opps}
    assert by_id["o2"].stake_fraction == pytest.approx(0.03)
    assert by_id["o2"].exposure_key == "event:nfl-phi-chi-2026-09-28"
    assert sum(o.stake_fraction for o in opps if o.market_id != "o2") == pytest.approx(CFG.max_game_exposure)


def test_exposure_key_fallbacks_and_game_id_inheritance():
    # no event slug: the sorted home|away pair groups the game's markets
    a = FakeMarket("a", "Chiefs vs Bills", "moneyline", home="KC", away="BUF", book=book(0.40, 0.41))
    b = FakeMarket("b", "Bills +3.5", "spread", home="KC", away="BUF", book=book(0.40, 0.41))
    opps = find_edges([a, b], {"a": 0.60, "b": 0.60}, cfg=CFG)
    assert {o.exposure_key for o in opps} == {"teams:BUF|KC"}
    assert sum(o.stake_fraction for o in opps) == pytest.approx(CFG.max_game_exposure)
    # futures on different teams are independent positions (per-market key)
    f1 = FakeMarket("f1", "Will the Chiefs win Super Bowl LXI?", "futures", team="KC", event_slug="sb", book=book(0.40, 0.41))
    f2 = FakeMarket("f2", "Will the Bills win Super Bowl LXI?", "futures", team="BUF", event_slug="sb", book=book(0.40, 0.41))
    opps = find_edges([f1, f2], {"f1": 0.60, "f2": 0.60}, cfg=CFG)
    assert {o.exposure_key for o in opps} == {"market:f1", "market:f2"}
    assert all(o.stake_fraction == pytest.approx(0.03) for o in opps)
    # a market the caller mapped lends its game_id to unmapped markets of the same event
    opps = find_edges(_game_event(), {"ml": 0.60, "sp": 0.60, "tot": 0.60}, cfg=CFG, game_ids={"ml": "2026_03_KC_MIA"})
    assert {o.game_id for o in opps} == {"2026_03_KC_MIA"}
    assert {o.exposure_key for o in opps} == {"game:2026_03_KC_MIA"}
    assert sum(o.stake_fraction for o in opps) == pytest.approx(CFG.max_game_exposure)
    # size_positions groups on the same key and falls back to game_id, then market_id
    sized = kelly.size_positions(
        [make_opp("x", 0.10, 0.2, exposure_key="event:e"), make_opp("y", 0.10, 0.2, exposure_key="event:e")],
        1_000.0, CFG,
    )
    assert sum(o.stake_fraction for o in sized) == pytest.approx(CFG.max_game_exposure)
    assert kelly.exposure_key(make_opp("z", 0.1, 0.2, game_id="g")) == "game:g"
    assert kelly.exposure_key(make_opp("z", 0.1, 0.2)) == "market:z"
    assert "exposure_key" in OPPORTUNITY_COLUMNS
    assert opportunities_table(sized)["exposure_key"].tolist() == ["event:e", "event:e"]


def test_price_band_applies_to_the_price_not_only_the_fair_prob():
    """config: 'never buy below 5c or above 95c' is a rule on what we pay."""
    cfg = replace(CFG, min_edge=0.0, slippage_buffer=0.0)
    # fair 0.06 is inside the band but the 2c ask is below the 5c floor: no YES
    cheap = FakeMarket("cheap", "Will the Jets win Super Bowl LXI?", "futures", team="NYJ", book=book(0.01, 0.02, size=100_000))
    assert find_edges([cheap], {"cheap": 0.06}, cfg=cfg) == []
    assert find_edges([cheap], {"cheap": 0.06}, cfg=CFG) == []  # with the default 1c buffer too
    # NO side: YES 0.97/0.98 with fair 0.94 -> NO fair 0.06 (in band) but NO price 0.03 (out)
    rich = FakeMarket("rich", "Chiefs vs Panthers", "moneyline", home="KC", away="CAR", book=book(0.97, 0.98, size=100_000))
    assert find_edges([rich], {"rich": 0.94}, cfg=cfg) == []
    # YES 0.96/0.97 with fair 0.90 -> NO at 4c: out as well
    rich2 = FakeMarket("rich2", "Chiefs vs Panthers", "moneyline", home="KC", away="CAR", book=book(0.96, 0.97, size=100_000))
    assert find_edges([rich2], {"rich2": 0.90}, cfg=CFG) == []
    # the floor is inclusive: a 5c ask is buyable, a 4c ask is not (same fair probability)
    ok = FakeMarket("ok", "Will the Jets win Super Bowl LXI?", "futures", team="NYJ", book=book(0.04, 0.05, size=100_000))
    (o,) = find_edges([ok], {"ok": 0.20}, cfg=CFG)
    assert o.side == "YES" and o.price == pytest.approx(0.05) and o.effective_price == pytest.approx(0.06)
    ok.book = book(0.03, 0.04, size=100_000)
    assert find_edges([ok], {"ok": 0.20}, cfg=CFG) == []
    # no-book path uses the same band on the last price
    nb = FakeMarket("nb", "Will the Jets win Super Bowl LXI?", "futures", team="NYJ", prices=[0.03, 0.97], liquidity=5_000.0)
    assert find_edges([nb], {"nb": 0.20}, cfg=CFG) == []


def test_no_side_liquidity_is_no_dollars_not_yes_bid_notional():
    cfg = replace(CFG, min_edge=0.0)
    # YES bids 0.90 x 1000: a YES seller would receive $900, but a NO buyer can only deploy
    # (1 - 0.90) * 1000 = $100 -> below min_liquidity_usd, so no opportunity
    m = FakeMarket("m", "Chiefs vs Panthers", "moneyline", home="KC", away="CAR",
                   book=FakeBook(bids=[(0.90, 1000)], asks=[(0.92, 1000)]))
    assert find_edges([m], {"m": 0.80}, cfg=cfg) == []
    (o,) = find_edges([m], {"m": 0.80}, cfg=replace(cfg, min_liquidity_usd=50.0))
    assert o.side == "NO" and o.price == pytest.approx(0.10)
    assert o.liquidity_usd == pytest.approx(100.0)
    # bid levels within the 1c buffer count in NO dollars; a level 10c away does not
    m.book = FakeBook(bids=[(0.90, 1000), (0.89, 2000), (0.80, 50_000)], asks=[(0.92, 1000)])
    (o,) = find_edges([m], {"m": 0.80}, cfg=replace(cfg, min_liquidity_usd=50.0))
    assert o.liquidity_usd == pytest.approx(0.10 * 1000 + 0.11 * 2000)
    # mirror: YES bids 0.05 x 5000 is only $250 of YES notional but $4,750 of NO -> not thin
    dog = FakeMarket("dog", "Panthers vs Chiefs", "moneyline", home="CAR", away="KC",
                     book=FakeBook(bids=[(0.05, 5000)], asks=[(0.07, 5000)]))
    (o,) = find_edges([dog], {"dog": 0.01}, cfg=replace(cfg, min_prob=0.0, max_prob=1.0))
    assert o.side == "NO" and o.price == pytest.approx(0.95) and o.liquidity_usd == pytest.approx(4750.0)
    # YES side is unchanged (ask notional within the buffer); empty books are zero
    assert edge_mod._side_depth_usd(book(0.50, 0.52), "YES", 0.01) == pytest.approx(0.52 * 5000 + 0.53 * 5000)
    assert edge_mod._side_depth_usd(book(0.50, 0.52), "NO", 0.01) == pytest.approx(0.50 * 5000 + 0.51 * 5000)
    assert edge_mod._side_depth_usd(FakeBook(), "NO", 0.01) == 0.0
    assert edge_mod._side_depth_usd(FakeBook(), "YES", 0.01) == 0.0


def test_stake_is_capped_by_the_liquidity_that_justified_the_edge():
    m = FakeMarket("m", "Chiefs vs Bills", "moneyline", home="KC", away="BUF",
                   book=FakeBook(bids=[(0.50, 600)], asks=[(0.52, 1200)]))
    (o,) = find_edges([m], {"m": 0.65}, cfg=CFG, bankroll=1_000_000.0)
    assert o.liquidity_usd == pytest.approx(624.0)
    assert o.stake_usd == pytest.approx(624.0)  # not $30,000 against 1,200 resting shares
    assert o.stake_fraction == pytest.approx(624.0 / 1_000_000.0)
    assert "capped by liquidity" in o.reason
    # with a small bankroll the per-position cap binds first and liquidity is not mentioned
    (o,) = find_edges([m], {"m": 0.65}, cfg=CFG, bankroll=1_000.0)
    assert o.stake_usd == pytest.approx(30.0) and "liquidity" not in o.reason
    # size_positions directly: utilisation factor, exhausted depth, unknown (NaN) depth
    opps = [
        make_opp("a", 0.10, 0.5, liquidity_usd=100.0),
        make_opp("b", 0.09, 0.5, liquidity_usd=0.0),
        make_opp("c", 0.08, 0.5, liquidity_usd=float("nan")),
    ]
    sized = {o.market_id: o for o in kelly.size_positions(opps, 10_000.0, CFG, max_liquidity_fraction=0.5)}
    assert sized["a"].stake_usd == pytest.approx(50.0) and "capped by liquidity" in sized["a"].reason
    assert sized["b"].stake_usd == 0.0 and "liquidity exhausted" in sized["b"].reason
    assert sized["c"].stake_usd == pytest.approx(300.0)  # unknown depth: only the other caps apply
    with pytest.raises(ValueError):
        kelly.size_positions(opps, 10_000.0, CFG, max_liquidity_fraction=-1.0)


def _afc_teams() -> list[str]:
    return [t for t in CURRENT_TEAMS if DIVISIONS[t][0] == "AFC"]


def test_find_arbitrage_requires_one_division_or_one_conference():
    # AFC East + AFC West winners under one slug: two YES pay, so bids summing to 2.0 are not a sell-all
    east = _division_event({"BUF": (0.35, 0.37), "MIA": (0.25, 0.27), "NYJ": (0.25, 0.27), "NE": (0.20, 0.22)}, slug="afc-divs")
    west = [FakeMarket(f"div-{t}", f"Will the {t} win the AFC West?", "futures", team=t, event_slug="afc-divs", book=book(b, a, size=1000))
            for t, (b, a) in {"KC": (0.40, 0.42), "DEN": (0.25, 0.27), "LAC": (0.20, 0.22), "LV": (0.15, 0.17)}.items()]
    assert find_arbitrage(east + west) == []
    assert [x["type"] for x in find_arbitrage(east)] == ["event_sell_all"]  # one division alone is fine
    # AFC + NFC conference champions in one slug (bids 0.20 x 8 = 1.6): two YES pay
    conf = [FakeMarket(f"cc-{t}", f"Will the {t} win the {'AFC' if i < 4 else 'NFC'} Championship?", "futures",
                       team=t, event_slug="conf-champs", book=book(0.20, 0.22, size=1000))
            for i, t in enumerate(("KC", "BUF", "BAL", "CIN", "PHI", "DET", "SF", "GB"))]
    assert find_arbitrage(conf) == []
    afc = conf[:4]
    for m in afc:
        m.book = book(0.30, 0.32, size=1000)
    (s,) = find_arbitrage(afc)
    assert s["type"] == "event_sell_all" and s["complete"] is False and s["category"] == "conference"
    # 16 AFC division-winner markets under one slug: four YES pay -> neither complete nor exclusive
    afc_divs = [FakeMarket(f"d-{t}", f"Will the {t} win the AFC {DIVISIONS[t][1]}?", "futures", team=t,
                           event_slug="afc-all-divs", book=book(0.05, 0.06, size=1000)) for t in _afc_teams()]
    assert find_arbitrage(afc_divs) == []
    # 16 AFC #1-seed markets: exactly one pays -> complete; asks 0.06 x 16 = 0.96 -> buy-all
    seeds = [FakeMarket(f"s-{t}", f"Will the {t} clinch the #1 seed?", "futures", team=t,
                        event_slug="afc-seed", book=book(0.05, 0.06, size=1000)) for t in _afc_teams()]
    (b,) = find_arbitrage(seeds)
    assert b["type"] == "event_buy_all" and b["complete"] is True and b["category"] == "top_seed"
    assert b["sum_asks"] == pytest.approx(0.96)
    # all 32 #1-seed markets in one slug: two #1 seeds -> nothing
    seeds32 = [FakeMarket(f"s-{t}", f"Will the {t} clinch the #1 seed?", "futures", team=t,
                          event_slug="all-seeds", book=book(0.02, 0.03, size=1000)) for t in CURRENT_TEAMS]
    assert find_arbitrage(seeds32) == []
    # an unknown team code disables the event-level logic
    weird = _division_event({"BUF": (0.60, 0.62), "MIA": (0.30, 0.32), "XXX": (0.30, 0.32)})
    assert find_arbitrage(weird) == []


def test_event_complete_is_category_aware():
    afc = _afc_teams()
    assert edge_mod._event_complete(afc, "division") is False
    assert edge_mod._event_complete(afc, "conference") is True
    assert edge_mod._event_complete(afc, "top_seed") is True
    assert edge_mod._event_complete(afc, "super_bowl") is False
    assert edge_mod._event_complete(["BUF", "MIA", "NYJ", "NE"], "division") is True
    assert edge_mod._event_complete(["BUF", "MIA", "NYJ", "NE"], "conference") is False
    assert edge_mod._event_complete(["BUF", "MIA", "NYJ", "KC"], "division") is False
    assert edge_mod._event_complete(list(CURRENT_TEAMS), "super_bowl") is True
    assert edge_mod._event_complete(list(CURRENT_TEAMS), "conference") is False
    assert edge_mod._event_complete(list(CURRENT_TEAMS), "division") is False
    assert edge_mod._event_complete(["BUF", "MIA", "NYJ", "NE"]) is True  # legacy shape check
    assert edge_mod._event_complete(["BUF", "MIA", "NYJ", "XXX"], "division") is False
    assert edge_mod._exclusive_scope(["BUF", "KC"], "division") is False
    assert edge_mod._exclusive_scope(["BUF", "KC"], "conference") is True
    assert edge_mod._exclusive_scope(["BUF", "PHI"], "conference") is False
    assert edge_mod._exclusive_scope(["BUF", "PHI"], "super_bowl") is True
    assert edge_mod._exclusive_scope(["BUF", "PHI"], "playoffs") is False


def test_make_the_super_bowl_is_a_conference_market_and_futures_type_wins():
    for q in ("Will the Chiefs make the Super Bowl?", "Will the Chiefs reach the Super Bowl?",
              "Will the Chiefs play in Super Bowl LXI?", "Will the Chiefs advance to the Super Bowl?",
              "Will the Chiefs make it to the Super Bowl?"):
        assert futures_category(q) == "conference", q
    for q in ("Will the Chiefs win the Super Bowl?", "Will the Chiefs win Super Bowl LXI?",
              "Super Bowl LXI Champion: Chiefs?", "Will the Chiefs be the Super Bowl champion?"):
        assert futures_category(q) == "super_bowl", q
    # 32 'make the Super Bowl' markets at 5c bids (sum 1.6): two YES pay, selling all is NOT risk-free
    reach = [FakeMarket(f"r-{t}", f"Will the {FULL_NAMES[t]} make the Super Bowl?", "futures", team=t,
                        event_slug="make-sb", book=book(0.05, 0.07, size=1000)) for t in CURRENT_TEAMS]
    assert find_arbitrage(reach) == []
    # the parser's futures_type takes precedence over the question text
    m = FakeMarket("x", "Will the Chiefs win Super Bowl LXI?", "futures", team="KC")
    assert market_category(m) == "super_bowl"
    m.futures_type = "conference"
    assert market_category(m) == "conference"
    m.futures_type = "win_total"
    assert market_category(m) is None
    m.futures_type = "something-new"
    assert market_category(m) == "super_bowl"  # unknown value -> text fallback
    # a group the parser typed win_total is never treated as exclusive even if the text looks like it
    wt = _division_event({"BUF": (0.50, 0.52), "MIA": (0.25, 0.27), "NYJ": (0.15, 0.17), "NE": (0.20, 0.22)})
    assert [x["type"] for x in find_arbitrage(wt)] == ["event_sell_all"]
    for m in wt:
        m.futures_type = "win_total"
    assert find_arbitrage(wt) == []
    # consistency_checks uses the same classifier: 'make the SB' at 0.30 is a conference price
    ms = [
        _futures("KC", "super_bowl", 0.15),
        FakeMarket("mk", "Will the Chiefs make the Super Bowl?", "futures", team="KC", event_slug="msb",
                   book=book(0.29, 0.31, size=1000)),
        _futures("KC", "playoffs", 0.80),
    ]
    assert consistency_checks(ms, None) == []
    # unknown team codes in a division event are skipped rather than raising
    unknown = [FakeMarket(f"u-{t}", f"Will {t} win the AFC East?", "futures", team=t, event_slug="afc-east-u",
                          book=book(0.20, 0.22, size=1000)) for t in ("AAA", "BBB", "CCC", "DDD")]
    assert consistency_checks(unknown, None) == []
