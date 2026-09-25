"""Tests for nfl_edge.models.elo: rating mechanics, look-ahead safety, fitting and persistence.

Synthetic frames exercise the maths exactly; the cached nflverse games table (offline) checks
real-data behaviour, quality and runtime. Network is blocked by tests/conftest.py.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd
import pytest

from nfl_edge.config import CACHE_DIR, ELO_MEAN, ELO_POINTS_PER_SPREAD_POINT
from nfl_edge.data import nflverse
from nfl_edge.models import elo
from nfl_edge.models.elo import EloModel, EloParams, fit_elo, load_params, save_params

GAMES = CACHE_DIR / "games.csv"
needs_cache = pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")


def elo_prob(diff: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


def make_games(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal load_games()-shaped frame from compact row specs."""
    recs = []
    for r in rows:
        margin = r.get("margin")
        played = margin is not None
        recs.append(
            {
                "game_id": r.get("game_id", f"{r['season']}_{r['week']:02d}_{r['away']}_{r['home']}"),
                "season": r["season"],
                "week": r["week"],
                "game_date": pd.Timestamp(r["game_date"]),
                "game_type": r.get("game_type", "REG"),
                "location": r.get("location", "Home"),
                "home_team_c": r["home"],
                "away_team_c": r["away"],
                "home_rest": r.get("home_rest", 7),
                "away_rest": r.get("away_rest", 7),
                "margin": float(margin) if played else np.nan,
                "played": played,
                "home_win": (1.0 if margin > 0 else 0.0 if margin < 0 else 0.5) if played else np.nan,
                "qb_change_home": r.get("qb_change_home", 0),
                "qb_change_away": r.get("qb_change_away", 0),
            }
        )
    return pd.DataFrame(recs)


@pytest.fixture(scope="module")
def games() -> pd.DataFrame:
    if not GAMES.exists():
        pytest.skip("games.csv not cached")
    return nflverse.load_games(path=GAMES)


@pytest.fixture(scope="module")
def default_run(games):
    model = EloModel()
    return model, model.run(games)


# ------------------------------------------------------------------ contract
def test_contract_names_and_defaults():
    for name in ("EloParams", "EloModel", "fit_elo", "save_params", "load_params"):
        assert hasattr(elo, name)
    p = EloParams()
    assert (p.k, p.hfa, p.mov, p.season_regress, p.rest_bonus) == (20.0, 55.0, True, 0.33, 25.0)
    assert (p.qb_change_penalty, p.playoff_mult, p.mean) == (0.0, 1.0, ELO_MEAN)
    with pytest.raises(ValueError):
        EloParams(k=0)
    with pytest.raises(ValueError):
        EloParams(season_regress=1.5)


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="missing required columns"):
        EloModel().run(pd.DataFrame({"game_id": ["x"]}))


# ------------------------------------------------------------ rating updates
def test_win_is_zero_sum():
    frame = make_games([{"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "margin": 7}])
    model = EloModel()
    out = model.run(frame)
    hist = model.elo_history()
    assert list(out.columns[-7:]) == list(elo.RUN_COLUMNS) + ["elo_home_post", "elo_away_post"]
    home_gain = out.loc[0, "elo_home_post"] - out.loc[0, "elo_home_pre"]
    away_gain = out.loc[0, "elo_away_post"] - out.loc[0, "elo_away_pre"]
    assert home_gain > 0
    assert home_gain == -away_gain
    kc = hist[hist["team"] == "KC"].iloc[0]
    buf = hist[hist["team"] == "BUF"].iloc[0]
    assert kc["elo_post"] - kc["elo_pre"] == home_gain
    assert buf["elo_post"] - buf["elo_pre"] == away_gain
    assert math.isclose(sum(model.ratings().values()), 2 * ELO_MEAN)


def test_loss_lowers_home_rating():
    frame = make_games([{"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "margin": -3}])
    model = EloModel()
    model.run(frame)
    r = model.ratings()
    assert r["KC"] < ELO_MEAN < r["BUF"]
    assert math.isclose(r["KC"] + r["BUF"], 2 * ELO_MEAN)


@pytest.mark.parametrize("margin", [10, -10, 0])
def test_update_matches_538_formula(margin):
    params = EloParams(k=20.0, hfa=55.0, mov=True)
    frame = make_games([{"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "margin": margin}])
    out = EloModel(params).run(frame)
    diff = 55.0
    prob = elo_prob(diff)
    score = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
    if margin == 0:
        mult = 1.0
    else:
        diff_winner = diff if margin > 0 else -diff
        mult = math.log(abs(margin) + 1) * 2.2 / (0.001 * diff_winner + 2.2)
    expected = params.k * mult * (score - prob)
    assert out.loc[0, "elo_diff"] == diff
    assert math.isclose(out.loc[0, "elo_prob"], prob)
    assert math.isclose(out.loc[0, "elo_home_post"] - ELO_MEAN, expected, rel_tol=1e-12, abs_tol=1e-12)
    # without the margin multiplier the update is plain k * (score - prob)
    out2 = EloModel(EloParams(k=20.0, hfa=55.0, mov=False)).run(frame)
    assert math.isclose(out2.loc[0, "elo_home_post"] - ELO_MEAN, params.k * (score - prob), abs_tol=1e-12)


def test_neutral_site_symmetry():
    rows = [
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "DEN", "margin": 21},
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "BUF", "away": "NYJ", "margin": -6},
        {"season": 2020, "week": 2, "game_date": "2020-09-20", "home": "KC", "away": "BUF", "location": "Neutral", "game_id": "n1"},
        {"season": 2020, "week": 2, "game_date": "2020-09-20", "home": "BUF", "away": "KC", "location": "Neutral", "game_id": "n2"},
        {"season": 2020, "week": 2, "game_date": "2020-09-20", "home": "KC", "away": "BUF", "game_id": "h1"},
        {"season": 2020, "week": 2, "game_date": "2020-09-20", "home": "BUF", "away": "KC", "game_id": "h2"},
    ]
    model = EloModel()
    out = model.run(make_games(rows)).set_index("game_id")
    assert out.loc["n1", "elo_prob"] > 0.5  # KC is rated higher after week 1
    assert math.isclose(out.loc["n1", "elo_prob"], 1.0 - out.loc["n2", "elo_prob"], abs_tol=1e-12)
    assert math.isclose(out.loc["n1", "elo_diff"], -out.loc["n2", "elo_diff"], abs_tol=1e-12)
    assert math.isclose(out.loc["n1", "elo_diff"], out.loc["n1", "elo_home_pre"] - out.loc["n1", "elo_away_pre"])
    # home field breaks the symmetry by exactly hfa on each side
    assert math.isclose(out.loc["h1", "elo_diff"] - out.loc["n1", "elo_diff"], model.params.hfa)
    assert math.isclose(out.loc["h2", "elo_diff"] - out.loc["n2", "elo_diff"], model.params.hfa)
    # predict() from the same ratings agrees with run() and is symmetric on a neutral field
    assert math.isclose(model.predict("KC", "BUF", neutral=True), out.loc["n1", "elo_prob"])
    assert math.isclose(model.predict("KC", "BUF", neutral=True), 1.0 - model.predict("BUF", "KC", neutral=True))


def test_predict_adjustments_from_fresh_model():
    p = EloParams(hfa=55.0, rest_bonus=25.0, qb_change_penalty=30.0, playoff_mult=1.2)
    model = EloModel(p)  # never run: every team sits at the mean
    assert math.isclose(model.predict("KC", "BUF"), elo_prob(55.0))
    assert math.isclose(model.predict("KC", "BUF", neutral=True), 0.5)
    assert math.isclose(model.predict("KC", "BUF", home_rest=14), elo_prob(80.0))
    assert math.isclose(model.predict("KC", "BUF", away_rest=10), elo_prob(30.0))
    assert math.isclose(model.predict("KC", "BUF", home_rest=14, away_rest=10), elo_prob(55.0))
    assert math.isclose(model.predict("KC", "BUF", qb_change_home=1), elo_prob(25.0))
    assert math.isclose(model.predict("KC", "BUF", qb_change_away=1), elo_prob(85.0))
    assert math.isclose(model.predict("KC", "BUF", playoff=True), elo_prob(55.0 * 1.2))
    assert math.isclose(model.predict("KC", "OAK"), model.predict("KC", "LV"))  # historical code canonicalised
    with pytest.raises(KeyError):
        model.predict("KC", "XXX")


def test_rest_and_playoff_flags_in_run():
    rows = [
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "home_rest": 14, "game_id": "rest_home"},
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "home_rest": 14, "away_rest": 11, "game_id": "rest_both"},
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "game_type": "DIV", "game_id": "playoff"},
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "qb_change_away": 1, "game_id": "qb"},
    ]
    p = EloParams(hfa=50.0, rest_bonus=20.0, playoff_mult=1.5, qb_change_penalty=10.0)
    out = EloModel(p).run(make_games(rows)).set_index("game_id")
    assert out.loc["rest_home", "elo_diff"] == 70.0
    assert out.loc["rest_both", "elo_diff"] == 50.0
    assert out.loc["playoff", "elo_diff"] == 75.0
    assert out.loc["qb", "elo_diff"] == 60.0
    assert (out["elo_spread"] == out["elo_diff"] / ELO_POINTS_PER_SPREAD_POINT).all()


def test_season_regression_and_new_franchise_synthetic():
    rows = [
        {"season": 2001, "week": 1, "game_date": "2001-09-09", "home": "KC", "away": "BUF", "margin": 30},
        {"season": 2002, "week": 1, "game_date": "2002-09-08", "home": "KC", "away": "HOU"},
    ]
    p = EloParams(season_regress=0.25)
    out = EloModel(p).run(make_games(rows))
    kc_post_2001 = out.loc[0, "elo_home_post"]
    assert kc_post_2001 > ELO_MEAN
    assert math.isclose(out.loc[1, "elo_home_pre"], ELO_MEAN + (kc_post_2001 - ELO_MEAN) * 0.75)
    assert out.loc[1, "elo_away_pre"] == ELO_MEAN + elo.NEW_FRANCHISE_OFFSET


def test_run_is_order_independent_and_keeps_input_order():
    rows = [
        {"season": 2020, "week": 1, "game_date": "2020-09-13", "home": "KC", "away": "BUF", "margin": 7},
        {"season": 2020, "week": 2, "game_date": "2020-09-20", "home": "BUF", "away": "KC", "margin": 3},
        {"season": 2020, "week": 3, "game_date": "2020-09-27", "home": "KC", "away": "BUF"},
    ]
    frame = make_games(rows)
    shuffled = frame.iloc[[2, 0, 1]]
    a = EloModel().run(frame)
    b = EloModel().run(shuffled)
    assert list(b.index) == [2, 0, 1]
    assert list(b["game_id"]) == list(shuffled["game_id"])
    for col in elo.RUN_COLUMNS:
        assert np.array_equal(a[col].to_numpy(), b.sort_index()[col].to_numpy())


# --------------------------------------------------------------- real data
@needs_cache
def test_real_data_predictions_in_unit_interval(games, default_run):
    model, out = default_run
    assert len(out) == len(games) and (out.index == games.index).all()
    for col in elo.RUN_COLUMNS:
        assert col in out.columns and out[col].notna().all()
    assert ((out["elo_prob"] > 0) & (out["elo_prob"] < 1)).all()
    unplayed = out[~out["played"]]
    assert (unplayed["season"] == 2026).any()
    assert (unplayed["elo_home_post"] == unplayed["elo_home_pre"]).all()
    assert (unplayed["elo_away_post"] == unplayed["elo_away_pre"]).all()
    hist = model.elo_history()
    assert len(hist) == 2 * len(games)
    assert list(hist.columns[:1]) == ["game_id"] and {"team", "elo_pre", "elo_post"} <= set(hist.columns)
    unplayed_hist = hist[hist["game_id"].isin(unplayed["game_id"])]
    assert (unplayed_hist["elo_post"] == unplayed_hist["elo_pre"]).all()
    ratings = model.ratings()
    assert len(ratings) == 32 and abs(np.mean(list(ratings.values())) - ELO_MEAN) < 1.0
    # the unplayed games are predicted from the final ratings
    row = unplayed.iloc[0]
    assert math.isclose(
        row["elo_prob"],
        model.predict(row["home_team_c"], row["away_team_c"], neutral=row["location"] == "Neutral",
                      home_rest=row["home_rest"], away_rest=row["away_rest"], playoff=row["game_type"] != "REG",
                      qb_change_home=row["qb_change_home"], qb_change_away=row["qb_change_away"]),
    )


@needs_cache
def test_real_data_new_franchise_and_season_regression(default_run):
    _, out = default_run
    hou = out[out["game_id"] == "2002_01_DAL_HOU"].iloc[0]
    assert hou["home_team_c"] == "HOU" and hou["elo_home_pre"] == ELO_MEAN - 100.0
    kc_2019 = out[(out["season"] == 2019) & ((out["home_team_c"] == "KC") | (out["away_team_c"] == "KC"))].iloc[-1]
    post = kc_2019["elo_home_post"] if kc_2019["home_team_c"] == "KC" else kc_2019["elo_away_post"]
    kc_2020 = out[(out["season"] == 2020) & ((out["home_team_c"] == "KC") | (out["away_team_c"] == "KC"))].iloc[0]
    pre = kc_2020["elo_home_pre"] if kc_2020["home_team_c"] == "KC" else kc_2020["elo_away_pre"]
    assert math.isclose(pre, ELO_MEAN + (post - ELO_MEAN) * (1 - 0.33))
    # neutral-site Super Bowls carry no home-field edge
    sb = out[(out["game_type"] == "SB") & (out["location"] == "Neutral")]
    assert len(sb) > 20
    assert np.allclose(sb["elo_diff"], sb["elo_home_pre"] - sb["elo_away_pre"])


@needs_cache
def test_no_lookahead_flipping_late_score(games, default_run):
    _, base = default_run
    flip_id = "2020_17_ARI_LA"
    idx = games.index[games["game_id"] == flip_id][0]
    flip_date = games.loc[idx, "game_date"]
    assert games.loc[idx, "played"]
    altered = games.copy()
    altered.loc[idx, "margin"] = -altered.loc[idx, "margin"]
    altered.loc[idx, "home_win"] = 1.0 - altered.loc[idx, "home_win"]
    out = EloModel().run(altered)
    before = games["game_date"] <= flip_date  # includes the flipped game's own pre-game prediction
    for col in ("elo_home_pre", "elo_away_pre", "elo_diff", "elo_prob", "elo_spread"):
        assert np.array_equal(base.loc[before, col].to_numpy(), out.loc[before, col].to_numpy()), col
    later = games["game_date"] > flip_date
    assert later.any()
    assert not np.array_equal(base.loc[later, "elo_prob"].to_numpy(), out.loc[later, "elo_prob"].to_numpy())
    # the flipped game's own post ratings moved, but nothing earlier did
    assert out.loc[idx, "elo_home_post"] != base.loc[idx, "elo_home_post"]


@needs_cache
def test_default_params_quality_2015_2024(default_run):
    _, out = default_run
    ev = out[out["played"] & out["season"].between(2015, 2024)]
    y = ev["home_win"].to_numpy()
    p = ev["elo_prob"].to_numpy()
    ll = elo._log_loss(y, p)
    acc = elo._accuracy(y, p)
    assert len(ev) > 2500
    # A 538-style Elo with the contract defaults measures 0.6355 / 64.3% here (tuned floor ~0.633);
    # the closing market measures 0.613 / 66.1% on the same rows.
    assert ll < 0.64
    assert acc > 0.62
    home_rate = float(np.mean(y))
    assert ll < elo._log_loss(y, np.full(len(y), home_rate)) - 0.03  # far better than the base rate
    assert ll > elo._log_loss(y, ev["market_prob"].to_numpy()) - 0.01  # and not suspiciously better than the market
    wide = out[out["played"] & out["season"].between(2003, 2024)]
    assert elo._log_loss(wide["home_win"].to_numpy(), wide["elo_prob"].to_numpy()) < 0.635


@needs_cache
def test_runtime_under_three_seconds(games):
    t = time.perf_counter()
    EloModel().run(games)
    assert time.perf_counter() - t < 3.0


# ---------------------------------------------------------------------- fit
@needs_cache
def test_fit_elo_tiny_grid(games):
    sub = games[games["season"] <= 2012]
    grid = {"k": [16, 24], "hfa": [45, 65]}
    best, results = fit_elo(sub, train_seasons=range(2010, 2013), grid=grid)
    assert isinstance(best, EloParams)
    assert best.k in {16.0, 20.0, 24.0} and best.hfa in {45.0, 55.0, 65.0}
    assert best.season_regress == 0.33 and best.mov is True  # untouched fields keep the start value
    for col in ("rank", "k", "hfa", "mov", "season_regress", "rest_bonus", "qb_change_penalty",
                "playoff_mult", "mean", "logloss", "brier", "accuracy", "n", "pass", "param"):
        assert col in results.columns
    n_train = int((sub["played"] & sub["season"].between(2010, 2012)).sum())
    assert (results["n"] == n_train).all()
    assert 3 <= len(results) <= 9
    assert results["logloss"].is_monotonic_increasing
    assert results.duplicated(subset=["k", "hfa"]).sum() == 0
    top = results.iloc[0]
    assert (top["k"], top["hfa"]) == (best.k, best.hfa)
    assert 0.55 < top["logloss"] < 0.70
    with pytest.raises(ValueError):
        fit_elo(sub, range(2010, 2013), grid={"not_a_param": [1]})
    with pytest.raises(ValueError):
        fit_elo(sub, range(2010, 2013), grid=grid, metric="rmse")
    best_acc, res_acc = fit_elo(sub, range(2010, 2013), grid={"k": [16, 24]}, metric="accuracy")
    assert res_acc["accuracy"].is_monotonic_decreasing


# ------------------------------------------------------------------ persist
def test_save_and_load_params_roundtrip(tmp_path):
    params = EloParams(k=24, hfa=45, mov=False, season_regress=0.5, rest_bonus=15, qb_change_penalty=40, playoff_mult=1.2)
    path = tmp_path / "nested" / "elo_params.json"
    save_params(params, path)
    loaded = load_params(path)
    assert loaded == params
    assert isinstance(loaded.k, float) and loaded.mov is False
    data = json.loads(path.read_text())
    assert set(data) == {"k", "hfa", "mov", "season_regress", "rest_bonus", "qb_change_penalty", "playoff_mult", "mean"}
    data["typo"] = 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown"):
        load_params(path)
