import math

import numpy as np
import pytest
from scipy.special import expit

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import nflverse
from nfl_edge.models import calibration as cal

GAMES = CACHE_DIR / "games.csv"


def _synthetic(n: int = 50000, seed: int = 0, noise: float = 0.0):
    """Calibrated probabilities p, outcomes y ~ Bernoulli(p), plus a noisy competitor."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.2, n)
    p = expit(z)
    y = (rng.uniform(size=n) < p).astype(float)
    p_noisy = expit(z + rng.normal(0.0, noise, n)) if noise > 0 else p.copy()
    return y, p, p_noisy


# ------------------------------------------------------------------ scoring rules
def test_log_loss_known_values():
    assert math.isclose(cal.log_loss([1, 0], [0.5, 0.5]), math.log(2.0))
    assert math.isclose(cal.log_loss([1.0], [0.9]), -math.log(0.9))
    assert math.isclose(cal.log_loss([0.0], [0.9]), -math.log(0.1))
    # tie counts half of each side
    assert math.isclose(cal.log_loss([0.5], [0.8]), -0.5 * (math.log(0.8) + math.log(0.2)))
    # clipping at 1e-6 keeps a confident miss finite
    assert math.isclose(cal.log_loss([1.0], [0.0]), -math.log(1e-6))
    assert math.isclose(cal.log_loss([0.0], [1.0]), -math.log(1e-6))
    assert cal.log_loss([1.0], [1.0]) < 2e-6


def test_brier_and_accuracy():
    assert cal.brier([1, 0], [1.0, 0.0]) == 0.0
    assert math.isclose(cal.brier([1, 0], [0.5, 0.5]), 0.25)
    assert math.isclose(cal.brier([0.5], [0.5]), 0.0)
    assert math.isclose(cal.accuracy([1, 0, 1, 0], [0.7, 0.3, 0.2, 0.6]), 0.5)
    assert math.isclose(cal.accuracy([1, 0.5], [0.9, 0.9]), 0.75)  # tie counts half
    assert math.isclose(cal.accuracy([1.0], [0.5]), 1.0)  # p = 0.5 predicts home


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        cal.log_loss([1, 0], [0.5])
    with pytest.raises(ValueError):
        cal.log_loss([1, np.nan], [0.5, 0.5])
    with pytest.raises(ValueError):
        cal.brier([1, 0], [0.5, np.nan])
    with pytest.raises(ValueError):
        cal.accuracy([2, 0], [0.5, 0.5])
    with pytest.raises(ValueError):
        cal.ece([1, 0], [1.5, 0.5])
    with pytest.raises(ValueError):
        cal.log_loss([], [])


# ------------------------------------------------------------------ calibration
def test_calibration_table_and_ece_on_calibrated_data():
    y, p, _ = _synthetic()
    tbl = cal.calibration_table(y, p, bins=10)
    assert list(tbl.columns) == ["bin", "bin_lo", "bin_hi", "p_mean", "y_mean", "n", "gap"]
    assert len(tbl) == 10 and tbl["n"].sum() == len(y)
    assert np.allclose(tbl["bin_lo"], np.arange(10) / 10) and np.allclose(tbl["bin_hi"], np.arange(1, 11) / 10)
    assert (tbl["p_mean"] >= tbl["bin_lo"]).all() and (tbl["p_mean"] <= tbl["bin_hi"]).all()
    assert np.allclose(tbl["gap"], tbl["y_mean"] - tbl["p_mean"])
    e = cal.ece(y, p, bins=10)
    assert e < 0.01
    assert math.isclose(e, float((tbl["n"] * tbl["gap"].abs()).sum() / tbl["n"].sum()))
    # a systematic +0.1 shift shows up as ~0.1 ECE
    shifted = np.clip(p + 0.1, 0.0, 1.0)
    assert 0.07 < cal.ece(y, shifted, bins=10) < 0.11
    # empty buckets are listed with n = 0 and NaN means
    tbl2 = cal.calibration_table([1, 0], [0.55, 0.45], bins=4)
    assert tbl2["n"].tolist() == [0, 1, 1, 0]
    assert tbl2["p_mean"].isna().tolist() == [True, False, False, True]
    # p = 1.0 falls in the last bucket
    assert cal.calibration_table([1.0], [1.0], bins=5)["n"].tolist() == [0, 0, 0, 0, 1]


# ------------------------------------------------------------------ model comparison
def test_paired_bootstrap_detects_better_model():
    y, p_true, p_noisy = _synthetic(n=20000, seed=1, noise=0.8)
    res = cal.paired_bootstrap(y, p_true, p_noisy, n=500, seed=0)
    for k in ("diff", "ci_lo", "ci_hi", "p_a_better", "logloss_a", "logloss_b", "n", "n_boot"):
        assert k in res
    assert res["n"] == len(y) and res["n_boot"] == 500
    assert math.isclose(res["diff"], cal.log_loss(y, p_true) - cal.log_loss(y, p_noisy))
    assert res["diff"] < 0 and res["ci_hi"] < 0 and res["p_a_better"] > 0.99
    assert res["ci_lo"] <= res["diff"] <= res["ci_hi"]
    # reversed roles flip the sign
    rev = cal.paired_bootstrap(y, p_noisy, p_true, n=500, seed=0)
    assert math.isclose(rev["diff"], -res["diff"]) and rev["p_a_better"] < 0.01
    # deterministic in the seed
    again = cal.paired_bootstrap(y, p_true, p_noisy, n=500, seed=0)
    assert again == res
    # identical models: zero difference, interval degenerate at zero
    same = cal.paired_bootstrap(y, p_true, p_true, n=100, seed=0)
    assert same["diff"] == 0.0 and same["ci_lo"] == 0.0 and same["ci_hi"] == 0.0
    with pytest.raises(ValueError):
        cal.paired_bootstrap(y, p_true, p_noisy[:-1])


def test_summarize_columns_and_common_support():
    y, p, p_noisy = _synthetic(n=5000, seed=2, noise=0.5)
    p_partial = p.copy()
    p_partial[:100] = np.nan
    tbl = cal.summarize(y, {"true": p, "noisy": p_noisy, "partial": p_partial})
    assert list(tbl.columns) == ["model", "logloss", "brier", "acc", "ece", "n"]
    assert tbl["model"].tolist() == ["true", "noisy", "partial"]
    assert (tbl["n"] == 4900).all()  # paired: rows dropped for every model
    by = tbl.set_index("model")
    assert by.loc["true", "logloss"] < by.loc["noisy", "logloss"]
    assert math.isclose(by.loc["true", "logloss"], by.loc["partial", "logloss"])
    assert math.isclose(by.loc["true", "logloss"], cal.log_loss(y[100:], p[100:]))
    with pytest.raises(ValueError):
        cal.summarize(y, {})
    with pytest.raises(ValueError):
        cal.summarize(y, {"short": p[:-1]})


@pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")
def test_real_market_baseline_scores():
    games = nflverse.load_games(path=GAMES)
    g = games[games["played"] & games["season"].between(2012, 2024)]
    y = g["home_win"].to_numpy()
    tbl = cal.summarize(y, {"market": g["market_prob"].to_numpy(), "spread": g["home_spread_prob"].to_numpy()})
    row = tbl.set_index("model").loc["market"]
    assert 0.58 < row["logloss"] < 0.64
    assert 0.20 < row["brier"] < 0.22
    assert 0.62 < row["acc"] < 0.70
    assert row["ece"] < 0.03
    assert row["n"] == len(g)
    res = cal.paired_bootstrap(y, g["market_prob"], g["home_spread_prob"], n=300, seed=0)
    assert abs(res["diff"]) < 0.01
