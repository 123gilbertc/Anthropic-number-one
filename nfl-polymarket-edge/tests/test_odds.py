import math

import numpy as np
import pytest

from nfl_edge import odds


def test_american_decimal_roundtrip():
    for a in (-110, -200, +150, +100, -105, +350, -650):
        d = odds.american_to_decimal(a)
        assert math.isclose(odds.decimal_to_american(d), a, rel_tol=1e-9)


def test_american_prob():
    assert math.isclose(odds.american_to_prob(-110), 110 / 210)
    assert math.isclose(odds.american_to_prob(+150), 100 / 250)
    assert math.isclose(odds.american_to_prob(+100), 0.5)
    assert math.isclose(odds.prob_to_american(0.5), -100.0)
    assert math.isclose(odds.prob_to_american(0.75), -300.0)
    assert math.isclose(odds.prob_to_american(0.25), 300.0)


def test_devig_methods_sum_to_one_and_order():
    raw = [odds.american_to_prob(-166), odds.american_to_prob(+140)]
    assert odds.overround(raw) > 1
    for m in ("multiplicative", "additive", "power", "shin"):
        p = odds.devig(raw, m)
        assert math.isclose(sum(p), 1.0, abs_tol=1e-9)
        assert p[0] > p[1]
        assert p[0] < raw[0]  # vig removed
    # shin/power push less probability onto the longshot than multiplicative
    mult = odds.devig(raw, "multiplicative")
    shin = odds.devig(raw, "shin")
    power = odds.devig(raw, "power")
    assert shin[1] <= mult[1] + 1e-9
    assert power[1] <= mult[1] + 1e-9


def test_devig_heavy_favorite():
    raw = [odds.american_to_prob(-900), odds.american_to_prob(+600)]
    for m in ("multiplicative", "additive", "power", "shin"):
        p = odds.devig(raw, m)
        assert math.isclose(sum(p), 1.0, abs_tol=1e-9)
        assert 0.85 < p[0] < 0.92


def test_devig_no_vig_passthrough():
    assert odds.devig([0.6, 0.4], "shin") == [0.6, 0.4]


def test_devig_bad_input():
    with pytest.raises(ValueError):
        odds.devig([0.5])
    with pytest.raises(ValueError):
        odds.devig([0.5, 0.5], "nope")


def test_spread_prob_monotone_and_symmetric():
    assert math.isclose(odds.spread_to_prob(0.0), 0.5)
    assert odds.spread_to_prob(3.0) > 0.5 > odds.spread_to_prob(-3.0)
    assert math.isclose(odds.spread_to_prob(7.0) + odds.spread_to_prob(-7.0), 1.0)
    # a 3-point favorite is roughly 58-59%
    assert 0.57 < odds.spread_to_prob(3.0) < 0.60
    assert math.isclose(odds.prob_to_spread(odds.spread_to_prob(4.5)), 4.5, abs_tol=1e-9)


def test_elo_prob():
    assert math.isclose(odds.elo_diff_to_prob(0), 0.5)
    assert math.isclose(odds.elo_diff_to_prob(400), 10 / 11)
    assert math.isclose(odds.prob_to_elo_diff(odds.elo_diff_to_prob(123.4)), 123.4, abs_tol=1e-9)


def test_vectorized_matches_scalar():
    arr = np.array([-110.0, 150.0, np.nan])
    v = odds.american_to_prob_array(arr)
    assert math.isclose(v[0], odds.american_to_prob(-110))
    assert math.isclose(v[1], odds.american_to_prob(150))
    assert np.isnan(v[2])
    h, a = odds.devig_two_way_array([0.55, 0.6], [0.5, 0.45])
    assert np.allclose(h + a, 1.0)


def test_price_to_decimal():
    assert math.isclose(odds.price_to_decimal(0.25), 4.0)
