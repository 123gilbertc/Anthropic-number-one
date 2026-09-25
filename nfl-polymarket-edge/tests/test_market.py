import math

import numpy as np
import pandas as pd
import pytest

from nfl_edge import odds
from nfl_edge.config import CACHE_DIR, NFL_MARGIN_SIGMA
from nfl_edge.data import nflverse
from nfl_edge.models import market

GAMES = CACHE_DIR / "games.csv"
pytestmark = pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")

METHODS = ("multiplicative", "additive", "power", "shin")


@pytest.fixture(scope="module")
def games():
    return nflverse.load_games(path=GAMES)


@pytest.fixture(scope="module")
def ml_games(games):
    return games[games["home_moneyline"].notna() & games["away_moneyline"].notna()]


# ------------------------------------------------------------------ market_probs
@pytest.mark.parametrize("method", METHODS)
def test_market_probs_matches_odds_devig_on_sampled_rows(games, ml_games, method):
    """Vectorised devig reproduces the scalar foundation devig row by row."""
    rng = np.random.default_rng(42)
    idx = rng.choice(ml_games.index.to_numpy(), 50, replace=False)
    probs = market.market_probs(games, devig=method)
    assert probs.name == "market_prob" and probs.index.equals(games.index)
    for i in idx:
        raw = [odds.american_to_prob(games.at[i, "home_moneyline"]), odds.american_to_prob(games.at[i, "away_moneyline"])]
        ref = odds.devig(raw, method)[0]
        assert math.isclose(probs[i], ref, abs_tol=1e-9), (method, i, probs[i], ref)


@pytest.mark.parametrize("method", ("shin", "power"))
def test_devig_rows_bulk_cross_check(ml_games, method):
    rng = np.random.default_rng(7)
    sample = ml_games.sample(500, random_state=int(rng.integers(1 << 30)))
    raw = np.column_stack([sample["home_ml_prob_raw"], sample["away_ml_prob_raw"]])
    assert market.devig_check(raw, method) < 1e-9
    fair = market.devig_rows(raw, method)
    assert np.allclose(fair.sum(axis=1), 1.0, atol=1e-12)
    # vig removal never flips the favourite
    assert np.all((fair[:, 0] > fair[:, 1]) == (raw[:, 0] > raw[:, 1]))


def test_devig_rows_edge_cases():
    # already fair -> pass through untouched
    fair = market.devig_rows(np.array([[0.6, 0.4], [0.25, 0.75]]), "shin")
    assert np.array_equal(fair, np.array([[0.6, 0.4], [0.25, 0.75]]))
    # negative margin (sum < 1) follows odds.devig exactly for every method
    row = [0.48, 0.5]
    for m in METHODS:
        assert np.allclose(market.devig_rows(np.array([row]), m)[0], odds.devig(row, m), atol=1e-12)
    # heavy favourite
    row = [odds.american_to_prob(-900), odds.american_to_prob(+600)]
    for m in METHODS:
        out = market.devig_rows(np.array([row]), m)[0]
        assert np.allclose(out, odds.devig(row, m), atol=1e-9)
        assert 0.85 < out[0] < 0.92
    # empty input is fine
    assert market.devig_rows(np.empty((0, 2)), "shin").shape == (0, 2)
    with pytest.raises(ValueError):
        market.devig_rows(np.array([[0.5, 0.5]]), "nope")
    with pytest.raises(ValueError):
        market.devig_rows(np.array([[1.2, 0.5]]), "shin")
    with pytest.raises(ValueError):
        market.devig_rows(np.array([0.5, 0.5]), "shin")


def test_market_probs_multiplicative_equals_nflverse_baseline(games, ml_games):
    """The nflverse `market_prob` column is the multiplicative devig; we must reproduce it."""
    probs = market.market_probs(games, devig="multiplicative")
    played = games["market_prob"].notna()
    assert np.allclose(probs[played], games.loc[played, "market_prob"], atol=1e-12)
    no_ml = played & ~games.index.isin(ml_games.index)
    assert no_ml.sum() > 1500  # 1999-2005 have spreads but no moneylines
    assert np.allclose(probs[no_ml], games.loc[no_ml, "home_spread_prob"], atol=1e-12)
    assert probs[games["spread_line"].isna() & ~games.index.isin(ml_games.index)].isna().all()


def test_market_probs_shin_is_sharper_than_multiplicative(games, ml_games):
    shin = market.market_probs(games, "shin")[ml_games.index]
    mult = market.market_probs(games, "multiplicative")[ml_games.index]
    fav = mult > 0.5
    # Shin moves probability from the longshot to the favourite
    assert (shin[fav] >= mult[fav] - 1e-12).mean() > 0.99
    assert (shin[~fav] <= mult[~fav] + 1e-12).mean() > 0.99
    assert (shin - mult).abs().max() < 0.03


def test_market_probs_spread_fallback_and_sigma():
    df = pd.DataFrame(
        {
            "home_moneyline": [-150.0, np.nan, np.nan, -120.0],
            "away_moneyline": [130.0, np.nan, np.nan, np.nan],
            "spread_line": [3.0, 7.0, np.nan, -2.5],
        }
    )
    p = market.market_probs(df, "shin")
    assert math.isclose(p[0], odds.devig_two_way(-150, 130, "shin")[0], abs_tol=1e-9)
    assert math.isclose(p[1], odds.spread_to_prob(7.0), abs_tol=1e-12)
    assert np.isnan(p[2])
    assert math.isclose(p[3], odds.spread_to_prob(-2.5), abs_tol=1e-12)  # one-sided ML -> spread
    p10 = market.market_probs(df, "shin", sigma=10.0)
    assert math.isclose(p10[1], odds.spread_to_prob(7.0, sigma=10.0), abs_tol=1e-12)
    assert p10[1] > p[1]
    with pytest.raises(ValueError):
        market.market_probs(df, "bogus")
    with pytest.raises(KeyError):
        market.market_probs(df.drop(columns=["spread_line"]))


# ------------------------------------------------------------------ spread model
def test_fit_spread_sigma_on_real_games(games):
    sigma = market.fit_spread_sigma(games)
    assert 13.0 <= sigma <= 14.0, sigma
    mu, sigma_free = market.fit_spread_model(games)
    assert abs(mu) < 0.5  # closing spread is essentially unbiased
    assert abs(sigma_free - sigma) < 0.05
    assert sigma_free <= sigma + 1e-12  # freeing the mean can only reduce the residual sd
    assert abs(sigma - NFL_MARGIN_SIGMA) < 0.5


def test_fit_spread_model_recovers_synthetic_offset():
    rng = np.random.default_rng(3)
    n = 20000
    spread = rng.choice(np.arange(-14, 14.5, 0.5), n)
    margin = spread + 1.5 + rng.normal(0.0, 12.0, n)
    df = pd.DataFrame({"played": True, "spread_line": spread, "margin": margin})
    mu, sigma = market.fit_spread_model(df)
    assert abs(mu - 1.5) < 0.3
    assert abs(sigma - 12.0) < 0.3
    assert market.fit_spread_sigma(df) > sigma  # pinned mean absorbs the offset
    df.loc[:, "played"] = False
    with pytest.raises(ValueError):
        market.fit_spread_sigma(df)


def test_spread_calibration_monotone(games):
    tbl = market.spread_calibration(games)
    cols = ["bin", "spread_lo", "spread_hi", "spread_mean", "n", "home_win_rate", "model_prob", "gap"]
    assert list(tbl.columns) == cols
    assert tbl["home_win_rate"].is_monotonic_increasing
    assert tbl["model_prob"].is_monotonic_increasing
    assert tbl["spread_lo"].is_monotonic_increasing
    played = games["played"] & games["spread_line"].notna()
    assert tbl["n"].sum() == played.sum()
    assert (tbl["n"] >= 100).all()
    assert (tbl["gap"].abs() < 0.08).all()
    assert np.allclose(tbl["gap"], tbl["home_win_rate"] - tbl["model_prob"])
    # custom bins; a pick'em (0.0) lands in the [0, 2) bucket
    custom = market.spread_calibration(games, bins=[-np.inf, 0.0, 2.0, np.inf])
    assert len(custom) == 3 and custom["home_win_rate"].is_monotonic_increasing
    zero = games[played & (games["spread_line"] == 0.0)]
    assert custom.loc[1, "n"] >= len(zero)
    with pytest.raises(ValueError):
        market.spread_calibration(games, bins=[3.0, 1.0])


def test_line_to_prob_table():
    tbl = market.line_to_prob_table(13.0)
    assert list(tbl.columns) == ["spread_line", "p_home", "p_away", "home_american", "away_american"]
    assert len(tbl) == 57
    assert tbl["spread_line"].iloc[0] == -14.0 and tbl["spread_line"].iloc[-1] == 14.0
    assert np.allclose(np.diff(tbl["spread_line"]), 0.5)
    assert tbl["p_home"].is_monotonic_increasing
    assert np.allclose(tbl["p_home"] + tbl["p_away"], 1.0)
    assert np.allclose(tbl["p_home"].to_numpy() + tbl["p_home"].to_numpy()[::-1], 1.0)  # symmetric
    mid = tbl[tbl["spread_line"] == 0.0].iloc[0]
    assert math.isclose(mid["p_home"], 0.5) and math.isclose(mid["home_american"], -100.0)
    three = tbl[tbl["spread_line"] == 3.0].iloc[0]
    assert math.isclose(three["p_home"], odds.spread_to_prob(3.0, sigma=13.0))
    assert 0.57 < three["p_home"] < 0.60
    wider = market.line_to_prob_table(16.0)
    assert wider.loc[wider["spread_line"] == 7.0, "p_home"].iloc[0] < three["p_home"] + 0.1
    assert (wider["p_home"].iloc[-1] < tbl["p_home"].iloc[-1])  # larger sigma flattens the curve
    with pytest.raises(ValueError):
        market.line_to_prob_table(0.0)
