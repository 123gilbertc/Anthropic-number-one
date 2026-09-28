"""Tests for nfl_edge.models.epa. Offline: cached nflverse data only (conftest blocks the network)."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import nflverse
from nfl_edge.models import epa

GAMES = CACHE_DIR / "games.csv"
STATS_2024 = CACHE_DIR / "stats_team_week_2024.csv"
pytestmark = pytest.mark.skipif(
    not (GAMES.exists() and STATS_2024.exists()), reason="nflverse cache (games + team-week stats) missing"
)

DYNASTIES = [(2007, "NE"), (2019, "BAL"), (2023, "SF")]


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def games() -> pd.DataFrame:
    return nflverse.load_games(path=GAMES)


@pytest.fixture(scope="module")
def team_week() -> pd.DataFrame:
    return nflverse.load_team_week_stats()


@pytest.fixture(scope="module")
def team_game(team_week: pd.DataFrame) -> pd.DataFrame:
    return epa.team_game_epa(team_week)


@pytest.fixture(scope="module")
def rated(games: pd.DataFrame, team_game: pd.DataFrame) -> pd.DataFrame:
    return epa.epa_ratings(games, team_game)


# ------------------------------------------------------------------------------ helpers
def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _accuracy(y: np.ndarray, p: np.ndarray) -> float:
    pred = (np.asarray(p) > 0.5).astype(float)
    return float(np.mean(np.where(y == 0.5, 0.5, (pred == y).astype(float))))


def _complete_obs(team_game: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Observations epa_ratings may use: both teams present and the game is in `games`."""
    return team_game[team_game["def_epa_pp"].notna() & team_game["game_id"].isin(games["game_id"])]


def _league_mean(obs: pd.DataFrame, season: int) -> float:
    prior = obs[(obs["season"] < season) & (obs["season"] >= season - epa.LEAGUE_MEAN_SEASONS)]
    return float(prior["off_epa_pp"].mean()) if len(prior) else epa.DEFAULT_LEAGUE_MEAN


def _team_rows(rated: pd.DataFrame, team: str) -> pd.DataFrame:
    """One row per game of `team` with its own pre-game off/def rating and n, chronological."""
    home = rated[rated["home_team_c"] == team]
    away = rated[rated["away_team_c"] == team]
    rows = pd.concat([
        pd.DataFrame({"game_id": home["game_id"], "season": home["season"], "week": home["week"],
                      "game_date": home["game_date"], "off": home["home_off_epa"],
                      "dfn": home["home_def_epa"], "n": home["epa_n_home"]}),
        pd.DataFrame({"game_id": away["game_id"], "season": away["season"], "week": away["week"],
                      "game_date": away["game_date"], "off": away["away_off_epa"],
                      "dfn": away["away_def_epa"], "n": away["epa_n_away"]}),
    ])
    return rows.sort_values(["game_date", "week"]).reset_index(drop=True)


def _net_entering_last_regular_season_game(rated: pd.DataFrame, season: int) -> pd.Series:
    g = rated[(rated["season"] == season) & (rated["game_type"] == "REG")]
    long = pd.concat([
        g[["week", "home_team_c", "home_off_epa", "home_def_epa"]].set_axis(["week", "team", "off", "def"], axis=1),
        g[["week", "away_team_c", "away_off_epa", "away_def_epa"]].set_axis(["week", "team", "off", "def"], axis=1),
    ])
    last = long.sort_values("week").groupby("team").tail(1).set_index("team")
    return (last["off"] - last["def"]).sort_values(ascending=False)


# ------------------------------------------------------------------------ team_game_epa
def test_team_game_epa_columns_and_keys(team_game: pd.DataFrame):
    assert list(team_game.columns) == list(epa.TEAM_GAME_COLUMNS)
    assert not team_game.duplicated(["game_id", "team_c"]).any()
    assert team_game["team_c"].notna().all() and team_game["game_id"].notna().all()
    assert set(team_game["season_type"]) == {"REG", "POST"}
    assert "2024_22_KC_PHI" in set(team_game["game_id"])  # Super Bowl LIX is included
    assert team_game["off_epa_pp"].notna().all()
    # off_epa_pp is bounded like a per-play quantity
    assert team_game["off_epa_pp"].abs().max() < 1.5


def test_team_game_epa_known_game(team_game: pd.DataFrame):
    """2024 week 1 DAL @ CLE, straight from the cached weekly stats."""
    g = team_game[team_game["game_id"] == "2024_01_DAL_CLE"].set_index("team_c")
    cle, dal = g.loc["CLE"], g.loc["DAL"]
    assert cle["opponent_c"] == "DAL" and dal["opponent_c"] == "CLE"
    assert cle["off_plays"] == 45 + 19 + 6  # attempts + carries + sacks_suffered
    assert cle["off_epa"] == pytest.approx(-23.115656 + 4.085149, abs=1e-6)
    assert cle["off_epa_pp"] == pytest.approx(cle["off_epa"] / 70.0)
    assert cle["pass_epa_pp"] == pytest.approx(-23.115656 / (45 + 6), abs=1e-6)
    assert cle["rush_epa_pp"] == pytest.approx(4.085149 / 19, abs=1e-6)
    assert cle["cpoe"] == pytest.approx(-7.962811, abs=1e-6)
    # defensive EPA allowed is exactly the opponent's offensive EPA per play
    assert dal["def_epa_pp"] == cle["off_epa_pp"]
    assert cle["def_epa_pp"] == dal["off_epa_pp"]


def test_def_epa_is_opponent_offense_everywhere(team_game: pd.DataFrame):
    opp = team_game[["game_id", "team_c", "off_epa_pp"]].rename(
        columns={"team_c": "opponent_c", "off_epa_pp": "opp_off"}
    )
    m = team_game.merge(opp, on=["game_id", "opponent_c"], how="left")
    both = m["opp_off"].notna()
    assert both.sum() > 14000
    assert np.array_equal(m.loc[both, "def_epa_pp"].to_numpy(), m.loc[both, "opp_off"].to_numpy())
    # the only incomplete games are the 2001-2002 Jacksonville rows with a missing opponent row
    incomplete = m.loc[~both, "game_id"]
    assert incomplete.str.endswith("_JAX").all() and set(m.loc[~both, "season"]) <= {2001, 2002}


def test_passing_epa_includes_sacks(team_week: pd.DataFrame):
    """Documented data fact behind off_plays = attempts + carries + sacks_suffered.

    receiving_epa counts targets only, so passing_epa - receiving_epa isolates the non-target
    dropbacks; it must scale with sacks_suffered at roughly -1.5..-2 EPA per sack.
    """
    tw = team_week[(team_week["season"] == 2024) & team_week["passing_epa"].notna()]
    gap = (tw["passing_epa"] - tw["receiving_epa"]).to_numpy()
    sacks = tw["sacks_suffered"].to_numpy(dtype=float)
    slope = np.polyfit(sacks, gap, 1)[0]
    assert -2.5 < slope < -1.0
    assert np.corrcoef(gap, sacks)[0, 1] < -0.5


# -------------------------------------------------------------------------- epa_ratings
def test_ratings_columns_and_alignment(games: pd.DataFrame, rated: pd.DataFrame):
    assert list(rated.columns) == list(games.columns) + list(epa.RATING_COLUMNS)
    assert len(rated) == len(games) and rated.index.equals(games.index)
    assert (rated["game_id"].to_numpy() == games["game_id"].to_numpy()).all()
    for c in epa.RATING_COLUMNS:
        assert np.isfinite(rated[c]).all(), c  # every row rated, unplayed 2026 games included
    net_home = rated["home_off_epa"] - rated["home_def_epa"]
    net_away = rated["away_off_epa"] - rated["away_def_epa"]
    assert np.allclose(rated["epa_diff"], net_home - net_away, atol=1e-12)
    assert (rated["epa_n_home"] >= 0).all() and (rated["epa_n_away"] >= 0).all()
    # effective sample size saturates at 1 / (1 - 0.5 ** (1 / halflife)) for halflife 5
    assert rated["epa_n_home"].max() < 1.0 / (1.0 - 0.5 ** 0.2) + 1e-9
    assert rated["epa_diff"].abs().max() < 0.5
    assert rated["epa_diff"].std() > 0.03


def test_first_games_are_league_mean_prior(games: pd.DataFrame, team_game: pd.DataFrame, rated: pd.DataFrame):
    wk1 = rated[(rated["season"] == 1999) & (rated["week"] == 1)]
    assert len(wk1) > 10
    for c in ("home_off_epa", "home_def_epa", "away_off_epa", "away_def_epa"):
        assert (wk1[c] == epa.DEFAULT_LEAGUE_MEAN).all(), c
    assert (wk1["epa_diff"] == 0).all()
    assert (wk1["epa_n_home"] == 0).all() and (wk1["epa_n_away"] == 0).all()
    # expansion Houston (2002): no history, so its first rating is the 2002 pre-season league mean
    hou = _team_rows(rated, "HOU").iloc[0]
    assert hou["season"] == 2002 and hou["n"] == 0
    mu_2002 = _league_mean(_complete_obs(team_game, games), 2002)
    assert mu_2002 != epa.DEFAULT_LEAGUE_MEAN
    assert hou["off"] == pytest.approx(mu_2002) and hou["dfn"] == pytest.approx(mu_2002)


def test_unplayed_games_use_latest_ratings_without_updating(rated: pd.DataFrame):
    pending = rated[~rated["played"]]
    assert len(pending) > 100 and (pending["season"] == rated["season"].max()).all()
    assert (pending["epa_n_home"] > 0).all() and (pending["epa_n_away"] > 0).all()
    for team in ("KC", "PHI", "DET"):
        rows = _team_rows(rated, team)
        future = rows[rows["game_id"].isin(pending["game_id"])]
        assert len(future) > 5
        assert future["off"].nunique() == 1 and future["dfn"].nunique() == 1 and future["n"].nunique() == 1


def test_unadjusted_ratings_match_reference_recursion(games: pd.DataFrame, team_game: pd.DataFrame):
    """A plain Python re-implementation of the EW recursion (iters=1) must match the vectorized one."""
    halflife, prior, carry = 4.0, 5.0, 0.4
    rated = epa.epa_ratings(games, team_game, halflife=halflife, prior_games=prior, carryover=carry, iters=1)
    obs = _complete_obs(team_game, games).set_index(["game_id", "team_c"])
    decay = 0.5 ** (1.0 / halflife)
    for team in ("KC", "LA", "HOU"):  # includes a relocated (STL->LA) and an expansion franchise
        rows = _team_rows(rated, team)
        s_off = s_def = w = 0.0
        prev_season = None
        for r in rows.itertuples(index=False):
            if prev_season is not None and r.season != prev_season:
                s_off, s_def, w = s_off * carry, s_def * carry, w * carry
            mu = _league_mean(obs.reset_index(), int(r.season))
            assert r.off == pytest.approx(mu + s_off / (w + prior), abs=1e-12), (team, r.game_id)
            assert r.n == pytest.approx(w, abs=1e-12)
            assert r.off - r.dfn == pytest.approx((s_off - s_def) / (w + prior), abs=1e-12)
            key = (r.game_id, team)
            if key in obs.index:
                s_off = decay * s_off + (obs.loc[key, "off_epa_pp"] - mu)
                s_def = decay * s_def + (obs.loc[key, "def_epa_pp"] - mu)
                w = decay * w + 1.0
            prev_season = r.season
        assert w > 0


def test_opponent_adjustment_changes_ratings(games: pd.DataFrame, team_game: pd.DataFrame, rated: pd.DataFrame):
    raw = epa.epa_ratings(games, team_game, iters=1)
    played = rated["played"] & (rated["season"] >= 2003)
    assert not np.allclose(raw.loc[played, "epa_diff"], rated.loc[played, "epa_diff"])
    assert np.corrcoef(raw.loc[played, "epa_diff"], rated.loc[played, "epa_diff"])[0, 1] > 0.9
    # more iterations converge: pass 5 vs pass 6 differ far less than pass 1 vs pass 5
    six = epa.epa_ratings(games, team_game, iters=6)
    step = np.abs(six.loc[played, "epa_diff"] - rated.loc[played, "epa_diff"]).max()
    jump = np.abs(rated.loc[played, "epa_diff"] - raw.loc[played, "epa_diff"]).max()
    assert step < 0.1 * jump


def test_no_lookahead_leakage(games: pd.DataFrame, team_game: pd.DataFrame, rated: pd.DataFrame):
    """Perturbing one game's EPA (its outcome, as this model sees it) must leave every game on or
    before that date untouched, and must change later games for the teams involved."""
    target = games[(games["season"] == 2019) & (games["week"] == 10)].iloc[0]
    tg2 = team_game.copy()
    home_row = (tg2["game_id"] == target["game_id"]) & (tg2["team_c"] == target["home_team_c"])
    away_row = (tg2["game_id"] == target["game_id"]) & (tg2["team_c"] == target["away_team_c"])
    assert home_row.sum() == 1 and away_row.sum() == 1
    # the home offense gains 0.5 EPA/play, so the away defense allowed 0.5 more (consistent self-join)
    tg2.loc[home_row, "off_epa_pp"] += 0.5
    tg2.loc[away_row, "def_epa_pp"] += 0.5
    shifted = epa.epa_ratings(games, tg2)

    cols = list(epa.RATING_COLUMNS)
    before = (rated["game_date"] <= target["game_date"]).to_numpy()
    assert np.array_equal(rated.loc[before, cols].to_numpy(), shifted.loc[before, cols].to_numpy())
    changed = np.abs(rated["epa_diff"].to_numpy() - shifted["epa_diff"].to_numpy()) > 1e-9
    assert changed.any()
    assert (rated.loc[changed, "game_date"] > target["game_date"]).all()
    for team in (target["home_team_c"], target["away_team_c"]):
        later = rated[(rated["game_date"] > target["game_date"])
                      & ((rated["home_team_c"] == team) | (rated["away_team_c"] == team))]
        assert changed[later.index[:3]].all()  # the team's next games move

    # Scores are never an input: flipping a result changes nothing at all.
    g2 = games.copy()
    row = g2.index[g2["game_id"] == target["game_id"]][0]
    g2.loc[row, ["home_score", "away_score"]] = g2.loc[row, ["away_score", "home_score"]].to_numpy()
    g2.loc[row, "margin"] = -g2.loc[row, "margin"]
    g2.loc[row, "home_win"] = 1.0 - g2.loc[row, "home_win"]
    flipped = epa.epa_ratings(g2, team_game)
    assert np.array_equal(rated[cols].to_numpy(), flipped[cols].to_numpy())


def test_runtime_under_30s(games: pd.DataFrame, team_week: pd.DataFrame):
    t0 = time.perf_counter()
    tg = epa.team_game_epa(team_week)
    out = epa.epa_ratings(games, tg)
    elapsed = time.perf_counter() - t0
    assert len(out) == len(games)
    assert elapsed < 30.0, f"epa pipeline took {elapsed:.1f}s"


@pytest.mark.parametrize("season,team", DYNASTIES)
def test_dynasties_rank_top3_entering_final_regular_season_game(rated: pd.DataFrame, season: int, team: str):
    net = _net_entering_last_regular_season_game(rated, season)
    assert len(net) == 32
    rank = int(np.flatnonzero(net.index == team)[0]) + 1
    assert rank <= 3, f"{season} {team} ranked {rank}: {net.head(5).round(4).to_dict()}"


def test_parameter_validation(games: pd.DataFrame, team_game: pd.DataFrame):
    with pytest.raises(ValueError):
        epa.epa_ratings(games, team_game, halflife=0)
    with pytest.raises(ValueError):
        epa.epa_ratings(games, team_game, carryover=1.5)
    with pytest.raises(ValueError):
        epa.epa_ratings(games, team_game, iters=0)
    with pytest.raises(ValueError):
        epa.epa_ratings(games.drop(columns=["game_date"]), team_game)
    with pytest.raises(ValueError):
        epa.team_game_epa(team_game)  # wrong input shape (already aggregated)
    with pytest.raises(ValueError):
        epa.fit_epa_scale(games)  # no epa_diff yet
    with pytest.raises(ValueError):
        epa.fit_epa_hfa(games)


def test_subset_of_games_is_supported(games: pd.DataFrame, team_game: pd.DataFrame, rated: pd.DataFrame):
    """Ratings only depend on games in the frame: a 2003+ subset restarts from the prior."""
    sub = games[games["season"].between(2010, 2012)]
    out = epa.epa_ratings(sub, team_game)
    assert len(out) == len(sub) and out.index.equals(sub.index)
    first = out[(out["season"] == 2010) & (out["week"] == 1)]
    assert (first["epa_n_home"] == 0).all()
    # by the end of 2012 the subset run and the full run agree closely (decayed history)
    late = out["season"] == 2012
    assert np.corrcoef(out.loc[late, "epa_diff"], rated.loc[out.index[late], "epa_diff"])[0, 1] > 0.99


def test_empty_games_frame_returns_empty_ratings(games: pd.DataFrame, team_game: pd.DataFrame):
    """Regression: a slate with no rows (offseason, post-Super-Bowl, a filter miss) used to trip
    numpy's empty-reduction error instead of returning an empty rated frame."""
    for empty in (games.iloc[0:0], games[games["season"] == 1990]):
        out = epa.epa_ratings(empty, team_game)
        assert len(out) == 0 and out.index.equals(empty.index)
        assert list(out.columns) == list(games.columns) + list(epa.RATING_COLUMNS)
        for c in epa.RATING_COLUMNS:
            assert out[c].dtype == float, c
        assert epa.epa_prob(out["epa_diff"], 8.0).shape == (0,)
        with pytest.raises(ValueError):
            epa.fit_epa_scale(out)  # no played games: still a clear error, not a crash
    # validation still comes first: an empty frame missing a required column is rejected
    with pytest.raises(ValueError):
        epa.epa_ratings(games.iloc[0:0].drop(columns=["game_date"]), team_game)
    # the guard does not touch the smallest non-empty inputs
    one = epa.epa_ratings(games.iloc[:1], team_game)
    assert len(one) == 1 and one["epa_diff"].iloc[0] == 0.0 and one["epa_n_home"].iloc[0] == 0.0


# -------------------------------------------------------------------------- epa_prob
def test_epa_prob_shape_and_monotone():
    assert float(epa.epa_prob(0.0, 8.0)) == 0.5
    p = epa.epa_prob(np.array([-0.2, -0.05, 0.0, 0.05, 0.2]), 8.0)
    assert isinstance(p, np.ndarray) and p.shape == (5,)
    assert np.all(np.diff(p) > 0) and np.allclose(p + p[::-1], 1.0)
    assert np.allclose(epa.epa_prob(pd.Series([0.1, -0.1]), 8.0), [1 / (1 + np.exp(-0.8)), 1 / (1 + np.exp(0.8))])
    assert np.isfinite(epa.epa_prob(np.array([1e6, -1e6]), 8.0)).all()


def test_fit_epa_scale_and_walk_forward_logloss(rated: pd.DataFrame):
    scale = epa.fit_epa_scale(rated)  # played games 2003-2024
    assert 2.0 < scale < 30.0
    # MLE: nudging the scale either way cannot lower the in-sample loss
    fit = rated[rated["played"] & rated["season"].between(2003, 2024)]
    y_fit, x_fit = fit["home_win"].to_numpy(), fit["epa_diff"].to_numpy()
    best = _log_loss(y_fit, epa.epa_prob(x_fit, scale))
    assert best <= _log_loss(y_fit, epa.epa_prob(x_fit, scale * 1.1)) + 1e-9
    assert best <= _log_loss(y_fit, epa.epa_prob(x_fit, scale * 0.9)) + 1e-9

    # walk-forward: the scale for season S is fit on seasons < S only
    test = rated[rated["played"] & rated["season"].between(2015, 2024)]
    y = test["home_win"].to_numpy()
    p = np.empty(len(test))
    for s in range(2015, 2025):
        m = (test["season"] == s).to_numpy()
        p[m] = epa.epa_prob(test.loc[m, "epa_diff"], epa.fit_epa_scale(rated, seasons=range(2003, s)))
    ll = _log_loss(y, p)
    assert ll < 0.65, ll
    assert ll < _log_loss(y, np.full(len(y), y.mean()))  # beats the constant home-win rate
    assert _accuracy(y, p) > 0.60


def test_epa_prob_home_field_term():
    s = 8.0
    x = np.array([-0.2, -0.05, 0.0, 0.05, 0.2])
    # the contract form is unchanged and identical to an explicit hfa of zero
    assert np.array_equal(epa.epa_prob(x, s), epa.epa_prob(x, s, hfa=0.0))
    assert np.array_equal(epa.epa_prob(x, s), epa.epa_prob(x, s, hfa=0.0, neutral=np.zeros(5, dtype=bool)))
    # equal ratings: the home side gets expit(hfa); a neutral site removes it
    assert float(epa.epa_prob(0.0, s, hfa=0.3)) == pytest.approx(1 / (1 + np.exp(-0.3)))
    assert float(epa.epa_prob(0.0, s, hfa=0.3, neutral=True)) == 0.5
    assert np.allclose(epa.epa_prob(x, s, hfa=0.3), 1 / (1 + np.exp(-(s * x + 0.3))))
    # per-game indicator: booleans, 0/1 floats with NaN, or location strings all mean the same
    neutral = np.array([False, True, False, True, False])
    expect = 1 / (1 + np.exp(-(s * x + 0.3 * (~neutral))))
    assert np.allclose(epa.epa_prob(x, s, hfa=0.3, neutral=neutral), expect)
    assert np.allclose(epa.epa_prob(pd.Series(x), s, hfa=0.3, neutral=pd.Series(neutral)), expect)
    assert np.allclose(epa.epa_prob(x, s, hfa=0.3, neutral=np.array([0.0, 1.0, np.nan, 1.0, 0.0])), expect)
    assert np.allclose(epa.epa_prob(x, s, hfa=0.3, neutral=["Home", "Neutral", None, "neutral", "Home"]), expect)
    # swapping sides (negated diff and hfa) gives the complementary probability
    assert np.allclose(epa.epa_prob(x, s, hfa=0.3) + epa.epa_prob(-x, s, hfa=-0.3), 1.0)
    with pytest.raises(ValueError):
        epa.epa_prob(x, s, hfa=0.3, neutral=[True, False])


def test_neutral_site_helper(games: pd.DataFrame):
    n = epa.neutral_site(games)
    assert n.dtype == bool and n.shape == (len(games),)
    assert np.array_equal(n, (games["location"] == "Neutral").to_numpy())
    assert 50 < n.sum() < 200  # Super Bowls plus international games
    assert not epa.neutral_site(games.drop(columns=["location"])).any()
    assert epa.neutral_site(games.iloc[0:0]).shape == (0,)


def test_fit_epa_hfa_removes_home_bias(rated: pd.DataFrame):
    """Regression for the home-field-blind contract form: the joint (scale, hfa) MLE must absorb
    the ~5pp home under-prediction, in sample and walk-forward."""
    fit = rated[rated["played"] & rated["season"].between(2003, 2024)]
    x, y = fit["epa_diff"].to_numpy(), fit["home_win"].to_numpy()
    home = ~epa.neutral_site(fit)
    scale0 = epa.fit_epa_scale(rated)
    scale, hfa = epa.fit_epa_hfa(rated)
    assert 2.0 < scale < 30.0 and 0.1 < hfa < 0.6
    assert abs(scale - scale0) < 0.5
    # the contract form under-predicts home wins; the joint fit is unbiased on home games (score equation)
    p0 = epa.epa_prob(x, scale0)
    p1 = epa.epa_prob(x, scale, hfa=hfa, neutral=~home)
    assert np.mean(y - p0) > 0.04
    assert abs(np.mean((y - p1)[home])) < 1e-6
    assert abs(np.mean(y - p1)) < 0.005
    # joint MLE: nudging either parameter cannot lower the in-sample loss
    best = _log_loss(y, p1)
    for s_mult, h_mult in ((1.1, 1.0), (0.9, 1.0), (1.0, 1.1), (1.0, 0.9)):
        nudged = epa.epa_prob(x, scale * s_mult, hfa=hfa * h_mult, neutral=~home)
        assert best <= _log_loss(y, nudged) + 1e-9, (s_mult, h_mult)
    assert best < _log_loss(y, p0)

    # walk-forward 2015-2024: both parameters fit on seasons < S only; hfa improves log-loss and bias
    test = rated[rated["played"] & rated["season"].between(2015, 2024)]
    yt = test["home_win"].to_numpy()
    p_scale, p_hfa = np.empty(len(test)), np.empty(len(test))
    for s in range(2015, 2025):
        m = (test["season"] == s).to_numpy()
        prior = range(2003, s)
        p_scale[m] = epa.epa_prob(test.loc[m, "epa_diff"], epa.fit_epa_scale(rated, seasons=prior))
        sc, hf = epa.fit_epa_hfa(rated, seasons=prior)
        assert 0.2 < hf < 0.45, (s, hf)
        p_hfa[m] = epa.epa_prob(test.loc[m, "epa_diff"], sc, hfa=hf, neutral=epa.neutral_site(test.loc[m]))
    ll_scale, ll_hfa = _log_loss(yt, p_scale), _log_loss(yt, p_hfa)
    assert ll_hfa < ll_scale - 0.002, (ll_scale, ll_hfa)
    assert ll_hfa < 0.645
    assert _accuracy(yt, p_hfa) >= _accuracy(yt, p_scale)
    assert abs(p_hfa.mean() - yt.mean()) < abs(p_scale.mean() - yt.mean())
