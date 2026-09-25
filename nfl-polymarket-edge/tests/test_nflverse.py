import numpy as np
import pandas as pd
import pytest

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import nflverse

GAMES = CACHE_DIR / "games.csv"
pytestmark = pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")


@pytest.fixture(scope="module")
def games():
    return nflverse.load_games(path=GAMES)


def test_shape_and_derived(games):
    assert len(games) > 7000
    assert games["season"].min() == 1999
    for c in ("game_date", "played", "margin", "home_win", "home_team_c", "market_prob", "qb_change_home"):
        assert c in games.columns
    played = games[games["played"]]
    assert set(played["home_win"].unique()) <= {0.0, 0.5, 1.0}
    assert played["market_prob"].between(0.01, 0.99).all()
    assert (played["margin"] == played["home_score"] - played["away_score"]).all()


def test_franchise_canonical(games):
    assert set(games["home_team_c"]).issubset(set(nflverse.canonical(t) for t in games["home_team"]))
    assert "STL" not in set(games["home_team_c"]) and "LA" in set(games["home_team_c"])


def test_moneyline_devig_consistency(games):
    g = games[games["home_ml_prob"].notna()]
    assert len(g) > 4000
    assert np.allclose(g["home_ml_prob"] + g["away_ml_prob"], 1.0)
    # devigged moneyline and spread-implied prob agree closely on average
    assert abs((g["home_ml_prob"] - g["home_spread_prob"]).mean()) < 0.02


def test_market_baseline_is_calibrated(games):
    """Closing lines are well calibrated: bucketed win rate ~ implied prob."""
    g = games[games["played"] & (games["season"] >= 2007)]
    bins = pd.cut(g["market_prob"], [0, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
    tbl = g.groupby(bins, observed=True).agg(p=("market_prob", "mean"), y=("home_win", "mean"), n=("home_win", "size"))
    assert (abs(tbl["p"] - tbl["y"]) < 0.05).all(), tbl


def test_current_week_helpers(games):
    s = nflverse.current_season(games)
    w = nflverse.current_week(games, s)
    up = nflverse.upcoming_games(games, s, w)
    assert s >= 2024 and 1 <= w <= 22
    assert not up.empty and (~up["played"]).all()


def test_qb_change_flags(games):
    g = games[games["season"] == 2024]
    assert g["qb_change_home"].isin([0, 1]).all()
    assert 0.02 < g["qb_change_home"].mean() < 0.30
