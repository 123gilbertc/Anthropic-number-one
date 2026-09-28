"""Integration tests for the pipeline and CLI on cached data and Polymarket fixtures (offline)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from nfl_edge import pipeline
from nfl_edge.config import CACHE_DIR
from nfl_edge.data import current_season, load_games
from nfl_edge.markets.polymarket import PolymarketClient, match_game_markets
from nfl_edge.models.elo import EloParams

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
pytestmark = pytest.mark.skipif(not (CACHE_DIR / "games.csv").exists(), reason="games.csv not cached")


@pytest.fixture(scope="module")
def games():
    return load_games()


@pytest.fixture(scope="module")
def bundle(games, tmp_path_factory):
    b = pipeline.fit_models(games, tune_elo=False, log=lambda *_: None)
    b.elo = EloParams(k=20, hfa=45, season_regress=0.5, rest_bonus=25, qb_change_penalty=40)
    path = tmp_path_factory.mktemp("models") / "models.json"
    b.save(path)
    return pipeline.ModelBundle.load(path)


@pytest.fixture(scope="module")
def feats(games, bundle):
    return pipeline.score_games(pipeline.build_features(games, bundle), bundle)


def test_bundle_roundtrip(bundle):
    d = bundle.to_dict()
    again = pipeline.ModelBundle.from_dict(json.loads(json.dumps(d)))
    assert again.elo == bundle.elo and again.stacker == bundle.stacker
    assert again.game_stacker() is not None and again.model_only_stacker() is not None


def test_features_and_scores_are_sane(feats, games):
    played = feats[feats["played"]]
    for c in ("elo_prob", "epa_prob", "market_shin", "fair_prob"):
        assert played[c].between(0, 1).all(), c
    live = current_season(games)
    up = feats[(feats["season"] == live) & (~feats["played"]) & feats["spread_line"].notna()]
    assert not up.empty
    assert (up["fair_prob_source"] == "ensemble").all()
    # the ensemble hugs the market: mean absolute disagreement well under 5 points
    assert (up["fair_prob"] - up["market_shin"]).abs().mean() < 0.05
    # fair spread is the market spread shifted by the model tilt, never far from the line
    assert (up["fair_spread"] - up["spread_line"]).abs().max() < 6
    # games without a line fall back to the model-only stack
    no_line = feats[(feats["season"] == live) & feats["spread_line"].isna()]
    assert (no_line["fair_prob_source"] == "model-only").all() and no_line["fair_prob"].notna().all()


def test_predict_week_writes_reports(games, bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REPORTS_DIR", tmp_path)
    live = current_season(games)
    table = pipeline.predict_week(games, bundle, live, 3)
    assert len(table) == 16 and table["fair_prob"].between(0, 1).all()
    assert (tmp_path / f"predictions_{live}_wk03.md").exists()
    md = (tmp_path / f"predictions_{live}_wk03.md").read_text()
    assert "| date |" in md and "KC" in md


def test_market_adjusted_ratings_recover_lines(feats, bundle):
    live = current_season(feats)
    elo = {t: 1500.0 for t in pipeline.CURRENT_TEAMS}
    r = pipeline.market_adjusted_ratings(feats, elo, bundle.elo.hfa, live, lam=1e-6)
    up = feats[(feats["season"] == live) & (~feats["played"]) & feats["market_shin"].notna()]
    # with a negligible prior the solved ratings reproduce every market Elo difference closely
    err = []
    for _, g in up.iterrows():
        hfa = 0.0 if g["location"] == "Neutral" else bundle.elo.hfa
        want = pipeline.prob_to_elo_diff(float(np.clip(g["market_shin"], 1e-4, 1 - 1e-4))) - hfa
        err.append(abs(r[g["home_team_c"]] - r[g["away_team_c"]] - want))
    assert np.mean(err) < 40  # 32 lines, 31 free parameters: residual is small but nonzero
    assert abs(np.mean(list(r.values())) - 1500.0) < 1e-6
    # a strong prior pins the ratings to Elo
    r2 = pipeline.market_adjusted_ratings(feats, elo, bundle.elo.hfa, live, lam=1e6)
    assert max(abs(v - 1500.0) for v in r2.values()) < 1.0


def test_futures_mixture_is_coherent(games, bundle, feats):
    live = current_season(games)
    sim = pipeline.futures(games, bundle, live, n_sims=1000, feats=feats, write=False)
    assert len(sim) == 32
    assert abs(sim["p_super_bowl"].sum() - 1.0) < 1e-6
    assert abs(sim["p_conference"].sum() - 2.0) < 1e-6
    assert abs(sim["p_playoffs"].sum() - 14.0) < 1e-6
    assert sim.attrs["wins"].shape == (1000, 32)
    for d in sim["win_dist"]:
        assert abs(sum(d.values()) - 1.0) < 1e-6
    sharp = pipeline.futures(games, bundle, live, n_sims=1000, feats=feats, write=False, rating_noise=0.0)
    assert abs(sharp["p_super_bowl"].sum() - 1.0) < 1e-6 and sharp.attrs["rating_noise"] == 0.0
    assert sim.attrs["rating_noise"] == bundle.rating_noise_elo and sim.attrs["anchored"]
    # rating noise fattens the tails: the favourite's title odds shrink vs the no-noise run
    noisy = pipeline.futures(games, bundle, live, n_sims=8000, feats=feats, write=False)
    sharp8 = pipeline.futures(games, bundle, live, n_sims=8000, feats=feats, write=False, rating_noise=0.0)
    assert noisy["p_super_bowl"].max() < sharp8["p_super_bowl"].max()
    assert noisy["p_super_bowl"].std() < sharp8["p_super_bowl"].std()  # noise flattens the title distribution


def test_fair_yes_for_markets_covers_every_kind(games, bundle, feats):
    client = PolymarketClient(fixture_dir=FIXTURES)
    markets = client.enrich_with_books(client.markets_from_events(client.list_nfl_events()))
    matched = match_game_markets(markets, games)
    sim = pipeline.futures(games, bundle, current_season(games), n_sims=500, feats=feats, write=False)
    fair, game_ids = pipeline.fair_yes_for_markets(markets, matched, feats, sim, bundle)
    by_id = {m.market_id: m for m in markets}
    kinds = {by_id[k].kind for k in fair}
    assert {"moneyline", "spread", "total", "futures"} <= kinds
    assert all(0 < p < 1 for p in fair.values())
    assert all(game_ids[k] for k in fair if by_id[k].kind in ("moneyline", "spread", "total"))
    # spread fair value is the normal tail around the market-tilted fair margin
    row = matched[matched["kind"] == "spread"].iloc[0]
    g = feats.set_index("game_id").loc[row["game_id"]]
    p_home_cover = norm.cdf((g["fair_spread"] - row["line_home"]) / bundle.sigma)
    want = p_home_cover if row["yes_team"] == g["home_team_c"] else 1 - p_home_cover
    assert abs(fair[str(row["market_id"])] - want) < 1e-9
    # moneyline fair value is the ensemble probability of the YES team
    row = matched[matched["kind"] == "moneyline"].iloc[0]
    g = feats.set_index("game_id").loc[row["game_id"]]
    want = g["fair_prob"] if row["yes_team"] == g["home_team_c"] else 1 - g["fair_prob"]
    assert abs(fair[str(row["market_id"])] - want) < 1e-9


@pytest.mark.parametrize("line,question,want", [
    (11.0, "Will the Chiefs win 11+ regular season games?", 11),
    (9.5, "Will the Bears win over 9.5 games?", 10),
    (12.0, "Will the Eagles win at least 12 games?", 12),
    (None, "anything", None),
])
def test_win_total_threshold(line, question, want):
    assert pipeline._win_total_threshold(line, question) == want


def test_scan_on_fixtures(games, bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "FAIR_VALUES_PATH", tmp_path / "fair.jsonl")
    client = PolymarketClient(fixture_dir=FIXTURES)
    out = pipeline.scan(client, games, bundle, bankroll=1000.0, n_sims=500, log=lambda *_: None)
    assert len(out["fair"]) >= 30
    t = out["table"]
    assert (tmp_path / "scan.md").exists() and (tmp_path / "scan_priced_markets.csv").exists()
    if not t.empty:
        assert (t["stake_usd"] <= 30.0 + 1e-9).all()          # 3% of bankroll per position
        assert t["stake_usd"].sum() <= 150.0 + 1e-6            # 15% weekly cap
        assert (t["edge"] >= 0.03 - 1e-9).all()
    recorded = pd.read_json(tmp_path / "fair.jsonl", lines=True)
    assert len(recorded) == len(out["fair"])


def test_snapshot_and_clv_roundtrip(games, bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REPORTS_DIR", tmp_path)
    client = PolymarketClient(fixture_dir=FIXTURES)
    snap = tmp_path / "snap.jsonl"
    fair = tmp_path / "fair.jsonl"
    n = pipeline.take_snapshot(client, None, None, path=snap, log=lambda *_: None)
    assert n > 0 and snap.exists()
    fixture_ids = ["2026_03_KC_MIA", "2026_03_BAL_DAL", "2026_03_SEA_WAS", "2026_03_PHI_CHI"]
    mask = games["game_id"].isin(fixture_ids)
    assert mask.sum() == len(fixture_ids)
    # The live cache decides whether the fixture games have been played, so force both branches.
    # 1) Nothing settled yet: the report returns None and writes nothing.
    unplayed = games.copy()
    unplayed.loc[mask, ["played", "home_win", "home_score", "away_score", "result"]] = [False, np.nan, np.nan, np.nan, np.nan]
    assert pipeline.clv_report(unplayed, bundle, snapshot_path=snap, fair_path=fair, log=lambda *_: None) is None
    assert not (tmp_path / "clv.md").exists()
    # 2) All four settled today (inside the 10-day snapshot window): a report with one bet per market side.
    settled = games.copy()
    today = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    settled.loc[mask, "played"] = True
    settled.loc[mask, "home_win"] = [1.0, 0.0, 1.0, 0.0]
    settled.loc[mask, "game_date"] = today
    res = pipeline.clv_report(settled, bundle, snapshot_path=snap, fair_path=fair, log=lambda *_: None)
    assert res is not None
    assert (tmp_path / "clv.md").exists()
    assert set(res.bets["game_id"]) <= set(fixture_ids)
    assert res.bets["stake_flat"].ge(0).all()


def test_md_renderer_handles_types():
    df = pd.DataFrame({"a": [1.23456, np.nan], "b": ["x|y", None], "c": [True, False], "n": [1, 2]})
    md = pipeline._md(df)
    assert md.splitlines()[0] == "| a | b | c | n |"
    assert "1.235" in md and "x\\|y" in md and "True" in md
