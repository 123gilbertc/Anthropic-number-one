import json
import math

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit, logit

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import nflverse
from nfl_edge.models import calibration as cal
from nfl_edge.models import ensemble as ens

GAMES = CACHE_DIR / "games.csv"
needs_games = pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")

TRUE_COEF = {"p1_logit": 1.3, "p2_logit": 0.6, "noise_logit": 0.0}
TRUE_INTERCEPT = 0.2


def synthetic_games(n: int = 20000, seed: int = 0) -> pd.DataFrame:
    """Component probabilities whose logits combine with known weights into the true P(home win)."""
    rng = np.random.default_rng(seed)
    z1 = np.clip(rng.normal(0.0, 1.2, n), -4.5, 4.5)
    z2 = np.clip(rng.normal(0.0, 1.0, n), -4.5, 4.5)
    z3 = np.clip(rng.normal(0.0, 1.0, n), -4.5, 4.5)
    true = expit(TRUE_INTERCEPT + TRUE_COEF["p1_logit"] * z1 + TRUE_COEF["p2_logit"] * z2)
    y = (rng.uniform(size=n) < true).astype(float)
    return pd.DataFrame(
        {
            "season": 2000 + (np.arange(n) * 20 // n),
            "played": True,
            "home_win": y,
            "p1": expit(z1),
            "p2": expit(z2),
            "noise": expit(z3),
            "true_prob": true,
        }
    )


@pytest.fixture(scope="module")
def games():
    if not GAMES.exists():
        pytest.skip("games.csv not cached")
    return nflverse.load_games(path=GAMES)


@pytest.fixture(scope="module")
def wf_market(games):
    return ens.walk_forward(games, ["market_prob"], 2012, 2024)


# ------------------------------------------------------------------ add_logits
def test_add_logits_clips_and_copies():
    df = pd.DataFrame({"a": [0.001, 0.5, 0.999, np.nan], "b": [0.25, 0.75, 0.5, 0.5]})
    out = ens.add_logits(df, ["a", "b"])
    assert "a_logit" not in df.columns  # input untouched
    assert list(out.columns) == ["a", "b", "a_logit", "b_logit"]
    assert math.isclose(out["a_logit"][0], logit(0.01)) and math.isclose(out["a_logit"][2], logit(0.99))
    assert out["a_logit"][1] == 0.0 and np.isnan(out["a_logit"][3])
    assert math.isclose(out["b_logit"][1], math.log(3.0))
    with pytest.raises(KeyError):
        ens.add_logits(df, ["missing"])
    assert ens.logit_column("market_prob") == "market_prob_logit"
    assert ens.logit_column("elo_diff_logit") == "elo_diff_logit"


def test_add_logits_rejects_non_probability_columns():
    """Regression: a raw score must raise, not be clipped to [0.01, 0.99] into a sign feature."""
    df = pd.DataFrame(
        {
            "score": [-27.0, 3.5, 27.0, np.nan],  # spread-like
            "big": [1500.0, 1450.0, 1600.0, 1550.0],  # Elo-like
            "tiny": [0.2, 0.5, -1e-9, 0.7],  # barely negative
            "over": [0.2, 0.5, 1.0 + 1e-9, 0.7],  # barely above one
            "inf": [0.2, np.inf, 0.5, 0.5],  # inf is out of range, not "missing"
            "edge": [0.0, 1.0, 0.5, np.nan],  # boundaries are legal probabilities
            "empty": [np.nan, np.nan, np.nan, np.nan],  # all missing is legal
        }
    )
    for col in ("score", "big", "tiny", "over", "inf"):
        with pytest.raises(ValueError, match=col):
            ens.add_logits(df, [col])
    # the message points at the escape hatch
    with pytest.raises(ValueError, match="score_logit"):
        ens.add_logits(df, ["score"])
    # a bad column anywhere in the list is rejected and the input is untouched
    with pytest.raises(ValueError):
        ens.add_logits(df, ["edge", "score"])
    assert "edge_logit" not in df.columns and "score_logit" not in df.columns
    ok = ens.add_logits(df, ["edge", "empty"])
    assert math.isclose(ok["edge_logit"][0], logit(0.01)) and math.isclose(ok["edge_logit"][1], logit(0.99))
    assert ok["edge_logit"][2] == 0.0 and np.isnan(ok["edge_logit"][3])
    assert ok["empty_logit"].isna().all()
    # every entry point that builds the design matrix refuses the raw score...
    games = pd.DataFrame({"season": [2000] * 4, "played": True, "home_win": [1.0, 0.0, 1.0, 0.0], "score": df["score"]})
    with pytest.raises(ValueError, match="score"):
        ens.LogisticStacker(["score"]).fit(games)
    with pytest.raises(ValueError, match="score"):
        ens.fit_final(games, ["score"])
    with pytest.raises(ValueError, match="score"):
        ens.walk_forward(games, ["score"], 2000, min_train_seasons=0)
    fitted = ens.LogisticStacker(["p"]).fit(synthetic_games(n=500, seed=4).rename(columns={"p1": "p"}))
    with pytest.raises(ValueError, match="'p'"):
        fitted.predict_proba(pd.DataFrame({"p": [2.0, 0.5]}))
    # ...while the same score under a *_logit name is stacked as-is (no clipping, no check)
    games["score_logit"] = games["score"] / 7.0
    model = ens.LogisticStacker(["score_logit"], l2=1.0).fit(games)
    assert model.design_columns == ["score_logit"] and model.n_fit_ == 3
    z = model.decision_function(games)
    assert np.allclose(z[:3], model.coef_["score_logit"] * games["score_logit"][:3] + model.intercept_)
    assert np.isnan(z[3])


@needs_games
def test_raw_score_feature_rejected_on_real_games(games):
    """Regression for the reviewer repro: LogisticStacker(['spread_line']).fit(games) must not 'succeed'."""
    for raw in ("spread_line", "home_moneyline", "margin"):
        with pytest.raises(ValueError, match=raw):
            ens.LogisticStacker([raw]).fit(games)
        with pytest.raises(ValueError, match=raw):
            ens.walk_forward(games, ["market_prob", raw], 2015, 2016)
    # escape hatch: a scaled spread stacked as a logit column is a legitimate, competitive feature
    df = games.assign(spread_line_logit=games["spread_line"] / 7.0)
    wf = ens.walk_forward(df, ["spread_line_logit"], 2015, 2024)
    played = wf[wf["played"]]
    assert played["fair_prob"].notna().all()
    for season, c in wf.attrs["coefs"].items():
        assert c["spread_line_logit"] > 0.5, (season, c)  # spread points (scaled) map to a real slope
    ll_spread = cal.log_loss(played["home_win"], played["fair_prob"])
    ll_market = cal.log_loss(played["home_win"], played["market_prob"])
    assert abs(ll_spread - ll_market) < 0.01


# ------------------------------------------------------------------ LogisticStacker
def test_recovers_known_coefficients_and_ignores_noise():
    df = synthetic_games(n=20000, seed=0)
    model = ens.LogisticStacker(["p1", "p2", "noise"], l2=1.0).fit(df)
    assert model.converged_ and model.n_fit_ == 20000
    assert set(model.coef_) == {"p1_logit", "p2_logit", "noise_logit"}
    for k, v in TRUE_COEF.items():
        assert abs(model.coef_[k] - v) < 0.05, (k, model.coef_[k])
    assert abs(model.coef_["noise_logit"]) < 0.1
    assert abs(model.intercept_ - TRUE_INTERCEPT) < 0.05
    p = model.predict_proba(df)
    assert p.shape == (20000,) and np.all((p >= 1e-6) & (p <= 1 - 1e-6))
    assert np.mean(np.abs(p - df["true_prob"])) < 0.01
    assert cal.log_loss(df["home_win"], p) < cal.log_loss(df["home_win"], df["p1"]) - 0.02


def test_fit_is_reproducible_to_1e6():
    df = synthetic_games(n=5000, seed=1)
    a = ens.LogisticStacker(["p1", "p2", "noise"]).fit(df)
    b = ens.LogisticStacker(["p1", "p2", "noise"]).fit(df.sample(frac=1.0, random_state=3))
    c = ens.LogisticStacker(["p1", "p2", "noise"]).fit(df.iloc[::-1])
    for other in (b, c):
        for k in a.coef_:
            assert abs(a.coef_[k] - other.coef_[k]) < 1e-6
        assert abs(a.intercept_ - other.intercept_) < 1e-6
    # gradient of the penalised objective vanishes at the reported optimum
    X = np.column_stack([logit(np.clip(df[c], 0.01, 0.99)) for c in ("p1", "p2", "noise")])
    w = np.array([a.coef_[k] for k in a.design_columns])
    z = X @ w + a.intercept_
    grad_w = X.T @ (expit(z) - df["home_win"].to_numpy()) + a.l2 * w
    grad_b = np.sum(expit(z) - df["home_win"].to_numpy())
    assert np.max(np.abs(grad_w)) < 1e-6 and abs(grad_b) < 1e-6


def test_l2_intercept_ties_and_prediction_edge_cases():
    df = synthetic_games(n=3000, seed=2)
    weak = ens.LogisticStacker(["p1"], l2=1.0).fit(df)
    strong = ens.LogisticStacker(["p1"], l2=5000.0).fit(df)
    assert abs(strong.coef_["p1_logit"]) < abs(weak.coef_["p1_logit"])
    no_b = ens.LogisticStacker(["p1"], use_intercept=False).fit(df)
    assert no_b.intercept_ == 0.0 and abs(no_b.coef_["p1_logit"] - weak.coef_["p1_logit"]) < 0.1
    # ties are legal outcomes
    tied = df.copy()
    tied.loc[tied.index[:50], "home_win"] = 0.5
    ens.LogisticStacker(["p1"]).fit(tied)
    # NaN feature -> NaN prediction; extreme logits are clipped
    test = pd.DataFrame({"p1": [0.5, np.nan, 0.99, 0.01]})
    p = weak.predict_proba(test)
    assert np.isnan(p[1]) and np.isfinite(p[[0, 2, 3]]).all()
    hot = ens.LogisticStacker(["p1"])
    hot.coef_ = {"p1_logit": 50.0}
    hot.intercept_ = 0.0
    ph = hot.predict_proba(test)
    assert ph[2] == 1 - 1e-6 and ph[3] == 1e-6
    with pytest.raises(RuntimeError):
        ens.LogisticStacker(["p1"]).predict_proba(test)
    with pytest.raises(KeyError):
        weak.predict_proba(pd.DataFrame({"p2": [0.5]}))
    with pytest.raises(ValueError):
        ens.LogisticStacker([])
    with pytest.raises(ValueError):
        ens.LogisticStacker(["p1", "p1"])
    with pytest.raises(ValueError):
        ens.LogisticStacker(["p1"], l2=-1.0)
    with pytest.raises(ValueError):
        ens.LogisticStacker(["p1"]).fit(df.assign(home_win=np.nan))
    with pytest.raises(ValueError):
        ens.LogisticStacker(["p1"]).fit(df.assign(home_win=2.0))


# ------------------------------------------------------------------ walk_forward on real games
@needs_games
def test_walk_forward_market_only_tracks_the_market(wf_market, games):
    wf = wf_market
    assert "fair_prob" in wf.columns and "market_prob_logit" in wf.columns
    assert wf["season"].min() == 2012 and wf["season"].max() == 2024
    assert len(wf) == games["season"].between(2012, 2024).sum()
    played = wf[wf["played"]]
    assert played["fair_prob"].notna().all()
    assert (played["fair_prob"] - played["market_prob"]).abs().mean() < 0.02
    assert (played["fair_prob"] - played["market_prob"]).abs().max() < 0.05
    coefs = wf.attrs["coefs"]
    assert sorted(coefs) == list(range(2012, 2025))
    for season, c in coefs.items():
        assert set(c) == {"market_prob_logit", "intercept"}
        assert 0.9 <= c["market_prob_logit"] <= 1.15, (season, c)
        assert abs(c["intercept"]) < 0.1
    n_train = wf.attrs["n_train"]
    assert all(n_train[s] < n_train[s + 1] for s in range(2012, 2024))
    assert n_train[2012] == int((games["played"] & (games["season"] < 2012)).sum())
    assert wf.attrs["features"] == ["market_prob"] and wf.attrs["l2"] == 1.0


@needs_games
def test_walk_forward_market_only_matches_market_logloss(wf_market):
    played = wf_market[wf_market["played"]]
    y = played["home_win"].to_numpy()
    res = cal.paired_bootstrap(y, played["fair_prob"], played["market_prob"], n=300, seed=0)
    # honest expectation: re-scaling the closing line cannot beat it by much (nor lose much)
    assert abs(res["diff"]) < 0.003
    assert res["ci_lo"] < 0 < res["ci_hi"] or res["ci_hi"] < 0


@needs_games
def test_walk_forward_no_leakage(games, wf_market):
    """Flipping a 2024 outcome must not move any prediction for 2023 (or 2024) rows."""
    flipped = games.copy()
    target = flipped.index[(flipped["season"] == 2024) & flipped["played"]][0]
    flipped.loc[target, "home_win"] = 1.0 - flipped.loc[target, "home_win"]
    flipped.loc[target, "margin"] = -flipped.loc[target, "margin"]
    wf2 = ens.walk_forward(flipped, ["market_prob"], 2023, 2025)
    for season in (2023, 2024):
        a = wf_market.loc[wf_market["season"] == season, "fair_prob"].to_numpy()
        b = wf2.loc[wf2["season"] == season, "fair_prob"].to_numpy()
        assert np.array_equal(a, b, equal_nan=True)
        assert wf_market.attrs["coefs"][season] == wf2.attrs["coefs"][season]
    # ...while 2025, whose fit includes the flipped game, does change
    wf25 = ens.walk_forward(games, ["market_prob"], 2025, 2025)
    a = wf25["fair_prob"].to_numpy()
    b = wf2.loc[wf2["season"] == 2025, "fair_prob"].to_numpy()
    assert np.nanmax(np.abs(a - b)) > 1e-6


@needs_games
def test_walk_forward_multi_feature_and_noise(games):
    rng = np.random.default_rng(5)
    df = games.copy()
    df["noise_prob"] = rng.uniform(0.05, 0.95, len(df))
    wf = ens.walk_forward(df, ["market_prob", "home_spread_prob", "noise_prob"], 2015, 2024)
    for season, c in wf.attrs["coefs"].items():
        assert abs(c["noise_prob_logit"]) < 0.1, (season, c)
        assert c["market_prob_logit"] + c["home_spread_prob_logit"] > 0.9
    played = wf[wf["played"]]
    assert abs(cal.log_loss(played["home_win"], played["fair_prob"]) - cal.log_loss(played["home_win"], played["market_prob"])) < 0.003


@needs_games
def test_walk_forward_guards(games):
    with pytest.raises(ValueError):
        ens.walk_forward(games, ["market_prob"], 2001, 2001, min_train_seasons=5)
    early = ens.walk_forward(games, ["market_prob"], 2001, 2001, min_train_seasons=2)
    assert early["fair_prob"].notna().all() and list(early.attrs["coefs"]) == [2001]
    with pytest.raises(ValueError):
        ens.walk_forward(games, ["market_prob"], 2020, 2019)
    with pytest.raises(KeyError):
        ens.walk_forward(games, ["not_a_column"], 2015)
    with pytest.raises(KeyError):
        ens.walk_forward(games.drop(columns=["season"]), ["market_prob"], 2015)
    # default last_test is the latest season: live rows without a line get NaN, others a prediction
    live = ens.walk_forward(games, ["market_prob"], 2026)
    assert live["season"].unique().tolist() == [2026]
    assert live.loc[live["market_prob"].isna(), "fair_prob"].isna().all()
    assert live.loc[live["market_prob"].notna(), "fair_prob"].notna().all()


# ------------------------------------------------------------------ fit_final / persistence
@needs_games
def test_fit_final_and_save_load_roundtrip(games, tmp_path):
    model = ens.fit_final(games, ["market_prob"], l2=1.0)
    assert model.n_fit_ == int(games["played"].sum())
    assert 0.9 <= model.coef_["market_prob_logit"] <= 1.15
    path = ens.save_model(model, tmp_path / "sub" / "stacker.json")
    assert path.exists()
    raw = json.loads(path.read_text())
    assert raw["features"] == ["market_prob"] and raw["format_version"] == 1
    loaded = ens.load_model(path)
    assert loaded.features == model.features and loaded.l2 == model.l2 and loaded.use_intercept == model.use_intercept
    assert loaded.coef_ == model.coef_ and loaded.intercept_ == model.intercept_
    assert loaded.n_fit_ == model.n_fit_ and loaded.converged_ == model.converged_
    sample = games.tail(300)
    assert np.array_equal(model.predict_proba(sample), loaded.predict_proba(sample), equal_nan=True)
    with pytest.raises(ValueError):
        ens.save_model(ens.LogisticStacker(["market_prob"]), tmp_path / "unfit.json")
    bad = dict(raw, format_version=99)
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        ens.load_model(tmp_path / "bad.json")
    wrong = dict(raw, coef_={"elo_prob_logit": 1.0})
    (tmp_path / "wrong.json").write_text(json.dumps(wrong))
    with pytest.raises(ValueError):
        ens.load_model(tmp_path / "wrong.json")


def test_save_load_roundtrip_synthetic(tmp_path):
    df = synthetic_games(n=2000, seed=9)
    model = ens.fit_final(df, ["p1", "p2"], l2=2.0)
    loaded = ens.load_model(ens.save_model(model, tmp_path / "m.json"))
    assert loaded.to_dict() == model.to_dict()
    assert np.array_equal(model.predict_proba(df), loaded.predict_proba(df))
