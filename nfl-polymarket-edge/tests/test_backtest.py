"""Tests for nfl_edge.backtest.engine (offline; synthetic games plus the cached nflverse data)."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from nfl_edge.backtest import (
    BET_COLUMNS,
    SETTLEMENT_LAG,
    BacktestConfig,
    BacktestResult,
    backtest_snapshots,
    backtest_vs_closing,
    bootstrap_roi,
    max_drawdown,
    nfl_season_week,
    report_markdown,
)
from nfl_edge.config import CACHE_DIR
from nfl_edge.data import nflverse
from nfl_edge.odds import american_to_decimal, devig, prob_to_american

GAMES = CACHE_DIR / "games.csv"
VIG = 0.045
GAMES_PER_WEEK = 16
WEEKS_PER_SEASON = 18
MULT = dict(devig="multiplicative")   # exact-CLV assertions compare against load_games' multiplicative columns


# ------------------------------------------------------------------ synthetic data helpers
def synthetic_games(
    p_grid: list[float],
    k_per_group: int,
    market_fn,
    vig: float = VIG,
    first_season: int = 2015,
    seed: int = 0,
) -> pd.DataFrame:
    """Played games whose TRUE home-win probability is ``p`` for each bucket in ``p_grid``.

    Every bucket has exactly ``round(k * p)`` home wins, so the expected profit of any rule that
    treats a bucket uniformly is realised exactly.  The closing line prices ``market_fn(p)`` with a
    symmetric overround of ``1 + vig``.  Games are shuffled across seasons/weeks and pushed through
    ``add_derived_columns`` so they carry exactly the ``load_games`` columns.
    """
    rng = np.random.default_rng(seed)
    p_true, home_win = [], []
    for p in p_grid:
        k_win = int(round(k_per_group * p))
        p_true += [p] * k_per_group
        home_win += [1.0] * k_win + [0.0] * (k_per_group - k_win)
    p_true = np.array(p_true)
    home_win = np.array(home_win)
    perm = rng.permutation(len(p_true))
    p_true, home_win = p_true[perm], home_win[perm]

    idx = np.arange(len(p_true))
    season = first_season + idx // (GAMES_PER_WEEK * WEEKS_PER_SEASON)
    week = (idx // GAMES_PER_WEEK) % WEEKS_PER_SEASON + 1
    p_mkt = np.array([market_fn(p) for p in p_true])
    raw_h, raw_a = p_mkt * (1 + vig), (1 - p_mkt) * (1 + vig)
    df = pd.DataFrame(
        {
            "game_id": [f"{s}_{w:02d}_A{i}_H{i}" for s, w, i in zip(season, week, idx)],
            "season": season,
            "game_type": "REG",
            "week": week,
            "gameday": [f"{s}-09-{(w - 1) % 28 + 1:02d}" for s, w in zip(season, week)],
            "home_team": "KC",
            "away_team": "BUF",
            "home_score": np.where(home_win == 1.0, 1, 0),
            "away_score": np.where(home_win == 0.0, 1, 0),
            "home_moneyline": [prob_to_american(p) for p in raw_h],
            "away_moneyline": [prob_to_american(p) for p in raw_a],
            "spread_line": 0.0,
            "home_qb_name": "QB1",
            "away_qb_name": "QB2",
            "p_true": p_true,
        }
    )
    return nflverse.add_derived_columns(df)


def small_games(rows: list[dict], season: int = 2015) -> pd.DataFrame:
    """Hand-built played games: each row has week, home_win (1/0/0.5), home_ml, away_ml, prob."""
    df = pd.DataFrame(rows)
    df["season"] = season
    df["game_type"] = "REG"
    df["game_id"] = [f"{season}_{w:02d}_A_H{i}" for i, w in enumerate(df["week"])]
    df["gameday"] = [f"{season}-09-{w:02d}" for w in df["week"]]
    df["home_team"], df["away_team"] = "KC", "BUF"
    df["home_score"] = np.where(df["home_win"] == 1.0, 1, 0)
    df["away_score"] = np.where(df["home_win"] == 0.0, 1, 0)
    df["home_moneyline"], df["away_moneyline"] = df["home_ml"], df["away_ml"]
    df["spread_line"] = 0.0
    df["home_qb_name"], df["away_qb_name"] = "QB1", "QB2"
    return nflverse.add_derived_columns(df.drop(columns=["home_win"]))


def expected_kelly_stakes(bets: pd.DataFrame, cfg: BacktestConfig) -> np.ndarray:
    """Closing-path Kelly stakes recomputed independently: min(f * full, cap) scaled per week to the
    exposure cap, times the week-start bankroll carried in the frame."""
    full = bets["ev"] / (bets["dec_odds"] - 1)
    frac = np.minimum(cfg.kelly_fraction * np.clip(full, 0, None), cfg.max_stake)
    week_total = frac.groupby([bets["season"], bets["week"]]).transform("sum")
    scale = np.minimum(1.0, cfg.max_weekly_exposure / week_total.replace(0.0, np.nan)).fillna(1.0)
    return (frac * scale * bets["bankroll_start"]).to_numpy()


@pytest.fixture(scope="module")
def mispriced():
    """Truth on {.35,.45,.55,.65}; the market only sees half the signal."""
    return synthetic_games([0.35, 0.45, 0.55, 0.65], 500, lambda p: 0.5 + 0.5 * (p - 0.5))


@pytest.fixture(scope="module")
def efficient():
    """Truth on {.3,...,.7}; the market prices the truth exactly (plus vig)."""
    return synthetic_games([0.3, 0.4, 0.5, 0.6, 0.7], 200, lambda p: p)


# ------------------------------------------------------------------ backtest_vs_closing
def test_truth_vs_wrong_odds_is_profitable(mispriced):
    res = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02, **MULT))
    s = res.summary
    assert isinstance(res, BacktestResult)
    # only the .35 / .65 buckets clear 2% EV (the .45/.55 ones have EV ~ 0.25%)
    assert s["n_bets"] == 1000
    assert set(res.bets["side"]) == {"home", "away"}
    expected_roi = 0.65 / (0.575 * (1 + VIG)) - 1.0
    assert s["roi_flat"] == pytest.approx(expected_roi, abs=1e-9)
    assert s["roi_ci_lo"] > 0.0 < s["roi_ci_hi"]
    assert s["roi_ci_lo"] < expected_roi < s["roi_ci_hi"]
    assert s["avg_model_clv"] == pytest.approx(0.65 - 0.575, abs=1e-9)
    assert s["roi_kelly"] > 0 and s["final_bankroll_kelly"] > 1.0
    assert s["final_bankroll_kelly"] == pytest.approx(1.0 + res.bets["profit_kelly"].sum())
    assert s["hit_rate"] == pytest.approx(0.65, abs=1e-9)
    assert s["weekly_sharpe"] > 0
    assert 0.0 <= s["max_drawdown_flat"] < 0.5 and 0.0 <= s["max_drawdown_kelly"] < 0.5
    assert list(res.by_season.columns[:1]) == ["season"]
    assert res.by_season["n_bets"].sum() == 1000
    assert (res.by_season["roi_flat"] > 0).all()
    # the Shin default only moves the CLV baseline a little (longshot/favourite correction)
    shin = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02))
    assert shin.summary["n_bets"] == 1000 and shin.summary["roi_flat"] == pytest.approx(expected_roi, abs=1e-9)
    assert 0.0 < abs(shin.summary["avg_model_clv"] - (0.65 - 0.575)) < 0.01


def test_market_prob_never_beats_the_vig(efficient):
    cfg = BacktestConfig(min_edge=0.02)
    res = backtest_vs_closing(efficient, "market_prob", cfg)
    assert res.summary["n_bets"] == 0
    assert res.bets.empty and list(res.bets.columns[: len(BET_COLUMNS)]) == list(BET_COLUMNS)
    assert res.by_season.empty and list(res.by_season.columns[:1]) == ["season"]
    assert math.isnan(res.summary["roi_flat"]) and res.summary["final_bankroll_kelly"] == 1.0
    assert "No bets" in report_markdown(res)

    res = backtest_vs_closing(efficient, "market_prob", BacktestConfig(min_edge=-1, **MULT))
    s = res.summary
    assert s["n_bets"] == len(efficient) == s["n_games"]
    expected = 1.0 / (1 + VIG) - 1.0
    assert s["roi_flat"] == pytest.approx(expected, abs=1e-9)   # exactly -vig/(1+vig)
    assert s["roi_ci_lo"] < expected < s["roi_ci_hi"]
    assert s["avg_model_clv"] == pytest.approx(0.0, abs=1e-12)
    # Kelly never stakes a negative-EV bet
    assert (res.bets["stake_kelly"] == 0).all() and math.isnan(s["roi_kelly"])
    assert s["final_bankroll_kelly"] == 1.0
    # against the (default) Shin baseline the multiplicative market_prob shows a small, non-zero CLV
    shin = backtest_vs_closing(efficient, "market_prob", BacktestConfig(min_edge=-1))
    assert shin.summary["avg_model_clv"] != 0.0 and abs(shin.summary["avg_model_clv"]) < 0.01


def test_bet_columns_and_odds_consistency(mispriced):
    cfg = BacktestConfig(min_edge=0.02, **MULT)
    res = backtest_vs_closing(mispriced, "p_true", cfg)
    b = res.bets
    assert list(b.columns[: len(BET_COLUMNS)]) == list(BET_COLUMNS)
    assert not any(c.startswith("_") for c in b.columns)
    g = mispriced.set_index("game_id").loc[b["game_id"]]
    home = (b["side"] == "home").to_numpy()
    ml = np.where(home, g["home_moneyline"], g["away_moneyline"])
    assert np.allclose(b["dec_odds"], [american_to_decimal(x) for x in ml])
    assert np.allclose(b["prob"], np.where(home, g["p_true"], 1 - g["p_true"]))
    assert np.allclose(b["ev"], b["prob"] * b["dec_odds"] - 1)
    assert (b["ev"] > 0.02).all()
    assert np.allclose(b["market_prob_devig"], np.where(home, g["home_ml_prob"], g["away_ml_prob"]))
    assert np.allclose(b["model_clv"], b["prob"] - b["market_prob_devig"])
    assert (b["stake_flat"] == 0.01).all()
    # Kelly stake = min(fraction * full Kelly, cap), scaled to the weekly exposure cap, x week-start bankroll
    assert np.allclose(b["stake_kelly"], expected_kelly_stakes(b, cfg))
    weekly_frac = (b["stake_kelly"] / b["bankroll_start"]).groupby([b["season"], b["week"]]).sum()
    assert (weekly_frac <= cfg.max_weekly_exposure + 1e-12).all()
    won = b["won"].to_numpy()
    assert np.allclose(b["profit_flat"], np.where(won == 1, 0.01 * (b["dec_odds"] - 1), -0.01))
    assert b[["season", "week"]].apply(tuple, axis=1).is_monotonic_increasing
    for k in ("n_bets", "hit_rate", "roi_flat", "roi_kelly", "final_bankroll_kelly", "max_drawdown_flat",
              "max_drawdown_kelly", "roi_ci_lo", "roi_ci_hi", "avg_model_clv", "weekly_sharpe",
              "edge_metric", "devig", "max_weekly_exposure"):
        assert k in res.summary
    for k in ("n_bets", "roi_flat", "profit_flat"):
        assert k in res.by_season.columns


def test_season_window_missing_lines_and_config_validation(mispriced):
    df = mispriced.copy()
    first = int(df["season"].min())
    res = backtest_vs_closing(df, "p_true", BacktestConfig(start_season=first + 1, min_edge=0.02))
    assert res.bets["season"].min() == first + 1
    res = backtest_vs_closing(df, "p_true", BacktestConfig(start_season=first, end_season=first, min_edge=0.02))
    assert set(res.bets["season"]) == {first}
    df.loc[df.index[:100], "home_moneyline"] = np.nan          # no line -> no bet
    df.loc[df.index[100:200], "p_true"] = np.nan               # no prediction -> no bet
    res = backtest_vs_closing(df, "p_true", BacktestConfig(min_edge=0.02))
    assert not set(res.bets["game_id"]) & set(df["game_id"].iloc[:200])
    with pytest.raises(KeyError):
        backtest_vs_closing(df, "not_a_column", BacktestConfig())
    with pytest.raises(ValueError):
        BacktestConfig(bet_types=("spread",))
    with pytest.raises(ValueError):
        BacktestConfig(start_season=2015, end_season=2010)
    with pytest.raises(ValueError):
        BacktestConfig(devig="bogus")                          # a typo must fail at construction
    with pytest.raises(ValueError):
        BacktestConfig(edge_metric="points")
    for bad in (0.0, 1.5, -0.1):
        with pytest.raises(ValueError):
            BacktestConfig(max_weekly_exposure=bad)


def test_devig_method_is_always_applied(mispriced):
    """cfg.devig defines the CLV baseline whether or not the frame carries load_games' columns."""
    stripped = mispriced.drop(columns=["home_ml_prob", "away_ml_prob"])
    cfg_m = BacktestConfig(min_edge=0.02, **MULT)
    ref = backtest_vs_closing(mispriced, "p_true", cfg_m)
    mult = backtest_vs_closing(stripped, "p_true", cfg_m)
    assert np.allclose(mult.bets["market_prob_devig"], ref.bets["market_prob_devig"])
    shin_full = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02, devig="shin"))
    shin_stripped = backtest_vs_closing(stripped, "p_true", BacktestConfig(min_edge=0.02, devig="shin"))
    assert np.array_equal(shin_full.bets["market_prob_devig"], shin_stripped.bets["market_prob_devig"])
    assert shin_full.summary["n_bets"] == ref.summary["n_bets"]         # selection is on the vigged price
    assert not np.allclose(shin_full.bets["market_prob_devig"], ref.bets["market_prob_devig"])
    assert np.abs(shin_full.bets["market_prob_devig"] - ref.bets["market_prob_devig"]).max() < 0.02
    # row-wise agreement with odds.devig on the bets taken
    b = shin_full.bets
    g = mispriced.set_index("game_id").loc[b["game_id"]]
    for i in range(0, len(b), 97):
        fair = devig([g["home_ml_prob_raw"].iloc[i], g["away_ml_prob_raw"].iloc[i]], "shin")
        assert b["market_prob_devig"].iloc[i] == pytest.approx(fair[0] if b["side"].iloc[i] == "home" else fair[1])
    power = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02, devig="power"))
    assert not np.allclose(power.bets["market_prob_devig"], shin_full.bets["market_prob_devig"])


def test_edge_metric_shares_units_and_inequality_across_entry_points():
    # home +100 (price 0.50) with P = 0.75: EV = +50% (exactly), probability edge = +25pp (exactly)
    games = small_games([{"week": 1, "home_win": 1.0, "home_ml": 100, "away_ml": -120, "prob": 0.75}])

    def n_closing(**kw):
        return backtest_vs_closing(games, "prob", BacktestConfig(**kw)).summary["n_bets"]

    assert n_closing(min_edge=0.3, edge_metric="ev") == 1
    assert n_closing(min_edge=0.3, edge_metric="prob") == 0
    assert n_closing(min_edge=0.2, edge_metric="prob") == 1
    assert n_closing(min_edge=0.5, edge_metric="ev") == 0                # strict inequality ...
    assert n_closing(min_edge=0.25, edge_metric="prob") == 0             # ... in both units
    # the same two bets on Polymarket: M is 0.75 fair at 0.50, L is a longshot 0.12 fair at 0.10 (EV +20%, +2pp)
    snaps = pd.DataFrame(
        [("2025-09-07T12:00:00Z", "M", "G", "YES", 0.50, 0.75), ("2025-09-07T12:00:00Z", "L", "G2", "YES", 0.10, 0.12)],
        columns=["ts", "market_id", "game_id", "side", "price", "fair_prob"],
    )
    results = pd.DataFrame({"game_id": ["G", "G2"], "yes_won": [1.0, 0.0], "kickoff": ["2025-09-07T17:00:00Z"] * 2})

    def snapshot_bets(**kw):
        return list(backtest_snapshots(snaps, None, results, BacktestConfig(**kw)).bets["market_id"])

    assert set(snapshot_bets(min_edge=0.1, edge_metric="ev")) == {"M", "L"}
    assert snapshot_bets(min_edge=0.1, edge_metric="prob") == ["M"]      # the 10c longshot needs +10pp
    assert snapshot_bets(min_edge=0.3, edge_metric="ev") == ["M"]
    assert snapshot_bets(min_edge=0.5, edge_metric="ev") == []           # strict
    assert snapshot_bets(min_edge=0.25, edge_metric="prob") == []
    ev = backtest_snapshots(snaps, None, results, BacktestConfig(min_edge=0.1, edge_metric="ev")).bets.set_index("market_id")
    assert ev.loc["M", "ev"] == pytest.approx(0.5) and ev.loc["L", "ev"] == pytest.approx(0.2)


def test_ties_refund_the_stake():
    df = small_games(
        [
            {"week": 1, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
            {"week": 1, "home_win": 0.5, "home_ml": -110, "away_ml": -110, "prob": 0.6},
            {"week": 2, "home_win": 0.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
        ]
    )
    res = backtest_vs_closing(df, "prob", BacktestConfig(min_edge=0.02))
    b = res.bets
    assert len(b) == 3 and (b["side"] == "home").all()
    tie = b[b["won"] == 0.5].iloc[0]
    assert tie["profit_flat"] == 0.0 and tie["profit_kelly"] == 0.0
    assert tie["stake_flat"] == 0.01 and tie["stake_kelly"] > 0
    dec = american_to_decimal(-110)
    assert res.summary["profit_flat"] == pytest.approx(0.01 * (dec - 1) - 0.01)
    assert res.summary["n_pushes"] == 1
    assert res.summary["hit_rate"] == pytest.approx(0.5)      # pushes excluded


def test_weekly_kelly_uses_week_start_bankroll():
    rows = [
        {"week": 1, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
        {"week": 1, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
        {"week": 2, "home_win": 0.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
        {"week": 3, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6},
    ]
    cfg = BacktestConfig(min_edge=0.02, kelly_fraction=0.1, max_stake=0.03)
    res = backtest_vs_closing(small_games(rows), "prob", cfg)
    b = res.bets
    dec = american_to_decimal(-110)
    full = (0.6 * dec - 1) / (dec - 1)
    frac = 0.1 * full
    assert frac < 0.03
    w1 = b[b["week"] == 1]
    assert np.allclose(w1["bankroll_start"], 1.0)
    assert np.allclose(w1["stake_kelly"], frac)                 # both sized off the week-start bankroll
    bank2 = 1.0 + 2 * frac * (dec - 1)
    w2 = b[b["week"] == 2].iloc[0]
    assert w2["bankroll_start"] == pytest.approx(bank2)
    assert w2["stake_kelly"] == pytest.approx(frac * bank2)
    bank3 = bank2 - frac * bank2
    w3 = b[b["week"] == 3].iloc[0]
    assert w3["bankroll_start"] == pytest.approx(bank3)
    final = bank3 + frac * bank3 * (dec - 1)
    assert res.summary["final_bankroll_kelly"] == pytest.approx(final)
    assert res.summary["max_drawdown_kelly"] == pytest.approx((bank2 - bank3) / bank2)
    assert res.summary["roi_kelly"] == pytest.approx(b["profit_kelly"].sum() / b["stake_kelly"].sum())
    # the cap binds when fraction * full Kelly exceeds max_stake
    capped = backtest_vs_closing(small_games(rows), "prob", BacktestConfig(min_edge=0.02, kelly_fraction=1.0, max_stake=0.03))
    assert np.allclose(capped.bets[capped.bets["week"] == 1]["stake_kelly"], 0.03)


def test_weekly_exposure_cap_scales_a_week_proportionally():
    rows = [{"week": 1, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6} for _ in range(16)]
    rows += [{"week": 2, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.6} for _ in range(2)]
    games = small_games(rows)
    cfg = BacktestConfig(min_edge=0.02, kelly_fraction=1.0, max_stake=0.03)   # 16 x 3% = 48% > 15%
    res = backtest_vs_closing(games, "prob", cfg)
    b = res.bets
    w1 = b[b["week"] == 1]
    assert np.allclose(w1["stake_kelly"], 0.03 * (0.15 / 0.48))
    assert w1["stake_kelly"].sum() == pytest.approx(0.15)
    dec = american_to_decimal(-110)
    bank2 = 1.0 + 0.15 * (dec - 1)
    w2 = b[b["week"] == 2]
    assert np.allclose(w2["bankroll_start"], bank2) and np.allclose(w2["stake_kelly"], 0.03 * bank2)  # 6% fits
    assert np.allclose(b["stake_kelly"], expected_kelly_stakes(b, cfg))
    loose = backtest_vs_closing(games, "prob", BacktestConfig(min_edge=0.02, kelly_fraction=1.0, max_stake=0.03, max_weekly_exposure=1.0))
    assert np.allclose(loose.bets[loose.bets["week"] == 1]["stake_kelly"], 0.03)
    assert loose.summary["final_bankroll_kelly"] > res.summary["final_bankroll_kelly"]


def test_bust_week_never_reports_a_negative_bankroll():
    rows = [{"week": 1, "home_win": 0.0, "home_ml": -110, "away_ml": -110, "prob": 0.9} for _ in range(4)]
    rows += [{"week": 2, "home_win": 1.0, "home_ml": -110, "away_ml": -110, "prob": 0.9} for _ in range(2)]
    cfg = BacktestConfig(min_edge=0.0, kelly_fraction=1.0, max_stake=0.5, max_weekly_exposure=1.0)
    res = backtest_vs_closing(small_games(rows), "prob", cfg)
    b = res.bets
    w1 = b[b["week"] == 1]
    assert np.allclose(w1["stake_kelly"], 0.25) and w1["stake_kelly"].sum() == pytest.approx(1.0)
    assert w1["profit_kelly"].sum() == pytest.approx(-1.0)
    assert (w1["profit_kelly"] >= -w1["bankroll_start"]).all()
    w2 = b[b["week"] == 2]
    assert (w2["bankroll_start"] == 0.0).all() and (w2["stake_kelly"] == 0.0).all()
    s = res.summary
    assert s["final_bankroll_kelly"] == 0.0 and s["profit_kelly"] == pytest.approx(-1.0)
    assert s["max_drawdown_kelly"] == 1.0 and s["roi_kelly"] == pytest.approx(-1.0)
    assert (b["stake_kelly"] >= 0).all()


def test_no_look_ahead_flipping_a_later_result(mispriced):
    cfg = BacktestConfig(min_edge=0.02)
    base = backtest_vs_closing(mispriced, "p_true", cfg)
    flipped = mispriced.copy()
    last_season = int(flipped["season"].max())
    target = flipped[(flipped["season"] == last_season) & (flipped["week"] == 10)].index
    flipped.loc[target, ["home_score", "away_score"]] = flipped.loc[target, ["away_score", "home_score"]].to_numpy()
    flipped = nflverse.add_derived_columns(flipped)            # recomputes home_win / played from the scores
    alt = backtest_vs_closing(flipped, "p_true", cfg)
    assert alt.summary["n_bets"] == base.summary["n_bets"]
    key = ["season", "week"]
    before = lambda b: b[(b["season"] < last_season) | ((b["season"] == last_season) & (b["week"] < 10))]  # noqa: E731
    pd.testing.assert_frame_equal(before(base.bets).reset_index(drop=True), before(alt.bets).reset_index(drop=True))
    same_week = lambda b: b[(b["season"] == last_season) & (b["week"] == 10)].reset_index(drop=True)  # noqa: E731
    for col in ("stake_flat", "stake_kelly", "bankroll_start", "prob", "ev"):
        assert np.allclose(same_week(base.bets)[col], same_week(alt.bets)[col])
    assert not np.allclose(same_week(base.bets)["profit_flat"], same_week(alt.bets)["profit_flat"])
    assert (before(base.bets)[key].drop_duplicates().shape[0]) > 10


def test_by_season_is_a_plain_frame(mispriced):
    res = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02))
    bs = res.by_season
    assert isinstance(bs.index, pd.RangeIndex) and list(bs.columns[:2]) == ["season", "n_bets"]
    assert bs.reset_index().shape[1] == bs.shape[1] + 1                 # the routine idioms work
    assert bs.to_dict("records")[0]["season"] == int(mispriced["season"].min())
    assert list(bs["season"]) == sorted(mispriced["season"].unique())
    pd.concat([bs, bs], ignore_index=True)


# ------------------------------------------------------------------ statistics helpers
def test_max_drawdown_known_curve():
    assert max_drawdown([1.0, 1.2, 0.9, 1.1, 0.8, 1.3]) == pytest.approx((1.2 - 0.8) / 1.2)
    assert max_drawdown([1.0, 1.1, 1.2, 1.3]) == 0.0
    assert max_drawdown(np.array([2.0, 1.0])) == pytest.approx(0.5)
    assert max_drawdown(pd.Series([1.0, 0.5, 0.75, 0.25])) == pytest.approx(0.75)
    assert max_drawdown([]) == 0.0
    assert max_drawdown([1.0, np.nan, 0.5]) == pytest.approx(0.5)


def test_bootstrap_roi_ci_contains_sample_roi():
    rng = np.random.default_rng(1)
    stakes = np.full(500, 0.01)
    won = rng.random(500) < 0.55
    profits = np.where(won, 0.01 * 0.9, -0.01)
    roi = profits.sum() / stakes.sum()
    lo, hi = bootstrap_roi(profits, stakes, n=1000, seed=0)
    assert lo < roi < hi
    assert hi - lo < 0.4
    assert bootstrap_roi(profits, stakes, n=1000, seed=0) == (lo, hi)          # deterministic
    assert bootstrap_roi(profits, stakes, n=1000, seed=7) != (lo, hi)
    lo2, hi2 = bootstrap_roi(list(profits), list(stakes), n=1000, seed=0, ci=0.5)
    assert lo < lo2 < hi2 < hi
    assert all(math.isnan(x) for x in bootstrap_roi([], []))
    with pytest.raises(ValueError):
        bootstrap_roi([1.0], [1.0, 1.0])


def test_nfl_season_week_labels_are_chronological():
    when = [
        "2025-09-04T00:20:00Z",   # 2025 opener, Thursday after Labor Day (Sep 1)
        "2025-09-07T17:00:00Z",   # week 1 Sunday
        "2025-09-09T00:15:00Z",   # week 1 Monday night (already Tuesday UTC)
        "2025-09-16T00:15:00Z",   # week 2 Monday night
        "2026-01-11T18:00:00Z",   # wild card
        "2026-02-08T23:30:00Z",   # Super Bowl
        "2021-09-09T00:20:00Z",   # 2021 opener (Labor Day Sep 6)
        "2021-09-12T17:00:00Z",
        "2024-09-05T00:20:00Z",   # 2024 opener (Labor Day Sep 2)
        "2025-08-15T00:00:00Z",   # before the opener
    ]
    season, week = nfl_season_week(when)
    assert list(season) == [2025, 2025, 2025, 2025, 2025, 2025, 2021, 2021, 2024, 2025]
    assert list(week) == [1, 1, 1, 2, 19, 23, 1, 1, 1, -2]
    s, w = nfl_season_week(pd.Series(pd.to_datetime(when[:6], utc=True)))
    assert list(zip(s, w)) == sorted(zip(s, w))                          # monotone within the season
    assert nfl_season_week([])[0].size == 0
    with pytest.raises(ValueError):
        nfl_season_week([pd.NaT])


# ------------------------------------------------------------------ backtest_snapshots
def _snapshot_stream() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = [
        # M1 YES: edge 0.01 (no), then 0.07 (entry at 0.48), then last pre-kickoff mid 0.52, then in-game
        ("2025-09-07T10:00:00Z", "M1", "G1", "YES", 0.50, 0.49, 0.51),
        ("2025-09-07T12:00:00Z", "M1", "G1", "YES", 0.48, 0.47, 0.55),
        ("2025-09-07T16:00:00Z", "M1", "G1", "YES", 0.53, 0.52, 0.55),
        ("2025-09-07T18:00:00Z", "M1", "G1", "YES", 0.90, 0.89, 0.55),
        # M1 NO never qualifies
        ("2025-09-07T10:00:00Z", "M1", "G1", "NO", 0.52, 0.51, 0.49),
        ("2025-09-07T12:00:00Z", "M1", "G1", "NO", 0.54, 0.53, 0.45),
        # M2 NO qualifies at its first snapshot (edge 0.05) and loses (YES won)
        ("2025-09-07T09:00:00Z", "M2", "G2", "NO", 0.40, 0.39, 0.45),
        ("2025-09-07T15:00:00Z", "M2", "G2", "NO", 0.38, 0.37, 0.45),
        # M3 YES qualifies only after kickoff -> no bet
        ("2025-09-07T19:00:00Z", "M3", "G3", "YES", 0.30, 0.29, 0.60),
        # M4 YES qualifies but the game has no result -> no bet
        ("2025-09-07T09:00:00Z", "M4", "G4", "YES", 0.30, 0.29, 0.60),
    ]
    snaps = pd.DataFrame(rows, columns=["ts", "market_id", "game_id", "side", "price", "mid", "fair_prob"])
    results = pd.DataFrame(
        {
            "game_id": ["G1", "G2", "G3"],
            "yes_won": [1.0, 1.0, 1.0],
            "kickoff": ["2025-09-07T17:00:00Z"] * 3,
            "season": [2025, 2025, 2025],
            "week": [1, 1, 1],
        }
    )
    return snaps, results


def test_backtest_snapshots_first_qualifying_entry_and_clv():
    snaps, results = _snapshot_stream()
    cfg = BacktestConfig(min_edge=0.03, flat_stake=0.01)
    res = backtest_snapshots(snaps, None, results, cfg)
    b = res.bets.set_index("market_id")
    assert list(res.bets.columns[: len(BET_COLUMNS)]) == list(BET_COLUMNS)
    assert not any(c.startswith("_") for c in res.bets.columns)
    assert set(b.index) == {"M1", "M2"}
    m1 = b.loc["M1"]
    assert m1["side"] == "YES" and m1["price"] == 0.48
    assert pd.Timestamp(m1["entry_ts"]) == pd.Timestamp("2025-09-07T12:00:00Z")
    assert pd.Timestamp(m1["kickoff"]) == pd.Timestamp("2025-09-07T17:00:00Z")
    assert m1["prob"] == 0.55 and m1["dec_odds"] == pytest.approx(1 / 0.48)
    assert m1["ev"] == pytest.approx(0.55 / 0.48 - 1)
    assert m1["close_price"] == pytest.approx(0.52)            # last pre-kickoff MID, not the in-game quote
    assert m1["clv"] == pytest.approx(0.52 - 0.48)
    assert m1["market_prob_devig"] == pytest.approx(0.47) and m1["model_clv"] == pytest.approx(0.55 - 0.47)
    assert m1["won"] == 1.0 and m1["profit_flat"] == pytest.approx(0.01 * (1 / 0.48 - 1))
    m2 = b.loc["M2"]
    assert m2["side"] == "NO" and m2["price"] == 0.40 and m2["won"] == 0.0
    assert m2["profit_flat"] == pytest.approx(-0.01)
    assert m2["close_price"] == pytest.approx(0.37) and m2["clv"] == pytest.approx(0.37 - 0.40)
    assert (res.bets["season"] == 2025).all() and (res.bets["week"] == 1).all()
    assert (res.bets["bankroll_start"] == 1.0).all()           # both entered before anything resolved
    s = res.summary
    assert s["n_bets"] == 2 and s["n_markets"] == 2
    assert s["avg_clv"] == pytest.approx(((0.52 - 0.48) + (0.37 - 0.40)) / 2)
    assert s["clv_hit_rate"] == 0.5
    assert s["hit_rate"] == 0.5
    assert s["roi_flat"] == pytest.approx((0.01 * (1 / 0.48 - 1) - 0.01) / 0.02)

    # without a mid column the last pre-kickoff ask is the closing reference
    res2 = backtest_snapshots(snaps.drop(columns=["mid"]), None, results, cfg)
    b2 = res2.bets.set_index("market_id")
    assert b2.loc["M1", "close_price"] == 0.53 and b2.loc["M1", "market_prob_devig"] == 0.48
    assert "M3" not in b2.index
    # the kickoff is mandatory: without it in-game quotes could enter and resolved quotes could "close"
    with pytest.raises(ValueError, match="kickoff"):
        backtest_snapshots(snaps, None, results.drop(columns=["kickoff"]), cfg)
    with pytest.raises(ValueError):
        backtest_snapshots(snaps, None, None, cfg)


def test_backtest_snapshots_fair_probs_table_and_tie():
    snaps, results = _snapshot_stream()
    snaps = snaps.drop(columns=["fair_prob"])
    # time-varying P(YES) per market: M1 fair jumps to 0.58 at 11:00 (before the 12:00 quote)
    fair = pd.DataFrame(
        {
            "market_id": ["M1", "M1", "M2"],
            "ts": ["2025-09-07T09:00:00Z", "2025-09-07T11:00:00Z", "2025-09-07T08:00:00Z"],
            "fair_prob": [0.50, 0.58, 0.55],
        }
    )
    results = results.copy()
    results.loc[results["game_id"] == "G2", "yes_won"] = 0.5      # tie / void
    cfg = BacktestConfig(min_edge=0.03)
    res = backtest_snapshots(snaps, fair, results, cfg)
    b = res.bets.set_index("market_id")
    assert b.loc["M1", "prob"] == pytest.approx(0.58) and b.loc["M1", "price"] == 0.48
    assert b.loc["M2", "side"] == "NO" and b.loc["M2", "prob"] == pytest.approx(0.45)   # 1 - P(YES)
    assert b.loc["M2", "won"] == 0.5 and b.loc["M2", "profit_flat"] == 0.0 and b.loc["M2", "profit_kelly"] == 0.0
    assert res.summary["n_pushes"] == 1
    # derived season/week when results carry none: Sunday 2025-09-07 is NFL week 1, not ISO week 36
    res3 = backtest_snapshots(snaps, fair, results.drop(columns=["season", "week"]), cfg)
    assert (res3.bets["season"] == 2025).all() and (res3.bets["week"] == 1).all()


def _two_game_stream(sep_won: float, jan_won: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    snaps = pd.DataFrame(
        [
            ("2025-09-07T12:00:00Z", "G_SEP", "G_SEP", "YES", 0.50, 0.60),
            ("2026-01-11T12:00:00Z", "G_JAN", "G_JAN", "YES", 0.50, 0.60),
        ],
        columns=["ts", "market_id", "game_id", "side", "price", "fair_prob"],
    )
    results = pd.DataFrame(
        {"game_id": ["G_SEP", "G_JAN"], "yes_won": [sep_won, jan_won],
         "kickoff": ["2025-09-07T17:00:00Z", "2026-01-11T18:00:00Z"]}
    )
    return snaps, results


def test_snapshot_weeks_derived_from_kickoff_keep_january_after_september():
    """Regression: the ISO-week fallback settled a January playoff game before September."""
    cfg = BacktestConfig(min_edge=0.03)
    snaps, results = _two_game_stream(1.0, 1.0)
    base = backtest_snapshots(snaps, None, results, cfg)
    assert list(base.bets["game_id"]) == ["G_SEP", "G_JAN"]
    assert list(base.bets["season"]) == [2025, 2025] and list(base.bets["week"]) == [1, 19]
    sep, jan = base.bets.iloc[0], base.bets.iloc[1]
    assert sep["bankroll_start"] == 1.0 and sep["stake_kelly"] == pytest.approx(0.03)
    assert jan["bankroll_start"] == pytest.approx(1.0 + sep["profit_kelly"])       # January sees September
    snaps, results = _two_game_stream(1.0, 0.0)
    flipped = backtest_snapshots(snaps, None, results, cfg)
    pd.testing.assert_series_equal(flipped.bets.iloc[0], base.bets.iloc[0])        # ... but not vice versa
    assert flipped.bets.iloc[1]["bankroll_start"] == pytest.approx(jan["bankroll_start"])
    assert flipped.summary["final_bankroll_kelly"] == pytest.approx(1.0 + sep["profit_kelly"] - jan["stake_kelly"])
    assert list(base.by_season["season"]) == [2025]


def test_snapshot_kelly_is_sized_off_the_bankroll_at_entry_time():
    """An early entry on a later week's game must not see results of games played after the entry."""
    kick_w1, kick_w2 = "2025-09-07T17:00:00Z", "2025-09-14T17:00:00Z"
    resolved_w1 = pd.Timestamp(kick_w1) + SETTLEMENT_LAG

    def run(w1_won: float) -> pd.DataFrame:
        snaps = pd.DataFrame(
            [
                ("2025-09-05T12:00:00Z", "W1", "W1", "YES", 0.50, 0.60),   # same instant -> one batch
                ("2025-09-05T12:00:00Z", "W2A", "W2A", "YES", 0.50, 0.60),
                ("2025-09-07T19:00:00Z", "W2B", "W2B", "YES", 0.50, 0.60),  # W1 in progress: unknown
                ((resolved_w1 + pd.Timedelta(minutes=1)).isoformat(), "W2C", "W2C", "YES", 0.50, 0.60),  # W1 known
            ],
            columns=["ts", "market_id", "game_id", "side", "price", "fair_prob"],
        )
        results = pd.DataFrame(
            {"game_id": ["W1", "W2A", "W2B", "W2C"], "yes_won": [w1_won, 1.0, 1.0, 1.0],
             "kickoff": [kick_w1, kick_w2, kick_w2, kick_w2]}
        )
        return backtest_snapshots(snaps, None, results, BacktestConfig(min_edge=0.03)).bets.set_index("market_id")

    win, loss = run(1.0), run(0.0)
    for b in (win, loss):
        assert list(b["week"]) == [1, 2, 2, 2]
        assert b.loc["W1", "bankroll_start"] == 1.0 and b.loc["W2A", "bankroll_start"] == 1.0
        assert b.loc["W2B", "bankroll_start"] == 1.0                          # kicked off, not resolved
        assert b.loc["W1", "stake_kelly"] == pytest.approx(0.03) and b.loc["W2A", "stake_kelly"] == pytest.approx(0.03)
    # W2C is entered after W1 resolved and is sized off the updated bankroll
    assert win.loc["W2C", "bankroll_start"] == pytest.approx(1.0 + 0.03)
    assert loss.loc["W2C", "bankroll_start"] == pytest.approx(1.0 - 0.03)
    assert win.loc["W2C", "stake_kelly"] == pytest.approx(0.03 * 1.03)
    for m in ("W2A", "W2B"):
        pd.testing.assert_series_equal(win.loc[m].drop("won"), loss.loc[m].drop("won"))
    # open positions count against the exposure cap: three 3% bets open, a fourth is trimmed to the room
    snaps = pd.DataFrame(
        [(f"2025-09-05T1{i}:00:00Z", f"M{i}", f"M{i}", "YES", 0.50, 0.90) for i in range(6)],
        columns=["ts", "market_id", "game_id", "side", "price", "fair_prob"],
    )
    results = pd.DataFrame({"game_id": snaps["game_id"], "yes_won": 1.0, "kickoff": kick_w1})
    b = backtest_snapshots(snaps, None, results, BacktestConfig(min_edge=0.03, kelly_fraction=1.0, max_stake=0.05)).bets
    assert np.allclose(b["stake_kelly"], [0.05, 0.05, 0.05, 0.0, 0.0, 0.0])
    assert b["stake_kelly"].sum() == pytest.approx(0.15)


def test_snapshot_in_game_and_resolved_quotes_never_enter_or_close():
    def stream(fair_pre: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                ("2025-09-07T16:00:00Z", "M", "G", "YES", 0.60, 0.595, fair_pre),   # pre-game
                ("2025-09-07T20:30:00Z", "M", "G", "YES", 0.20, 0.195, fair_pre),   # trailing in-game
                ("2025-09-08T02:00:00Z", "M", "G", "YES", 0.99, 0.985, fair_pre),   # resolved
            ],
            columns=["ts", "market_id", "game_id", "side", "price", "mid", "fair_prob"],
        )

    cfg = BacktestConfig(min_edge=0.03)
    no_edge = stream(0.60)
    results = pd.DataFrame({"game_id": ["G"], "yes_won": [1.0]})
    with pytest.raises(ValueError, match="kickoff"):
        backtest_snapshots(no_edge, None, results, cfg)
    assert backtest_snapshots(no_edge, None, results.assign(kickoff="2025-09-07T17:00:00Z"), cfg).summary["n_bets"] == 0
    # a kickoff carried on the stream itself is accepted; a numeric market_id key still joins
    assert backtest_snapshots(no_edge.assign(kickoff="2025-09-07T17:00:00Z"), None, results, cfg).summary["n_bets"] == 0
    numeric = stream(0.70).assign(market_id=7)
    res_num = pd.DataFrame({"market_id": [7], "yes_won": [1.0], "kickoff": ["2025-09-07T17:00:00Z"]})
    assert backtest_snapshots(numeric, None, res_num, cfg).summary["n_bets"] == 1
    # game_date + gametime (Eastern) derive the kickoff: 13:00 ET = 17:00 UTC
    assert backtest_snapshots(no_edge, None, results.assign(game_date="2025-09-07", gametime="13:00"), cfg).summary["n_bets"] == 0
    with_edge = stream(0.70)
    res = backtest_snapshots(with_edge, None, results.assign(game_date="2025-09-07", gametime="13:00"), cfg)
    assert res.summary["n_bets"] == 1
    bet = res.bets.iloc[0]
    assert bet["price"] == 0.60 and bet["close_price"] == pytest.approx(0.595) and bet["clv"] == pytest.approx(-0.005)
    assert pd.Timestamp(bet["kickoff"]) == pd.Timestamp("2025-09-07T17:00:00Z")
    # a bare game_date is midnight UTC: every quote on game day counts as in-game (conservative)
    assert backtest_snapshots(with_edge, None, results.assign(game_date="2025-09-07"), cfg).summary["n_bets"] == 0
    early = with_edge.copy()
    early.loc[0, "ts"] = "2025-09-06T16:00:00Z"
    res = backtest_snapshots(early, None, results.assign(game_date="2025-09-07"), cfg)
    assert res.summary["n_bets"] == 1 and res.bets.iloc[0]["close_price"] == pytest.approx(0.595)
    assert pd.Timestamp(res.bets.iloc[0]["kickoff"]) == pd.Timestamp("2025-09-07T00:00:00Z")


def test_native_snapshot_schema_from_load_snapshots(tmp_path):
    polymarket = pytest.importorskip("nfl_edge.markets.polymarket")
    cols = polymarket.SNAPSHOT_COLUMNS
    assert {"ts", "market_id", "is_yes", "price", "best_ask", "mid", "kind", "outcome_index"} <= set(cols)

    def row(ts, market_id, kind, outcome_index, is_yes, price, bid, ask):
        base = {c: None for c in cols}
        base.update(
            ts=ts, market_id=market_id, condition_id=f"c{market_id}", question=f"{kind} q", kind=kind,
            event_slug="kc-buf", token_id=f"t{market_id}{outcome_index}", outcome="KC" if outcome_index == 0 else "BUF",
            outcome_index=outcome_index, is_yes=is_yes, price=price, best_bid=bid, best_ask=ask,
            mid=None if bid is None or ask is None else round(0.5 * (bid + ask), 6),
            spread=None if bid is None or ask is None else round(ask - bid, 6), liquidity=1000.0, volume=5000.0,
            end_date="2025-09-08", home="KC", away="BUF", team=None, line=None,
        )
        return base

    rows = [
        # moneyline: YES (KC) quoted 0.50 by Gamma but the ask is 0.56; NO ask 0.52 (= 1 - YES bid 0.48)
        row("2025-09-07T12:00:00Z", "ML", "moneyline", 0, True, 0.50, 0.48, 0.56),
        row("2025-09-07T12:00:00Z", "ML", "moneyline", 1, False, 0.50, 0.44, 0.52),
        row("2025-09-07T16:00:00Z", "ML", "moneyline", 0, True, 0.52, 0.50, 0.58),
        row("2025-09-07T16:00:00Z", "ML", "moneyline", 1, False, 0.48, 0.42, 0.50),
        # spread market on the same game: a game-level yes_won says nothing about it
        row("2025-09-07T12:00:00Z", "SP", "spread", 0, True, 0.30, 0.28, 0.32),
        row("2025-09-07T12:00:00Z", "SP", "spread", 1, False, 0.70, 0.68, 0.72),
        # a quote with no book cannot be entered
        row("2025-09-07T13:00:00Z", "NB", "moneyline", 0, True, 0.10, None, None),
    ]
    path = tmp_path / "snapshots.jsonl"
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    native = polymarket.load_snapshots(path)
    assert len(native) == len(rows)
    native = native.assign(game_id=np.where(native["market_id"] == "NB", "G2", "G1"))
    fair = pd.DataFrame({"market_id": ["ML", "SP", "NB"], "fair_prob": [0.70, 0.60, 0.90]})   # P(YES)
    results = pd.DataFrame({"game_id": ["G1", "G2"], "yes_won": [1.0, 1.0], "kickoff": ["2025-09-07T17:00:00Z"] * 2})
    res = backtest_snapshots(native, fair, results, BacktestConfig(min_edge=0.03))
    b = res.bets.set_index("market_id")
    assert list(b.index) == ["ML"]                                     # spread filtered out, NB has no ask
    assert b.loc["ML", "side"] == "YES" and b.loc["ML", "price"] == pytest.approx(0.56)   # entered at the ASK
    assert b.loc["ML", "ev"] == pytest.approx(0.70 / 0.56 - 1)
    assert b.loc["ML", "market_prob_devig"] == pytest.approx(0.52)     # mid at entry
    assert b.loc["ML", "close_price"] == pytest.approx(0.54)           # last pre-kickoff mid
    assert b.loc["ML", "won"] == 1.0
    # the spread market is settled when results are keyed by market_id
    res_m = backtest_snapshots(native, fair, pd.DataFrame(
        {"market_id": ["ML", "SP"], "yes_won": [1.0, 0.0], "kickoff": ["2025-09-07T17:00:00Z"] * 2}
    ), BacktestConfig(min_edge=0.03))
    assert set(res_m.bets["market_id"]) == {"ML", "SP"}
    sp = res_m.bets.set_index("market_id").loc["SP"]
    assert sp["side"] == "YES" and sp["price"] == pytest.approx(0.32) and sp["won"] == 0.0
    # a stream without side/is_yes or without any ask column is rejected
    with pytest.raises(KeyError):
        backtest_snapshots(native.drop(columns=["is_yes"]), fair, results, BacktestConfig())
    with pytest.raises(KeyError):
        backtest_snapshots(native.drop(columns=["price", "best_ask"]), fair, results, BacktestConfig())


# ------------------------------------------------------------------ report
def test_report_markdown_contains_headline_numbers(mispriced):
    res = backtest_vs_closing(mispriced, "p_true", BacktestConfig(min_edge=0.02))
    md = report_markdown(res)
    s = res.summary
    assert md.startswith("# Backtest report")
    assert f"**{s['n_bets']} bets**" in md
    assert f"{100 * s['roi_flat']:+.2f}%" in md
    assert f"{100 * s['roi_ci_lo']:+.2f}%" in md and f"{100 * s['roi_ci_hi']:+.2f}%" in md
    assert f"{100 * s['hit_rate']:.2f}%" in md
    assert f"{s['final_bankroll_kelly']:.4f}" in md
    assert "| roi_kelly |" in md and "| max_drawdown_flat |" in md and "| weekly_sharpe |" in md
    assert "| devig | shin |" in md and "| edge_metric | ev |" in md
    assert "## By season" in md
    for season in res.by_season["season"]:
        assert f"| {season} |" in md


# ------------------------------------------------------------------ honesty check on real data
@pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")
def test_real_data_market_prob_returns_minus_vig():
    games = nflverse.load_games(path=GAMES)
    none = backtest_vs_closing(games, "market_prob", BacktestConfig(min_edge=0.02))
    assert none.summary["n_bets"] == 0                       # nothing beats the vig with the market's own prob
    res = backtest_vs_closing(games, "market_prob", BacktestConfig(min_edge=-1, **MULT))
    s = res.summary
    assert s["n_bets"] > 4000 and s["start_season"] == 2010
    played = games[games["played"] & games["home_moneyline"].notna() & (games["season"] >= 2010)]
    expected = (1.0 / (played["home_ml_prob_raw"] + played["away_ml_prob_raw"]) - 1.0).mean()
    assert -0.045 < expected < -0.02
    assert -0.06 < s["roi_flat"] < -0.005                   # ~ -vig
    assert s["roi_ci_lo"] < expected < s["roi_ci_hi"]
    assert s["roi_ci_hi"] < 0.0
    assert abs(s["avg_model_clv"]) < 1e-9
    assert len(res.by_season) == s["end_season"] - s["start_season"] + 1
    assert res.by_season["n_bets"].sum() == s["n_bets"]
    res.by_season.reset_index()
    # the default Shin baseline differs from the frame's multiplicative columns by a few tenths of a point
    shin = backtest_vs_closing(games, "market_prob", BacktestConfig(min_edge=-1))
    assert shin.summary["devig"] == "shin" and shin.summary["n_bets"] == s["n_bets"]
    diff = shin.bets["market_prob_devig"].to_numpy() - res.bets["market_prob_devig"].to_numpy()
    assert 0.0 < np.abs(diff).max() < 0.03 and abs(shin.summary["avg_model_clv"]) < 0.01
    weekly_frac = (res.bets["stake_kelly"] / res.bets["bankroll_start"].replace(0.0, np.nan)).fillna(0.0)
    assert (weekly_frac.groupby([res.bets["season"], res.bets["week"]]).sum() <= 0.15 + 1e-12).all()
