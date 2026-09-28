"""Tests for nfl_edge.sim.season: standings, tiebreakers, bracket, probabilities, look-ahead safety.

Real cached nflverse seasons (offline) check that the standings engine reproduces every actual
playoff field and seed order; synthetic schedules exercise individual tiebreak steps and the
home-field / neutral-site handling.  Network is blocked by tests/conftest.py.
"""
from __future__ import annotations

import time
import warnings

import numpy as np
import pandas as pd
import pytest

from nfl_edge.config import CACHE_DIR
from nfl_edge.data import load_games
from nfl_edge.odds import elo_diff_to_prob
from nfl_edge.sim import SimConfig, playoff_format, simulate_season, standings_from_results, win_total_probs
from nfl_edge.sim import season as season_mod
from nfl_edge.teams import DIVISIONS, division_members

GAMES = CACHE_DIR / "games.csv"
needs_cache = pytest.mark.skipif(not GAMES.exists(), reason="games.csv not cached")
PROB_COLS = ["p_playoffs", "p_division", "p_top_seed", "p_conference", "p_super_bowl"]
TEAMS = sorted(DIVISIONS)


@pytest.fixture(scope="module")
def games() -> pd.DataFrame:
    if not GAMES.exists():
        pytest.skip("games.csv not cached")
    return load_games()


@pytest.fixture(scope="module")
def flat() -> dict[str, float]:
    return {t: 1500.0 for t in TEAMS}


def actual_field(games: pd.DataFrame, season: int) -> set[str]:
    d = games[(games["season"] == season) & (games["game_type"].isin(["WC", "DIV"]))]
    return set(d["home_team_c"]) | set(d["away_team_c"])


def make_games(rows: list[dict], season: int = 2024) -> pd.DataFrame:
    """Minimal load_games()-shaped frame from compact specs (margin None = unplayed)."""
    recs = []
    for k, r in enumerate(rows):
        margin = r.get("margin")
        played = margin is not None
        recs.append(
            {
                "game_id": f"{season}_{r.get('week', 1):02d}_{r['away']}_{r['home']}_{k}",
                "season": season,
                "week": r.get("week", 1),
                "game_date": pd.Timestamp(f"{season}-09-10") + pd.Timedelta(days=7 * (r.get("week", 1) - 1)),
                "game_type": r.get("game_type", "REG"),
                "location": r.get("location", "Home"),
                "home_team_c": r["home"],
                "away_team_c": r["away"],
                "margin": float(margin) if played else np.nan,
                "played": played,
                "home_win": (1.0 if margin > 0 else 0.0 if margin < 0 else 0.5) if played else np.nan,
            }
        )
    return pd.DataFrame(recs)


def round_robin(results: dict[str, list[tuple[str, str, float]]]) -> pd.DataFrame:
    """Division-only schedule: results[div] = [(winner, loser, margin), ...]."""
    rows = []
    for triples in results.values():
        for w, l, m in triples:
            rows.append({"home": w, "away": l, "margin": m})
    return make_games(rows)


def full_round_robin_except(special: dict[str, list[tuple[str, str, float]]]) -> pd.DataFrame:
    """Every division plays a single round robin, except the ones in `special`.

    Default division: the alphabetically first club beats everyone (3-0) and the other three form
    a cycle (1-2 each), so non-winners sit at .333 and cannot interfere with wild-card tests.
    """
    results = {}
    for conf_div in sorted(set(DIVISIONS.values())):
        a, b, c, d = sorted(t for t, dv in DIVISIONS.items() if dv == conf_div)
        key = f"{conf_div[0]} {conf_div[1]}"
        if key in special:
            results[key] = special[key]
            continue
        results[key] = [(a, b, 7.0), (a, c, 7.0), (a, d, 7.0), (b, c, 7.0), (c, d, 7.0), (d, b, 7.0)]
    return round_robin(results)


# --------------------------------------------------------------------------- basics
def test_playoff_format() -> None:
    assert playoff_format(2019) == (6, 2)
    assert playoff_format(2020) == (7, 1)
    assert playoff_format(2026) == (7, 1)
    assert season_mod.bracket_game_count(2024) == 13
    assert season_mod.bracket_game_count(2019) == 11


def test_elo_prob_array_matches_scalar() -> None:
    diffs = np.array([-400.0, -55.0, 0.0, 55.0, 400.0])
    expected = np.array([elo_diff_to_prob(d) for d in diffs])
    np.testing.assert_allclose(season_mod._elo_prob_array(diffs), expected, rtol=1e-12)


def test_conditional_margins_respect_winner() -> None:
    rng = np.random.default_rng(3)
    hw = (rng.random((500, 6)) < 0.5).astype(float)
    diff = np.array([-300.0, -55.0, 0.0, 55.0, 150.0, 400.0])
    m = season_mod._conditional_margins(hw, diff, rng)
    assert m.shape == hw.shape
    assert np.all(m[hw == 1.0] >= 1.0)
    assert np.all(m[hw == 0.0] <= -1.0)
    assert np.all(m == np.rint(m))


# --------------------------------------------------------------------------- real seasons
@needs_cache
def test_2024_reproduces_actual_field_seeds_and_champion(games: pd.DataFrame, flat: dict[str, float]) -> None:
    out = simulate_season(games, 2024, flat, SimConfig(n_sims=50, seed=0))
    assert list(out.columns) == ["mean_wins", "p_playoffs", "p_division", "p_top_seed", "p_conference",
                                 "p_super_bowl", "win_dist"]
    assert list(out.index) == TEAMS
    field = set(out.index[out["p_playoffs"] > 0.999])
    assert field == actual_field(games, 2024)
    assert set(out.index[out["p_division"] > 0.999]) == {"KC", "BAL", "HOU", "BUF", "DET", "PHI", "TB", "LA"}
    assert set(out.index[out["p_top_seed"] > 0.999]) == {"KC", "DET"}
    # full seed order: every actual wild-card game paired seeds (2,7), (3,6), (4,5)
    seeds = out.attrs["seeds"][0]
    seed_of = {TEAMS[t]: (c, k + 1) for c in range(2) for k, t in enumerate(seeds[c])}
    wc = games[(games["season"] == 2024) & (games["game_type"] == "WC")]
    for _, r in wc.iterrows():
        h, a = r["home_team_c"], r["away_team_c"]
        assert seed_of[h][0] == seed_of[a][0] and seed_of[h][1] + seed_of[a][1] == 9, (h, a)
    # played playoff games are fixed too: the real champion is certain
    assert out.loc["PHI", "p_super_bowl"] == 1.0
    assert out.loc["PHI", "p_conference"] == 1.0 and out.loc["KC", "p_conference"] == 1.0
    assert out["mean_wins"].sum() == pytest.approx(272.0)


@needs_cache
def test_2023_reproduces_at_least_13_of_14(games: pd.DataFrame, flat: dict[str, float]) -> None:
    out = simulate_season(games, 2023, flat, SimConfig(n_sims=20, seed=0))
    field = set(out.index[out["p_playoffs"] > 0.999])
    actual = actual_field(games, 2023)
    assert len(field & actual) >= 13
    assert field == actual  # the engine gets all 14, including GB over SEA and TB over NO on tiebreaks


@needs_cache
@pytest.mark.parametrize("season", list(range(2002, 2026)))
def test_every_completed_season_field_and_wildcard_pairings(games: pd.DataFrame, flat: dict[str, float], season: int) -> None:
    out = simulate_season(games, season, flat, SimConfig(n_sims=4, seed=1))
    n_playoff, _ = playoff_format(season)
    field = set(out.index[out["p_playoffs"] > 0.999])
    assert field == actual_field(games, season)
    assert out["p_playoffs"].sum() == pytest.approx(2 * n_playoff)
    seeds = out.attrs["seeds"][0]
    seed_of = {TEAMS[t]: (c, k + 1) for c in range(2) for k, t in enumerate(seeds[c])}
    wc = games[(games["season"] == season) & (games["game_type"] == "WC")]
    for _, r in wc.iterrows():
        h, a = r["home_team_c"], r["away_team_c"]
        assert seed_of[h][0] == seed_of[a][0] and seed_of[h][1] + seed_of[a][1] == 9, (season, h, a)


@needs_cache
def test_counterfactual_bracket_when_playoffs_dropped(games: pd.DataFrame, flat: dict[str, float]) -> None:
    reg_only = games[games["game_type"] == "REG"]
    out = simulate_season(reg_only, 2024, flat, SimConfig(n_sims=2000, seed=0))
    assert out["p_super_bowl"].sum() == pytest.approx(1.0)
    assert 0.0 < out.loc["PHI", "p_super_bowl"] < 1.0
    assert (out.loc[out["p_playoffs"] == 0.0, "p_super_bowl"] == 0.0).all()
    # byes: the 1 seeds cannot lose in the wild-card round, so with flat ratings they are the favourites
    assert out.loc["KC", "p_conference"] == out.loc[out["p_playoffs"] > 0, "p_conference"].loc[
        lambda s: s.index.map(lambda t: DIVISIONS[t][0] == "AFC")].max()


# --------------------------------------------------------------------------- live season
@needs_cache
def test_partial_2026_probability_identities(games: pd.DataFrame, flat: dict[str, float]) -> None:
    out = simulate_season(games, 2026, flat, SimConfig(n_sims=3000, seed=7))
    assert out.attrs["season"] == 2026 and out.attrs["n_sims"] == 3000
    assert out.attrs["hot"] is True and out.attrs["as_of"] is None
    assert out.attrs["n_unplayed"] == int(((games["season"] == 2026) & (games["game_type"] == "REG")
                                           & ~games["played"]).sum())
    for c in PROB_COLS:
        assert ((out[c] >= 0.0) & (out[c] <= 1.0)).all(), c
    assert out["p_super_bowl"].sum() == pytest.approx(1.0)
    assert out["p_conference"].sum() == pytest.approx(2.0)
    assert out["p_top_seed"].sum() == pytest.approx(2.0)
    assert out["p_playoffs"].sum() == pytest.approx(14.0)
    for conf_div in sorted(set(DIVISIONS.values())):
        members = [t for t, d in DIVISIONS.items() if d == conf_div]
        assert out.loc[members, "p_division"].sum() == pytest.approx(1.0), conf_div
    # nested events: SB <= conference <= playoffs, division <= playoffs, top seed <= division
    assert (out["p_super_bowl"] <= out["p_conference"] + 1e-12).all()
    assert (out["p_conference"] <= out["p_playoffs"] + 1e-12).all()
    assert (out["p_division"] <= out["p_playoffs"] + 1e-12).all()
    assert (out["p_top_seed"] <= out["p_division"] + 1e-12).all()
    # a live season is genuinely uncertain
    assert (out["p_playoffs"] > 0.0).all() and (out["p_playoffs"] < 1.0).all()
    # win distributions are proper and consistent with mean_wins / the wins matrix
    wins = out.attrs["wins"]
    assert wins.shape == (3000, 32)
    for t in TEAMS:
        dist = out.loc[t, "win_dist"]
        assert sum(dist.values()) == pytest.approx(1.0)
        assert sum(k * v for k, v in dist.items()) == pytest.approx(out.loc[t, "mean_wins"])
        assert all(0 <= k <= 17 for k in dist)
    played = games[(games["season"] == 2026) & games["played"]]
    so_far = pd.concat([played.assign(t=played["home_team_c"], w=played["home_win"]),
                        played.assign(t=played["away_team_c"], w=1 - played["home_win"])]).groupby("t")["w"].sum()
    assert (out["mean_wins"] >= so_far.reindex(TEAMS).fillna(0) - 1e-9).all()
    assert (out["mean_wins"] <= 17.0).all()


@needs_cache
def test_deterministic_under_seed(games: pd.DataFrame, flat: dict[str, float]) -> None:
    a = simulate_season(games, 2026, flat, SimConfig(n_sims=500, seed=11))
    b = simulate_season(games, 2026, flat, SimConfig(n_sims=500, seed=11))
    pd.testing.assert_frame_equal(a.drop(columns="win_dist"), b.drop(columns="win_dist"))
    assert list(a["win_dist"]) == list(b["win_dist"])
    np.testing.assert_array_equal(a.attrs["seeds"], b.attrs["seeds"])
    c = simulate_season(games, 2026, flat, SimConfig(n_sims=500, seed=12))
    assert not np.allclose(a["p_super_bowl"], c["p_super_bowl"])


@needs_cache
def test_dominant_team_wins_super_bowl(games: pd.DataFrame, flat: dict[str, float]) -> None:
    ratings = dict(flat)
    ratings["JAX"] = 1900.0
    hot = simulate_season(games, 2026, ratings, SimConfig(n_sims=2000, seed=0))
    static = simulate_season(games, 2026, ratings, SimConfig(n_sims=2000, seed=0, hot=False))
    for out in (hot, static):
        assert out.loc["JAX", "p_playoffs"] > 0.95
        assert out.loc["JAX", "p_division"] > 0.9
        assert out["p_super_bowl"].idxmax() == "JAX"
        assert out.loc["JAX", "p_super_bowl"] > 10 * out["p_super_bowl"].drop("JAX").max()
    assert static.loc["JAX", "p_super_bowl"] > 0.7
    # hot ratings drift and playoff opponents are selected winners whose ratings rose, so a heavy
    # favourite's title odds are lower (and more honest) than under frozen ratings
    assert 0.5 < hot.loc["JAX", "p_super_bowl"] < static.loc["JAX", "p_super_bowl"]


@needs_cache
def test_playoff_mult_and_hfa_config_are_used(games: pd.DataFrame, flat: dict[str, float]) -> None:
    ratings = dict(flat)
    ratings["DET"] = 1650.0
    base = simulate_season(games, 2026, ratings, SimConfig(n_sims=3000, seed=0, playoff_mult=1.0))
    boosted = simulate_season(games, 2026, ratings, SimConfig(n_sims=3000, seed=0, playoff_mult=2.0))
    assert boosted.loc["DET", "p_super_bowl"] > base.loc["DET", "p_super_bowl"]


@needs_cache
def test_timing_2k_sims(games: pd.DataFrame, flat: dict[str, float]) -> None:
    t0 = time.perf_counter()
    simulate_season(games, 2026, flat, SimConfig(n_sims=2000, seed=0))
    dt = time.perf_counter() - t0
    assert dt < 6.0, f"2k sims took {dt:.1f}s; 20k would exceed the 60s budget"


# --------------------------------------------------------------------------- look-ahead safety
WEEK10_2024 = "2024-11-07"  # first kickoff of 2024 week 10; weeks 1-9 are strictly before it


def masked_2024(games: pd.DataFrame, keep_playoffs: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    """2024 with REG weeks >= 10 masked by hand in every result column (a faithful time-travel)."""
    g = games[games["season"] == 2024].copy()
    future = (g["week"] >= 10) & (g["game_type"] == "REG")
    for col in ("home_score", "away_score", "result", "margin", "home_win", "total"):
        g.loc[future, col] = np.nan
    g.loc[future, "played"] = False
    if not keep_playoffs:
        g = g[g["game_type"] == "REG"]
    return g, future


def flipped_2024(games: pd.DataFrame) -> pd.DataFrame:
    """2024 with every REG week >= 10 outcome reversed, consistently across all result columns."""
    g = games[games["season"] == 2024].copy()
    future = (g["week"] >= 10) & (g["game_type"] == "REG")
    hs, aw = g.loc[future, "home_score"].to_numpy(), g.loc[future, "away_score"].to_numpy()
    g.loc[future, "home_score"], g.loc[future, "away_score"] = aw, hs
    for col in ("result", "margin"):
        g.loc[future, col] = -g.loc[future, col]
    g.loc[future, "home_win"] = 1.0 - g.loc[future, "home_win"]
    return g


@needs_cache
def test_no_lookahead_hidden_future_results_do_not_matter(games: pd.DataFrame, flat: dict[str, float]) -> None:
    """Time-travel to 2024 week 10 with ``as_of``: reversing every later result must not change the
    sim, and the module's own masking must equal a faithful hand mask of every result column."""
    cfg = SimConfig(n_sims=400, seed=5, as_of=WEEK10_2024)
    a = simulate_season(games, 2024, flat, cfg)
    b = simulate_season(flipped_2024(games), 2024, flat, cfg)
    pd.testing.assert_frame_equal(a.drop(columns="win_dist"), b.drop(columns="win_dist"))
    hand, future = masked_2024(games)
    c = simulate_season(hand, 2024, flat, SimConfig(n_sims=400, seed=5))
    pd.testing.assert_frame_equal(a.drop(columns="win_dist"), c.drop(columns="win_dist"))
    assert a.attrs["n_unplayed"] == int(future.sum()) == c.attrs["n_unplayed"]
    assert a.attrs["as_of"] == pd.Timestamp(WEEK10_2024)
    # the masked season is genuinely uncertain, and the full one is not
    assert 0.0 < a.loc["KC", "p_playoffs"] <= 1.0 and (a["p_playoffs"] < 1.0).any()
    assert a.loc["PHI", "p_super_bowl"] < 1.0
    full = simulate_season(games, 2024, flat, SimConfig(n_sims=400, seed=5))
    assert full.loc["PHI", "p_super_bowl"] == 1.0
    assert not full["p_playoffs"].equals(a["p_playoffs"])


@needs_cache
def test_as_of_masks_by_date_only(games: pd.DataFrame, flat: dict[str, float]) -> None:
    """Rows dated ``as_of`` or later are unknown (same-day games included), so are undated rows."""
    s = games[games["season"] == 2024]
    sunday = "2024-11-10"
    expected = int(((s["game_type"] == "REG") & (s["game_date"] >= sunday)).sum())
    out = simulate_season(games, 2024, flat, SimConfig(n_sims=8, seed=0, as_of=sunday))
    assert out.attrs["n_unplayed"] == expected
    assert expected < int(((s["game_type"] == "REG") & (s["week"] >= 10)).sum())  # Thursday game is known
    # a timestamp with a time of day is truncated to the date: same mask
    late = simulate_season(games, 2024, flat, SimConfig(n_sims=8, seed=0, as_of=pd.Timestamp("2024-11-10 15:30")))
    assert late.attrs["n_unplayed"] == expected
    # a played row without a date cannot be placed before as_of, so it is treated as unplayed
    undated = games[games["season"] == 2024].copy()
    early = undated.index[(undated["week"] == 1) & (undated["game_type"] == "REG")][0]
    undated.loc[early, "game_date"] = pd.NaT
    out_u = simulate_season(undated, 2024, flat, SimConfig(n_sims=8, seed=0, as_of=sunday))
    assert out_u.attrs["n_unplayed"] == expected + 1
    # as_of after the Super Bowl: everything stays played
    out_all = simulate_season(games, 2024, flat, SimConfig(n_sims=8, seed=0, as_of="2025-03-01"))
    assert out_all.attrs["n_unplayed"] == 0 and out_all.loc["PHI", "p_super_bowl"] == 1.0
    with pytest.raises(ValueError, match="game_date"):
        simulate_season(games[games["season"] == 2024].drop(columns="game_date"), 2024, flat,
                        SimConfig(n_sims=8, as_of=sunday))
    with pytest.raises(ValueError, match="as_of"):
        simulate_season(games, 2024, flat, SimConfig(n_sims=8, as_of=pd.NaT))


@needs_cache
def test_played_playoff_rows_ignored_when_regular_season_incomplete(games: pd.DataFrame, flat: dict[str, float]) -> None:
    """A mid-season snapshot that still carries played playoff rows (a partial masking bug) must
    not graft the real bracket onto simulated standings: warn, then simulate the bracket."""
    leaky, _ = masked_2024(games, keep_playoffs=True)
    assert leaky["game_type"].isin(["WC", "DIV", "CON", "SB"]).sum() == 13 and leaky["played"].sum() > 0
    with pytest.warns(UserWarning, match="playoff rows ignored"):
        a = simulate_season(leaky, 2024, flat, SimConfig(n_sims=1500, seed=5))
    clean, _ = masked_2024(games)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        b = simulate_season(clean, 2024, flat, SimConfig(n_sims=1500, seed=5))
        # once the regular season is complete the played playoff rows are fixed again
        full = simulate_season(games, 2024, flat, SimConfig(n_sims=20, seed=5))
    assert not [w for w in rec if "playoff rows ignored" in str(w.message)]
    pd.testing.assert_frame_equal(a.drop(columns="win_dist"), b.drop(columns="win_dist"))
    assert a.loc["PHI", "p_super_bowl"] < 0.5 and a.loc["KC", "p_conference"] < 0.5
    assert a["p_super_bowl"].sum() == pytest.approx(1.0)
    assert full.loc["PHI", "p_super_bowl"] == 1.0


@needs_cache
def test_inconsistent_played_state_raises(games: pd.DataFrame, flat: dict[str, float]) -> None:
    """The ``played`` flag is never trusted on its own: partial masks raise instead of leaking
    hidden results (scores NaN but home_win kept) or producing NaN standings (flag True, no result)."""
    g = games[games["season"] == 2024].copy()
    future = (g["week"] >= 10) & (g["game_type"] == "REG")
    scores_only = g.copy()
    for col in ("home_score", "away_score", "result", "total"):
        scores_only.loc[future, col] = np.nan
    with pytest.raises(ValueError, match="home_win disagrees with home_score"):
        simulate_season(scores_only, 2024, flat, SimConfig(n_sims=8, seed=5))
    stale_flag = scores_only.copy()
    for col in ("home_win", "margin"):
        stale_flag.loc[future, col] = np.nan
    assert stale_flag.loc[future, "played"].all()
    with pytest.raises(ValueError, match="'played' flag disagrees"):
        simulate_season(stale_flag, 2024, flat, SimConfig(n_sims=8, seed=5))
    # the reverse partial mask (flag cleared, results kept) is rejected too
    flag_only = g.copy()
    flag_only.loc[future, "played"] = False
    with pytest.raises(ValueError, match="'played' flag disagrees"):
        simulate_season(flag_only, 2024, flat, SimConfig(n_sims=8, seed=5))
    # the faithful mask is accepted and the error names the offending games
    faithful, _ = masked_2024(games)
    simulate_season(faithful, 2024, flat, SimConfig(n_sims=8, seed=5))
    with pytest.raises(ValueError, match="2024_10_"):
        simulate_season(stale_flag, 2024, flat, SimConfig(n_sims=8, seed=5))


def test_inconsistent_played_state_raises_without_score_columns() -> None:
    g = full_round_robin_except({})
    ratings = {t: 1500.0 for t in TEAMS}
    stale = g.copy()
    stale.loc[0, "home_win"] = np.nan
    stale.loc[0, "margin"] = np.nan  # played flag still True
    with pytest.raises(ValueError, match="'played' flag disagrees"):
        simulate_season(stale, 2024, ratings, SimConfig(n_sims=4))
    bad_value = g.copy()
    bad_value.loc[0, "home_win"] = 0.7
    with pytest.raises(ValueError, match="home_win in"):
        simulate_season(bad_value, 2024, ratings, SimConfig(n_sims=4))
    bad_margin = g.copy()
    bad_margin.loc[0, "margin"] = -bad_margin.loc[0, "margin"]  # sign no longer matches home_win
    with pytest.raises(ValueError, match="margin"):
        simulate_season(bad_margin, 2024, ratings, SimConfig(n_sims=4))
    with pytest.raises(ValueError, match="home_win"):
        simulate_season(g.drop(columns="home_win"), 2024, ratings, SimConfig(n_sims=4))
    # NaN in the flag column counts as unplayed and must match the data
    nan_flag = g.copy()
    nan_flag["played"] = nan_flag["played"].astype(object)
    nan_flag.loc[0, "played"] = np.nan
    with pytest.raises(ValueError, match="'played' flag disagrees"):
        simulate_season(nan_flag, 2024, ratings, SimConfig(n_sims=4))
    # a frame without the flag derives it from the data and runs
    out = simulate_season(g.drop(columns="played"), 2024, ratings, SimConfig(n_sims=4))
    assert out["mean_wins"].sum() == pytest.approx(len(g))


def test_standings_reject_non_finite_results() -> None:
    g = full_round_robin_except({})
    W = np.tile(g["home_win"].to_numpy(dtype=float), (3, 1))
    W[1, 2] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        standings_from_results(g, W)


@needs_cache
def test_non_finite_ratings_raise(games: pd.DataFrame, flat: dict[str, float]) -> None:
    for bad in (np.nan, np.inf, -np.inf):
        ratings = dict(flat)
        ratings["KC"] = bad
        with pytest.raises(ValueError, match="non-finite ratings.*KC"):
            simulate_season(games, 2026, ratings, SimConfig(n_sims=4, seed=0))
    with pytest.raises(ValueError, match="non-finite ratings"):
        season_mod._ratings_array({**flat, "OAK": np.nan, "LV": np.nan}, TEAMS)


# --------------------------------------------------------------------------- hot simulation
def test_mov_multiplier_matches_elo_formula() -> None:
    margins = np.array([1.0, 3.0, -7.0, 21.0, -35.0, 0.0])
    diff = np.array([55.0, -200.0, 300.0, 0.0, -455.0, 10.0])
    expected = []
    for m, d in zip(margins, diff):
        if m == 0:
            expected.append(1.0)
        else:
            d_winner = d if m > 0 else -d
            expected.append(np.log(abs(m) + 1.0) * 2.2 / (0.001 * d_winner + 2.2))
    np.testing.assert_allclose(season_mod._mov_multiplier(margins, diff, True), expected, rtol=1e-12)
    np.testing.assert_array_equal(season_mod._mov_multiplier(margins, diff, False), np.ones(6))


def test_rating_update_is_zero_sum_and_follows_the_result() -> None:
    cfg = SimConfig(k=20.0, mov=False)
    R = np.full((5, 32), 1500.0)
    home = np.array([0, 2, 4])
    away = np.array([1, 3, 5])
    diff = np.full((5, 3), 55.0)
    p = season_mod._elo_prob_array(diff)
    w = np.array([[1, 0, 1]] * 5, dtype=float)
    m = np.where(w == 1, 7.0, -7.0)
    season_mod._rating_update(R, home, away, diff, p, w, m, cfg)
    np.testing.assert_allclose(R.sum(1), 32 * 1500.0)
    assert (R[:, 0] > 1500.0).all() and (R[:, 1] < 1500.0).all()
    assert (R[:, 2] < 1500.0).all() and (R[:, 3] > 1500.0).all()
    np.testing.assert_allclose(R[:, 0] - 1500.0, 20.0 * (1.0 - p[:, 0]))
    np.testing.assert_allclose(R[:, 2] - 1500.0, -20.0 * p[:, 1])
    # per-simulation layout (bracket games): one matchup per simulation
    R2 = np.full((4, 32), 1500.0)
    home2, away2 = np.array([0, 1, 2, 3]), np.array([9, 8, 7, 6])
    d2 = np.zeros(4)
    w2 = np.array([1.0, 0.0, 1.0, 0.0])
    season_mod._rating_update(R2, home2, away2, d2, season_mod._elo_prob_array(d2), w2, np.array([3.0, -3.0, 3.0, -3.0]), cfg)
    np.testing.assert_allclose(R2.sum(1), 32 * 1500.0)
    np.testing.assert_allclose(R2[np.arange(4), home2] - 1500.0, 20.0 * (w2 - 0.5))
    np.testing.assert_allclose(R2[np.arange(4), away2] - 1500.0, -20.0 * (w2 - 0.5))


def test_no_repeat_batches_split_when_a_team_repeats() -> None:
    # game 2 repeats club 0 -> new batch; game 4 (clubs 1, 3) is idle in that batch -> merged;
    # game 5 repeats club 4 -> new batch
    home = np.array([0, 2, 0, 4, 1, 4])
    away = np.array([1, 3, 2, 5, 3, 6])
    batches = season_mod._no_repeat_batches(np.arange(6), home, away)
    assert [b.tolist() for b in batches] == [[0, 1], [2, 3, 4], [5]]
    # a custom order is respected
    assert [b.tolist() for b in season_mod._no_repeat_batches(np.array([5, 3, 1]), home, away)] == [[5], [3, 1]]
    assert season_mod._no_repeat_batches(np.array([], dtype=int), home, away) == []


@needs_cache
def test_hot_batches_are_nfl_weeks(games: pd.DataFrame) -> None:
    reg = games[(games["season"] == 2026) & (games["game_type"] == "REG") & ~games["played"]]
    reg = reg.sort_values(["week", "game_date", "game_id"])
    home = season_mod._team_index(TEAMS, reg["home_team_c"])
    away = season_mod._team_index(TEAMS, reg["away_team_c"])
    batches = season_mod._no_repeat_batches(np.arange(len(reg)), home, away)
    weeks = reg["week"].to_numpy()
    n_weeks = len(np.unique(weeks))
    # a batch never splits inside a week (no team plays twice in one), and only a game whose two
    # clubs were both idle the week before can be pulled into the previous batch (bye weeks)
    assert n_weeks - 6 <= len(batches) <= n_weeks
    assert np.array_equal(np.concatenate(batches), np.arange(len(reg)))  # schedule order, complete
    for b in batches:
        assert len(np.unique(np.concatenate([home[b], away[b]]))) == 2 * len(b)
        assert weeks[b].max() - weeks[b].min() <= 1
    # every game sees ratings that include all earlier games of both clubs: no earlier game of
    # either club sits in the same batch (the sequential-semantics invariant)
    for b in batches:
        for pos, j in enumerate(b):
            earlier = b[:pos]
            assert home[j] not in home[earlier] and home[j] not in away[earlier]
            assert away[j] not in home[earlier] and away[j] not in away[earlier]


@needs_cache
def test_hot_simulation_widens_win_totals(games: pd.DataFrame) -> None:
    """Frozen ratings make season totals Poisson-binomial and too narrow; hot ratings and rating
    noise both widen them, and every probability identity still holds."""
    rng = np.random.default_rng(1)
    ratings = {t: float(v) for t, v in zip(TEAMS, rng.normal(1500.0, 90.0, len(TEAMS)))}
    static = simulate_season(games, 2026, ratings, SimConfig(n_sims=4000, seed=1, hot=False))
    hot = simulate_season(games, 2026, ratings, SimConfig(n_sims=4000, seed=1, hot=True))
    noisy = simulate_season(games, 2026, ratings, SimConfig(n_sims=4000, seed=1, hot=False, rating_noise_sd=80.0))
    sd = lambda out: out.attrs["wins"].to_numpy().std(axis=0)  # noqa: E731
    assert sd(hot).mean() > 1.2 * sd(static).mean()
    assert sd(noisy).mean() > 1.1 * sd(static).mean()
    assert (sd(hot) > sd(static)).mean() > 0.9  # nearly every team, not just the average
    assert static.attrs["hot"] is False and hot.attrs["hot"] is True
    for out in (hot, noisy):
        assert out["p_super_bowl"].sum() == pytest.approx(1.0)
        assert out["p_conference"].sum() == pytest.approx(2.0)
        assert out["p_playoffs"].sum() == pytest.approx(14.0)
        assert (out["p_super_bowl"] <= out["p_conference"] + 1e-12).all()
        assert (out["p_conference"] <= out["p_playoffs"] + 1e-12).all()
        for t in TEAMS:
            dist = out.loc[t, "win_dist"]
            assert sum(dist.values()) == pytest.approx(1.0)
            assert sum(k * v for k, v in dist.items()) == pytest.approx(out.loc[t, "mean_wins"])
    # banked wins are unchanged by the rating model, and total wins are conserved
    played = games[(games["season"] == 2026) & games["played"]]
    so_far = pd.concat([played.assign(t=played["home_team_c"], w=played["home_win"]),
                        played.assign(t=played["away_team_c"], w=1 - played["home_win"])]).groupby("t")["w"].sum()
    assert (hot["mean_wins"] >= so_far.reindex(TEAMS).fillna(0) - 1e-9).all()
    assert hot["mean_wins"].sum() == pytest.approx(static["mean_wins"].sum())


@needs_cache
def test_hot_with_zero_k_matches_the_static_distribution(games: pd.DataFrame) -> None:
    rng = np.random.default_rng(1)
    ratings = {t: float(v) for t, v in zip(TEAMS, rng.normal(1500.0, 90.0, len(TEAMS)))}
    static = simulate_season(games, 2026, ratings, SimConfig(n_sims=10000, seed=3, hot=False))
    frozen = simulate_season(games, 2026, ratings, SimConfig(n_sims=10000, seed=3, hot=True, k=0.0))
    assert (static["mean_wins"] - frozen["mean_wins"]).abs().max() < 0.15
    assert (static["p_playoffs"] - frozen["p_playoffs"]).abs().max() < 0.05
    assert abs(static.attrs["wins"].to_numpy().std(axis=0).mean() - frozen.attrs["wins"].to_numpy().std(axis=0).mean()) < 0.05


def test_hot_single_game_probability_is_exact() -> None:
    """With one game there is nothing to drift: hot and static both give the exact Elo expectation."""
    ratings = {t: 1500.0 for t in TEAMS}
    ratings["KC"] = 1600.0
    n = 20000
    g = make_games([{"home": "KC", "away": "BUF"}])
    expected = elo_diff_to_prob(155.0)
    tol = 4 * np.sqrt(expected * (1 - expected) / n)
    for hot in (True, False):
        out = simulate_season(g, 2024, ratings, SimConfig(n_sims=n, seed=0, hot=hot))
        assert out.loc["KC", "mean_wins"] == pytest.approx(expected, abs=tol)


def test_hot_second_game_reflects_the_first_result() -> None:
    """Two games KC-BUF: after a KC win the rematch odds move for KC, after a loss against."""
    ratings = {t: 1500.0 for t in TEAMS}
    g = make_games([{"home": "KC", "away": "BUF", "week": 1, "location": "Neutral"},
                    {"home": "KC", "away": "BUF", "week": 2, "location": "Neutral"}])
    out = simulate_season(g, 2024, ratings, SimConfig(n_sims=20000, seed=0, hot=True))
    dist = out.loc["KC", "win_dist"]
    # independent coin flips would give P(2 wins) = 0.25; hot ratings make results positively dependent
    assert dist[2] > 0.26 and dist[0] > 0.26 and dist[1] < 0.48
    assert out.loc["KC", "mean_wins"] == pytest.approx(1.0, abs=0.03)
    static = simulate_season(g, 2024, ratings, SimConfig(n_sims=20000, seed=0, hot=False))
    assert static.loc["KC", "win_dist"][1] == pytest.approx(0.5, abs=0.015)


def test_bad_config_values_raise() -> None:
    g = full_round_robin_except({})
    ratings = {t: 1500.0 for t in TEAMS}
    with pytest.raises(ValueError, match="k must"):
        simulate_season(g, 2024, ratings, SimConfig(n_sims=2, k=-1.0))
    with pytest.raises(ValueError, match="rating_noise_sd"):
        simulate_season(g, 2024, ratings, SimConfig(n_sims=2, rating_noise_sd=-5.0))
    with pytest.raises(ValueError, match="k must"):
        simulate_season(g, 2024, ratings, SimConfig(n_sims=2, k=np.nan))


@needs_cache
def test_other_seasons_do_not_influence_the_simulation(games: pd.DataFrame, flat: dict[str, float]) -> None:
    a = simulate_season(games, 2026, flat, SimConfig(n_sims=300, seed=2))
    tampered = games.copy()
    prior = tampered["season"] < 2026
    tampered.loc[prior, "home_win"] = 1.0 - tampered.loc[prior, "home_win"]
    tampered.loc[prior, "margin"] = -tampered.loc[prior, "margin"]
    b = simulate_season(tampered, 2026, flat, SimConfig(n_sims=300, seed=2))
    pd.testing.assert_frame_equal(a.drop(columns="win_dist"), b.drop(columns="win_dist"))


@needs_cache
def test_flipping_a_played_game_moves_exactly_one_win(games: pd.DataFrame, flat: dict[str, float]) -> None:
    g = games[games["season"] == 2026].copy()
    row = g[(g["played"]) & (g["home_win"] != 0.5)].index[0]
    home, away = g.loc[row, "home_team_c"], g.loc[row, "away_team_c"]
    flipped = g.copy()
    flipped.loc[row, "home_win"] = 1.0 - g.loc[row, "home_win"]
    flipped.loc[row, "margin"] = -g.loc[row, "margin"]
    a = simulate_season(g, 2026, flat, SimConfig(n_sims=300, seed=0))
    b = simulate_season(flipped, 2026, flat, SimConfig(n_sims=300, seed=0))
    sign = 1.0 if g.loc[row, "home_win"] == 1.0 else -1.0
    assert b.loc[home, "mean_wins"] - a.loc[home, "mean_wins"] == pytest.approx(-sign)
    assert b.loc[away, "mean_wins"] - a.loc[away, "mean_wins"] == pytest.approx(sign)
    others = [t for t in TEAMS if t not in (home, away)]
    np.testing.assert_allclose(a.loc[others, "mean_wins"], b.loc[others, "mean_wins"])


# --------------------------------------------------------------------------- synthetic schedules
def test_home_field_and_neutral_site_probabilities() -> None:
    """One unplayed game: P(home) = Elo(hfa) at home and 0.5 on a neutral field."""
    ratings = {t: 1500.0 for t in TEAMS}
    n = 20000
    home = make_games([{"home": "KC", "away": "BUF"}])
    out = simulate_season(home, 2024, ratings, SimConfig(n_sims=n, seed=0, hfa_elo=55.0))
    expected = elo_diff_to_prob(55.0)
    assert out.loc["KC", "mean_wins"] == pytest.approx(expected, abs=4 * np.sqrt(expected * (1 - expected) / n))
    assert out.loc["KC", "mean_wins"] + out.loc["BUF", "mean_wins"] == pytest.approx(1.0)
    neutral = make_games([{"home": "KC", "away": "BUF", "location": "Neutral"}])
    out_n = simulate_season(neutral, 2024, ratings, SimConfig(n_sims=n, seed=0, hfa_elo=55.0))
    assert out_n.loc["KC", "mean_wins"] == pytest.approx(0.5, abs=4 * np.sqrt(0.25 / n))
    ratings["BUF"] = 1900.0
    out_r = simulate_season(neutral, 2024, ratings, SimConfig(n_sims=n, seed=0))
    p = elo_diff_to_prob(-400.0)
    assert out_r.loc["KC", "mean_wins"] == pytest.approx(p, abs=4 * np.sqrt(p * (1 - p) / n))
    assert out_r.loc["KC", "win_dist"] == {0: pytest.approx(1 - out_r.loc["KC", "mean_wins"]),
                                           1: pytest.approx(out_r.loc["KC", "mean_wins"])}


def test_division_tiebreak_head_to_head() -> None:
    """Two clubs tied on record: the head-to-head winner takes the division."""
    special = {"AFC North": [("BAL", "CIN", 3.0), ("BAL", "CLE", 3.0), ("PIT", "BAL", 3.0),
                             ("PIT", "CLE", 3.0), ("CIN", "PIT", 3.0), ("CIN", "CLE", 3.0)]}
    # BAL 2-1, PIT 2-1, CIN 2-1?  -> BAL beat CIN, PIT beat BAL, CIN beat PIT: make it a clean 2-way tie
    special = {"AFC North": [("BAL", "CIN", 3.0), ("BAL", "CLE", 3.0), ("PIT", "BAL", 3.0),
                             ("PIT", "CLE", 3.0), ("CIN", "PIT", 10.0), ("CLE", "CIN", 1.0)]}
    # records: BAL 2-1, PIT 2-1, CIN 1-2, CLE 1-2; PIT beat BAL head-to-head
    g = full_round_robin_except(special)
    out = simulate_season(g, 2024, {t: 1500.0 for t in TEAMS}, SimConfig(n_sims=64, seed=0))
    assert out.loc["PIT", "p_division"] == 1.0
    assert out.loc["BAL", "p_division"] == 0.0
    assert out.loc["PIT", "mean_wins"] == 2.0 and out.loc["BAL", "mean_wins"] == 2.0


def test_division_three_way_cycle_falls_to_point_differential_then_coin_flip() -> None:
    """A -> B -> C -> A cycle with identical records and SOV: point differential decides,
    and without margins the coin flip splits the division evenly."""
    cycle = [("BAL", "CIN", 3.0), ("CIN", "CLE", 3.0), ("CLE", "BAL", 3.0),
             ("BAL", "PIT", 21.0), ("CIN", "PIT", 7.0), ("CLE", "PIT", 1.0)]
    g = full_round_robin_except({"AFC North": cycle})
    out = simulate_season(g, 2024, {t: 1500.0 for t in TEAMS}, SimConfig(n_sims=64, seed=0))
    assert out.loc["BAL", "p_division"] == 1.0  # +24 net beats +4 and -2
    assert out.loc[["CIN", "CLE", "PIT"], "p_division"].sum() == 0.0
    # same schedule without margins -> coin flip among the three
    W = np.tile(g["home_win"].to_numpy(dtype=float), (3000, 1))
    st = standings_from_results(g, W, margins=None, rng=np.random.default_rng(1), season=2024)
    d = st.divisions.index("AFC North")
    counts = np.bincount(st.division_winners[:, d], minlength=32) / 3000
    for t in ("BAL", "CIN", "CLE"):
        assert counts[TEAMS.index(t)] == pytest.approx(1 / 3, abs=0.04)
    assert counts[TEAMS.index("PIT")] == 0.0


def test_wildcard_same_division_pair_uses_division_ladder() -> None:
    """Two AFC North clubs tied for the wild cards, split 1-1 head-to-head: the division ladder
    (division record) must rank CIN first even though the wild-card ladder (conference record)
    would rank CLE first."""
    north = [("BAL", "CIN", 3.0), ("BAL", "CLE", 3.0), ("BAL", "PIT", 3.0),
             ("CIN", "CLE", 3.0), ("CLE", "CIN", 3.0), ("CIN", "PIT", 3.0), ("PIT", "CLE", 3.0)]
    # division records: BAL 3-0, CIN 2-2, CLE 1-3, PIT 1-2
    extra = [{"home": "CIN", "away": "DAL", "margin": 3.0},   # non-conference win
             {"home": "CIN", "away": "JAX", "margin": 3.0}, {"home": "TEN", "away": "CIN", "margin": 3.0},
             {"home": "CLE", "away": "JAX", "margin": 3.0}, {"home": "CLE", "away": "TEN", "margin": 3.0},
             {"home": "CLE", "away": "HOU", "margin": 3.0}]
    # overall: CIN 4-3, CLE 4-3 (tied, best non-winners in the AFC); conference: CIN 3-3, CLE 4-3
    g = pd.concat([full_round_robin_except({"AFC North": north}), make_games(extra)], ignore_index=True)
    out = simulate_season(g, 2024, {t: 1500.0 for t in TEAMS}, SimConfig(n_sims=32, seed=0))
    assert out.loc["BAL", "p_division"] == 1.0
    assert out.loc["CIN", "mean_wins"] == 4.0 and out.loc["CLE", "mean_wins"] == 4.0
    afc_order = [TEAMS[t] for t in out.attrs["seeds"][0][0]]
    assert set(afc_order[:4]) == {"BAL", "BUF", "DEN", "HOU"}  # the 3-0 division winners
    assert afc_order[4] == "CIN" and afc_order[5] == "CLE"
    assert out.loc["CIN", "p_playoffs"] == 1.0 and out.loc["CLE", "p_playoffs"] == 1.0


def test_wildcard_three_way_cycle_uses_conference_record_then_restarts() -> None:
    """Three clubs from three divisions tied for the wild cards with a head-to-head cycle (no
    sweep): conference record picks the first, then the remaining pair restarts at head-to-head."""

    def div(w: str, r: str, x: str, y: str) -> list[tuple[str, str, float]]:
        return [(w, r, 3.0), (w, x, 3.0), (w, y, 3.0), (r, x, 3.0), (r, y, 3.0), (x, y, 3.0)]

    special = {"AFC East": div("BUF", "MIA", "NE", "NYJ"), "AFC North": div("BAL", "CIN", "CLE", "PIT"),
               "AFC South": div("HOU", "IND", "JAX", "TEN")}
    extra = [
        # head-to-head cycle among the runners-up: MIA > CIN > IND > MIA
        {"home": "MIA", "away": "CIN", "margin": 3.0}, {"home": "CIN", "away": "IND", "margin": 3.0},
        {"home": "IND", "away": "MIA", "margin": 3.0},
        # one more win and one more loss each, chosen so conference records differ
        {"home": "MIA", "away": "DEN", "margin": 3.0}, {"home": "PHI", "away": "MIA", "margin": 3.0},  # MIA conf 4-2
        {"home": "CIN", "away": "NYG", "margin": 3.0}, {"home": "PHI", "away": "CIN", "margin": 3.0},  # CIN conf 3-2
        {"home": "IND", "away": "DAL", "margin": 3.0}, {"home": "LV", "away": "IND", "margin": 3.0},   # IND conf 3-3
    ]
    g = pd.concat([full_round_robin_except(special), make_games(extra)], ignore_index=True)
    out = simulate_season(g, 2024, {t: 1500.0 for t in TEAMS}, SimConfig(n_sims=16, seed=0))
    assert out.loc[["MIA", "CIN", "IND"], "mean_wins"].tolist() == [4.0, 4.0, 4.0]
    assert set(out.index[(out["p_division"] == 1.0) & out.index.map(lambda t: DIVISIONS[t][0] == "AFC")]) == {
        "BUF", "BAL", "HOU", "DEN"}
    afc_order = [TEAMS[t] for t in out.attrs["seeds"][0][0]]
    assert afc_order[4:7] == ["MIA", "CIN", "IND"], afc_order


def test_standings_matrix_validation() -> None:
    g = full_round_robin_except({})
    with pytest.raises(ValueError):
        standings_from_results(g, np.zeros((4, len(g) + 1)))
    with pytest.raises(ValueError):
        standings_from_results(g, np.zeros((4, len(g))), margins=np.zeros((4, 3)))


def test_bad_inputs_raise() -> None:
    g = full_round_robin_except({})
    ratings = {t: 1500.0 for t in TEAMS}
    with pytest.raises(ValueError, match="missing"):
        simulate_season(g, 2024, {k: v for k, v in ratings.items() if k != "KC"})
    with pytest.raises(ValueError, match="no games"):
        simulate_season(g, 2025, ratings, SimConfig(n_sims=2))
    with pytest.raises(ValueError, match="2002"):
        simulate_season(g.assign(season=2001), 2001, ratings, SimConfig(n_sims=2))
    with pytest.raises(ValueError):
        simulate_season(g, 2024, ratings, SimConfig(n_sims=0))


def test_historical_codes_in_ratings_are_canonicalised() -> None:
    g = make_games([{"home": "LA", "away": "LAC"}])
    ratings = {t: 1500.0 for t in TEAMS if t not in ("LA", "LAC", "LV")}
    ratings.update({"STL": 1500.0, "SD": 1500.0, "OAK": 1500.0})
    out = simulate_season(g, 2024, ratings, SimConfig(n_sims=10, seed=0))
    assert out.loc["LA", "mean_wins"] + out.loc["LAC", "mean_wins"] == pytest.approx(1.0)


def test_win_total_probs() -> None:
    wins = np.array([[10, 3], [12, 3], [8, 4], [10, 5]], dtype=float)
    p = win_total_probs(wins, 0)
    assert p == {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.0, 8: 1.0,
                 9: 0.75, 10: 0.75, 11: 0.25, 12: 0.25}
    vals = list(p.values())
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    df = pd.DataFrame(wins, columns=["KC", "BUF"])
    assert win_total_probs(df, "BUF") == {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 0.5, 5: 0.25}
    with pytest.raises(ValueError):
        win_total_probs(np.zeros(3), 0)


@needs_cache
def test_win_total_probs_from_simulation_attrs(games: pd.DataFrame, flat: dict[str, float]) -> None:
    out = simulate_season(games, 2026, flat, SimConfig(n_sims=500, seed=0))
    p = win_total_probs(out.attrs["wins"], "KC")
    assert p[0] == 1.0
    dist = out.loc["KC", "win_dist"]
    for n in p:
        assert p[n] == pytest.approx(sum(v for k, v in dist.items() if k >= n))
