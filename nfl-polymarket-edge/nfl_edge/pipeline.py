"""End-to-end orchestration used by the CLI.

Flow
----
1. ``build_features``: Elo, EPA ratings and Shin-devigged market probabilities on every game.
2. ``fit_models``: tune Elo, fit the EPA scale, margin sigmas and the logistic stackers.
3. ``evaluate``: strict walk-forward scoring vs the closing line and a betting simulation at
   vigged closing odds. This is the honesty report.
4. ``predict_week``: fair probabilities and margins for the upcoming games.
5. ``futures``: Monte Carlo season simulation on *market-anchored* ratings.
6. ``scan``: fair value for every Polymarket market, edge finder, arbitrage and consistency checks.
7. ``take_snapshot`` / ``clv_report``: record live prices and measure realised closing-line value.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from scipy.stats import norm

from .backtest.engine import BacktestConfig, BacktestResult, backtest_snapshots, backtest_vs_closing, report_markdown
from .config import DATA_DIR, DEFAULT_STRATEGY, NFL_MARGIN_SIGMA, REPORTS_DIR, SNAPSHOT_DIR, StrategyConfig, ensure_dirs
from .data import current_season, current_week, load_games, load_team_week_stats
from .markets.polymarket import (
    PolyMarket,
    PolymarketClient,
    fee_for_market,
    load_snapshots,
    match_game_markets,
    snapshot,
)
from .models import calibration as cal
from .models.elo import EloModel, EloParams, fit_elo
from .models.ensemble import LogisticStacker, add_logits, fit_final, walk_forward
from .models.epa import epa_prob, epa_ratings, fit_epa_hfa, team_game_epa
from .models.market import fit_spread_sigma, market_probs
from .odds import american_to_prob, devig, prob_to_elo_diff
from .sim.season import SimConfig, simulate_season, win_total_probs
from .strategy.edge import consistency_checks, find_arbitrage, find_edges, opportunities_table
from .teams import CURRENT_TEAMS, FULL_NAMES

MODELS_PATH = DATA_DIR / "models.json"
FAIR_VALUES_PATH = SNAPSHOT_DIR / "fair_values.jsonl"
SNAPSHOT_PATH = SNAPSHOT_DIR / "polymarket_nfl.jsonl"

GAME_FEATURES = ["market_shin", "elo_prob", "epa_prob"]
MODEL_ONLY_FEATURES = ["elo_prob", "epa_prob"]
FIRST_TUNE_SEASON = 2003
# Wider than the module default so the coordinate descent is not pinned at a grid boundary.
ELO_GRID = {
    "k": [12, 16, 20, 24, 28, 32], "hfa": [30, 35, 45, 55, 65], "mov": [True, False],
    "season_regress": [0.2, 0.33, 0.5, 0.67, 0.8], "rest_bonus": [0, 15, 25, 40],
    "qb_change_penalty": [0, 20, 40, 60, 80, 100], "playoff_mult": [1.0, 1.2],
}
FUTURES_COLUMNS = {
    "super_bowl": "p_super_bowl",
    "conference": "p_conference",
    "division": "p_division",
    "playoffs": "p_playoffs",
    "top_seed": "p_top_seed",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md(df: pd.DataFrame, floatfmt: str = ".3f", index: bool = False) -> str:
    """Minimal GitHub-markdown table renderer (no tabulate dependency)."""
    d = df.reset_index() if index else df
    cols = [str(c) for c in d.columns]

    def fmt(v) -> str:
        if isinstance(v, (float, np.floating)):
            return "" if not np.isfinite(v) else format(float(v), floatfmt)
        if isinstance(v, (bool, np.bool_)):
            return str(bool(v))
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        return str(v).replace("|", "\\|")

    body = ["| " + " | ".join(fmt(v) for v in r) + " |" for r in d.itertuples(index=False)]
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"] + body)


# ----------------------------------------------------------------------------- model bundle
@dataclass
class ModelBundle:
    """Everything needed to price a game or a season, serialisable to JSON."""

    elo: EloParams = field(default_factory=EloParams)
    epa: dict[str, float] = field(
        default_factory=lambda: {"halflife": 5.0, "prior_games": 6.0, "carryover": 0.5, "iters": 5, "scale": 15.0, "hfa": 0.0}
    )
    sigma: float = NFL_MARGIN_SIGMA        # final-margin sigma around the fair spread
    sigma_total: float = 13.5              # total-points sigma around the fair total
    l2: float = 5.0                        # stacker ridge strength
    stacker: dict | None = None            # LogisticStacker.to_dict() on GAME_FEATURES
    stacker_model_only: dict | None = None  # on MODEL_ONLY_FEATURES (games without a line)
    market_anchor_lambda: float = 0.2      # ridge weight pulling market-anchored ratings to Elo
    rating_noise_elo: float = 35.0         # sd of per-simulation rating draws: current-strength uncertainty (drift is simulated)
    train_through: int = 0
    fitted_at: str = ""
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["elo"] = self.elo.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ModelBundle":
        d = dict(d)
        d["elo"] = EloParams.from_dict(d["elo"])
        return cls(**d)

    def save(self, path: Path = MODELS_PATH) -> Path:
        ensure_dirs()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=float))
        return path

    @classmethod
    def load(cls, path: Path = MODELS_PATH) -> "ModelBundle":
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} not found; run `nfl-edge fit` first")
        return cls.from_dict(json.loads(Path(path).read_text()))

    def game_stacker(self) -> LogisticStacker | None:
        return LogisticStacker.from_dict(self.stacker) if self.stacker else None

    def model_only_stacker(self) -> LogisticStacker | None:
        return LogisticStacker.from_dict(self.stacker_model_only) if self.stacker_model_only else None


# ----------------------------------------------------------------------------- features
def build_features(games: pd.DataFrame, bundle: ModelBundle, team_week: pd.DataFrame | None = None) -> pd.DataFrame:
    """Attach Elo, EPA and Shin-devigged market probabilities to every game row (strictly pre-game)."""
    feats = EloModel(bundle.elo).run(games)
    if team_week is None:
        team_week = load_team_week_stats()
    feats = epa_ratings(
        feats,
        team_game_epa(team_week),
        halflife=bundle.epa["halflife"],
        prior_games=bundle.epa["prior_games"],
        carryover=bundle.epa["carryover"],
        iters=int(bundle.epa["iters"]),
    )
    feats["epa_prob"] = _epa_prob(feats, bundle)
    feats["market_shin"] = market_probs(feats, "shin", bundle.sigma).to_numpy(dtype=float)
    return feats


def _epa_prob(feats: pd.DataFrame, bundle: ModelBundle) -> np.ndarray:
    return epa_prob(feats["epa_diff"].to_numpy(dtype=float), bundle.epa["scale"],
                    hfa=float(bundle.epa.get("hfa", 0.0)), neutral=feats["location"])


def _played_before(games: pd.DataFrame, season: int) -> pd.DataFrame:
    return games[games["played"] & (games["season"] < season)]


def fit_total_sigma(games: pd.DataFrame) -> float:
    g = games[games["played"] & games["total_line"].notna()]
    resid = (g["home_score"] + g["away_score"] - g["total_line"]).to_numpy(dtype=float)
    return float(np.sqrt(np.mean(resid ** 2))) if resid.size else 13.5


def fit_models(
    games: pd.DataFrame,
    tune_elo: bool = True,
    quick: bool = False,
    l2: float = 5.0,
    train_through: int | None = None,
    log=print,
) -> ModelBundle:
    """Fit every component. Tuning and descriptive fits use seasons strictly before the live season."""
    live = current_season(games)
    train_through = train_through or live - 1
    train_seasons = range(FIRST_TUNE_SEASON, train_through + 1)
    bundle = ModelBundle(l2=l2, train_through=train_through)

    if tune_elo:
        grid = ELO_GRID
        if quick:
            grid = {"k": [16, 20, 24], "hfa": [45, 55, 65], "season_regress": [0.25, 0.33], "rest_bonus": [0, 25]}
        log(f"tuning Elo on {train_seasons.start}-{train_seasons.stop - 1} ({'quick' if quick else 'full'} grid)...")
        params, results = fit_elo(games, train_seasons, grid=grid, passes=1 if quick else 2)
        bundle.elo = params
        best = results.iloc[0]
        bundle.notes["elo_tuning"] = {
            "logloss": float(best["logloss"]), "accuracy": float(best["accuracy"]), "n": int(best["n"]),
            "combos": int(len(results)),
        }
        log(f"  best {params} -> logloss {best['logloss']:.4f}, acc {best['accuracy']:.3f}")

    hist = _played_before(games, train_through + 1)
    bundle.sigma = float(fit_spread_sigma(hist))
    bundle.sigma_total = fit_total_sigma(hist)
    log(f"margin sigma {bundle.sigma:.2f}, total sigma {bundle.sigma_total:.2f}")

    team_week = load_team_week_stats()
    feats = build_features(games, bundle, team_week)
    scale, hfa = fit_epa_hfa(feats, seasons=train_seasons)
    bundle.epa["scale"], bundle.epa["hfa"] = float(scale), float(hfa)
    feats["epa_prob"] = _epa_prob(feats, bundle)
    log(f"EPA scale {scale:.2f}, home-field {hfa:.3f} logits")

    train = feats[feats["played"] & feats["home_win"].notna() & (feats["season"] >= FIRST_TUNE_SEASON)]
    train = train[train["season"] <= train_through]
    st = fit_final(train, GAME_FEATURES, l2=l2)
    bundle.stacker = st.to_dict()
    mo = fit_final(train, MODEL_ONLY_FEATURES, l2=l2)
    bundle.stacker_model_only = mo.to_dict()
    bundle.notes["stacker_coefs"] = {**st.coef_, "intercept": st.intercept_}
    bundle.notes["model_only_coefs"] = {**mo.coef_, "intercept": mo.intercept_}
    bundle.fitted_at = _utcnow()
    log(f"stacker coefs {bundle.notes['stacker_coefs']}")
    return bundle


# ----------------------------------------------------------------------------- evaluation
def evaluate(
    games: pd.DataFrame,
    bundle: ModelBundle,
    first_test: int = 2012,
    last_test: int | None = None,
    min_edges: Iterable[float] = (0.02, 0.05),
    log=print,
) -> dict[str, Any]:
    """Walk-forward evaluation vs the closing line plus betting simulations at vigged closing odds."""
    feats = build_features(games, bundle)
    live = current_season(games)
    last_test = last_test or live - 1
    log(f"walk-forward {first_test}-{last_test} on {GAME_FEATURES} ...")
    wf = walk_forward(feats, GAME_FEATURES, first_test, last_test, l2=bundle.l2)
    coefs = wf.attrs.get("coefs", {})
    wf_mo = walk_forward(feats, MODEL_ONLY_FEATURES, first_test, last_test, l2=bundle.l2)
    wf = wf.copy()
    wf["model_only_prob"] = wf_mo["fair_prob"].reindex(wf.index)

    probs = {
        "closing line (multiplicative devig)": "market_prob",
        "closing line (Shin devig)": "market_shin",
        "Elo": "elo_prob",
        "EPA ratings": "epa_prob",
        "Elo+EPA stack (no market)": "model_only_prob",
        "ensemble (market+Elo+EPA)": "fair_prob",
    }
    mask = wf["played"] & wf["home_win"].notna()
    for c in probs.values():
        mask &= wf[c].notna()
    scored = wf[mask]
    y = scored["home_win"].to_numpy(dtype=float)
    summary = cal.summarize(y, {k: scored[c].to_numpy(dtype=float) for k, c in probs.items()})
    boot = cal.paired_bootstrap(y, scored["fair_prob"].to_numpy(float), scored["market_shin"].to_numpy(float))
    boot_mo = cal.paired_bootstrap(y, scored["model_only_prob"].to_numpy(float), scored["market_shin"].to_numpy(float))
    calib = cal.calibration_table(y, scored["fair_prob"].to_numpy(float), bins=10)

    backtests: dict[str, BacktestResult] = {}
    for me in min_edges:
        cfg = BacktestConfig(start_season=first_test, end_season=last_test, min_edge=me, devig="shin")
        backtests[f"ensemble @ min_edge {me:.0%}"] = backtest_vs_closing(scored, "fair_prob", cfg)
    backtests["Elo alone @ min_edge 5% (cautionary)"] = backtest_vs_closing(
        scored, "elo_prob", BacktestConfig(start_season=first_test, end_season=last_test, min_edge=0.05, devig="shin")
    )
    backtests["every closing line (pays the vig)"] = backtest_vs_closing(
        scored, "market_shin", BacktestConfig(start_season=first_test, end_season=last_test, min_edge=-1.0, devig="shin")
    )

    md = _evaluation_markdown(first_test, last_test, summary, boot, boot_mo, calib, coefs, backtests, bundle)
    ensure_dirs()
    (REPORTS_DIR / "backtest.md").write_text(md)
    keep = ["game_id", "season", "week", "home_team_c", "away_team_c", "home_win", "spread_line",
            "market_prob", "market_shin", "elo_prob", "epa_prob", "model_only_prob", "fair_prob"]
    scored[keep].to_csv(REPORTS_DIR / "walk_forward_predictions.csv", index=False)
    log(md)
    return {"summary": summary, "bootstrap": boot, "bootstrap_model_only": boot_mo, "calibration": calib,
            "coefs": coefs, "backtests": backtests, "markdown": md, "predictions": scored}


def _evaluation_markdown(first, last, summary, boot, boot_mo, calib, coefs, backtests, bundle) -> str:
    lines = [f"# Walk-forward evaluation {first}-{last}", "",
             f"Generated {_utcnow()}. Every prediction for season S uses only games before S; "
             "Elo/EPA ratings update sequentially and never see the game they predict.", "",
             "## Probability quality (lower log-loss / Brier is better)", "",
             _md(summary, ".4f", index=True), "",
             "## Does the ensemble beat the closing line?", "",
             f"Paired bootstrap, ensemble minus Shin-devigged closing line, log-loss: "
             f"diff {boot['diff']:+.5f}, 95% CI [{boot['ci_lo']:+.5f}, {boot['ci_hi']:+.5f}], "
             f"P(ensemble better) = {boot['p_a_better']:.2f}.", "",
             f"Model-only stack minus closing line: diff {boot_mo['diff']:+.5f}, "
             f"95% CI [{boot_mo['ci_lo']:+.5f}, {boot_mo['ci_hi']:+.5f}].", "",
             "Read this honestly: a CI that spans zero means the model does not beat the market at the close. "
             "Edge must come from Polymarket prices that diverge from this fair value, not from out-modelling the sharps.", "",
             "## Ensemble calibration", "", _md(calib, ".3f"), "",
             "## Stacker weights by test season (logit space)", ""]
    if coefs:
        ct = pd.DataFrame(coefs).T
        ct.index.name = "season"
        lines += [_md(ct, ".3f", index=True), ""]
    lines += ["## Betting simulations at vigged closing moneylines", "",
              "Flat stakes 1% of starting bankroll; Kelly path is quarter-Kelly capped at 3% per bet and 15% per week. "
              "ROI CI is a bootstrap over bets.", ""]
    rows = []
    for name, r in backtests.items():
        s = r.summary
        rows.append({"strategy": name, "bets": s.get("n_bets", 0), "hit rate": s.get("hit_rate", float("nan")),
                     "flat ROI": s.get("roi_flat", float("nan")), "ROI CI lo": s.get("roi_ci_lo", float("nan")),
                     "ROI CI hi": s.get("roi_ci_hi", float("nan")), "Kelly final bankroll": s.get("final_bankroll_kelly", float("nan")),
                     "Kelly max DD": s.get("max_drawdown_kelly", float("nan"))})
    lines += [_md(pd.DataFrame(rows), ".3f"), ""]
    lines += ["## Model parameters", "", f"Elo: {bundle.elo}", f"EPA: {bundle.epa}",
              f"margin sigma {bundle.sigma:.2f}, total sigma {bundle.sigma_total:.2f}, ridge l2 {bundle.l2}", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- weekly predictions
def score_games(feats: pd.DataFrame, bundle: ModelBundle) -> pd.DataFrame:
    """Add ``fair_prob`` (ensemble when a line exists, else the model-only stack) and ``fair_spread``."""
    out = add_logits(feats, GAME_FEATURES)
    st = bundle.game_stacker()
    mo = bundle.model_only_stacker()
    if st is None or mo is None:
        raise ValueError("bundle has no fitted stackers; run fit_models first")
    fair = st.predict_proba(out)
    fallback = mo.predict_proba(out)
    out["fair_prob_source"] = np.where(np.isfinite(fair), "ensemble", "model-only")
    out["fair_prob"] = np.where(np.isfinite(fair), fair, fallback)
    p = np.clip(out["fair_prob"].to_numpy(float), 1e-6, 1 - 1e-6)
    m = np.clip(out["market_shin"].to_numpy(float), 1e-6, 1 - 1e-6)
    line = out["spread_line"].to_numpy(float)
    model_margin = bundle.sigma * norm.ppf(p)
    with np.errstate(invalid="ignore"):
        tilt = bundle.sigma * (norm.ppf(p) - norm.ppf(m))
    # Sportsbook spreads and moneylines are not normally consistent around key numbers (3, 7), so the
    # fair margin is the market spread shifted by the ensemble's disagreement, not a spread rebuilt
    # from a win probability. Without a line, fall back to the pure model margin.
    out["fair_spread"] = np.where(np.isfinite(line) & np.isfinite(tilt), line + tilt, model_margin)
    return out


def predict_week(games: pd.DataFrame, bundle: ModelBundle, season: int | None = None, week: int | None = None,
                 write: bool = True) -> pd.DataFrame:
    season = season or current_season(games)
    week = week or current_week(games, season)
    feats = score_games(build_features(games, bundle), bundle)
    rows = feats[(feats["season"] == season) & (feats["week"] == week)].copy()
    rows["fair_vs_market"] = rows["fair_prob"] - rows["market_shin"]
    cols = ["game_id", "gameday", "away_team_c", "home_team_c", "location", "spread_line", "total_line",
            "market_shin", "elo_prob", "epa_prob", "fair_prob", "fair_spread", "fair_vs_market", "fair_prob_source",
            "elo_home_pre", "elo_away_pre", "home_rest", "away_rest", "qb_change_home", "qb_change_away",
            "home_qb_name", "away_qb_name", "played", "home_score", "away_score"]
    table = rows[cols].sort_values(["gameday", "game_id"]).reset_index(drop=True)
    if write:
        ensure_dirs()
        stem = REPORTS_DIR / f"predictions_{season}_wk{week:02d}"
        table.to_csv(stem.with_suffix(".csv"), index=False)
        stem.with_suffix(".md").write_text(_predictions_markdown(table, season, week, bundle))
    return table


def _predictions_markdown(t: pd.DataFrame, season: int, week: int, bundle: ModelBundle) -> str:
    view = pd.DataFrame({
        "date": t["gameday"], "away": t["away_team_c"], "home": t["home_team_c"],
        "line (home)": t["spread_line"], "market P(home)": t["market_shin"], "Elo": t["elo_prob"],
        "EPA": t["epa_prob"], "fair P(home)": t["fair_prob"], "fair spread": t["fair_spread"],
        "fair - market": t["fair_vs_market"], "source": t["fair_prob_source"],
        "notes": [_game_notes(r) for _, r in t.iterrows()],
    })
    return "\n".join([f"# {season} week {week} fair prices", "", f"Generated {_utcnow()} with models fitted {bundle.fitted_at}.",
                      "Probabilities are P(home win). 'fair - market' is the ensemble's disagreement with the "
                      "Shin-devigged closing/current line; positive favours the home side.", "",
                      _md(view), ""])


def _game_notes(r) -> str:
    notes = []
    if r.get("qb_change_home"):
        notes.append(f"home QB change ({r.get('home_qb_name')})")
    if r.get("qb_change_away"):
        notes.append(f"away QB change ({r.get('away_qb_name')})")
    if pd.notna(r.get("home_rest")) and r["home_rest"] >= 10:
        notes.append("home off bye")
    if pd.notna(r.get("away_rest")) and r["away_rest"] >= 10:
        notes.append("away off bye")
    if str(r.get("location")) == "Neutral":
        notes.append("neutral site")
    if r.get("played"):
        notes.append(f"final {int(r['away_score'])}-{int(r['home_score'])}")
    return "; ".join(notes)


# ----------------------------------------------------------------------------- season simulation
def market_adjusted_ratings(games: pd.DataFrame, elo_ratings: Mapping[str, float], hfa: float,
                            season: int, lam: float = 0.2) -> dict[str, float]:
    """Ridge-solve team ratings from current market lines, shrunk toward Elo.

    Each unplayed game with a line contributes ``r_home - r_away + hfa = market Elo difference``;
    the Elo ratings act as a prior with weight ``lam`` (in squared-Elo units per team). The result
    carries what sharp books currently believe (injuries, suspensions, trades) into the season
    simulation, which is where futures crowds are usually sloppiest.
    """
    teams = list(CURRENT_TEAMS)
    idx = {t: i for i, t in enumerate(teams)}
    g = games[(games["season"] == season) & (~games["played"]) & games["market_shin"].notna()]
    prior = np.array([elo_ratings.get(t, 1500.0) for t in teams], dtype=float)
    if g.empty or lam <= 0 and len(g) < len(teams):
        return {t: float(v) for t, v in zip(teams, prior)}
    rows, rhs = [], []
    for _, r in g.iterrows():
        h, a = r["home_team_c"], r["away_team_c"]
        if h not in idx or a not in idx:
            continue
        x = np.zeros(len(teams))
        x[idx[h]] = 1.0
        x[idx[a]] = -1.0
        neutral = str(r.get("location")) == "Neutral"
        d = prob_to_elo_diff(float(np.clip(r["market_shin"], 1e-4, 1 - 1e-4))) - (0.0 if neutral else hfa)
        rows.append(x)
        rhs.append(d)
    X = np.asarray(rows)
    d = np.asarray(rhs)
    # minimise ||X r - d||^2 + lam ||r - prior||^2  -> (X'X + lam I) r = X'd + lam prior
    A = X.T @ X + lam * np.eye(len(teams))
    b = X.T @ d + lam * prior
    r = np.linalg.solve(A, b)
    r += prior.mean() - r.mean()  # ratings are only identified up to a constant
    return {t: float(v) for t, v in zip(teams, r)}


SIM_PROB_COLUMNS = ["mean_wins", "p_playoffs", "p_division", "p_top_seed", "p_conference", "p_super_bowl"]


def futures(games: pd.DataFrame, bundle: ModelBundle, season: int | None = None, n_sims: int = 20000,
            seed: int = 0, anchor_to_market: bool = True, feats: pd.DataFrame | None = None,
            rating_noise: float | None = None, write: bool = True) -> pd.DataFrame:
    """Per-team playoff / division / conference / Super Bowl probabilities and win distributions.

    Ratings are market-anchored by default. The simulation is "hot" (each simulated season updates
    its own Elo after every game, so strength drifts as it does in reality) and every simulation
    draws each team's current rating from ``N(rating, rating_noise^2)``; ignoring either
    overstates favourites and understates longshots.
    """
    season = season or current_season(games)
    if feats is None:
        feats = build_features(games, bundle)
    noise = bundle.rating_noise_elo if rating_noise is None else float(rating_noise)
    model = EloModel(bundle.elo)
    model.run(games[games["season"] <= season])
    elo = model.ratings()
    ratings = (market_adjusted_ratings(feats, elo, bundle.elo.hfa, season, bundle.market_anchor_lambda)
               if anchor_to_market else dict(elo))
    cfg = SimConfig(n_sims=n_sims, seed=seed, hfa_elo=bundle.elo.hfa, playoff_mult=bundle.elo.playoff_mult,
                    hot=True, k=bundle.elo.k, mov=bundle.elo.mov, rating_noise_sd=noise)
    sim = simulate_season(games, season, ratings, cfg).copy()
    sim["elo"] = pd.Series(elo).reindex(sim.index)
    sim["rating_used"] = pd.Series(ratings).reindex(sim.index)
    sim.attrs.update({"rating_noise": noise, "anchored": anchor_to_market})
    if write:
        ensure_dirs()
        out = sim.drop(columns=["win_dist"]).copy()
        out["win_dist"] = [json.dumps({int(k): round(float(v), 4) for k, v in d.items()}) for d in sim["win_dist"]]
        out.to_csv(REPORTS_DIR / f"futures_{season}.csv")
        (REPORTS_DIR / f"futures_{season}.md").write_text(_futures_markdown(sim, season, n_sims, anchor_to_market, noise))
    return sim


def _futures_markdown(sim: pd.DataFrame, season: int, n_sims: int, anchored: bool, noise: float) -> str:
    t = sim.sort_values("p_super_bowl", ascending=False)
    view = pd.DataFrame({
        "team": [FULL_NAMES.get(i, i) for i in t.index], "rating": t["rating_used"], "Elo": t["elo"],
        "mean wins": t["mean_wins"], "playoffs": t["p_playoffs"], "division": t["p_division"],
        "top seed": t["p_top_seed"], "conference": t["p_conference"], "Super Bowl": t["p_super_bowl"],
    })
    return "\n".join([f"# {season} season simulation ({n_sims:,} runs)", "",
                      f"Generated {_utcnow()}. Ratings: {'market-anchored Elo' if anchored else 'Elo'}; hot simulation "
                      f"(ratings drift within each simulated season) with per-simulation rating noise sd {noise:.0f} Elo; "
                      "played games fixed, NFL tiebreakers and bracket applied.", "",
                      _md(view), ""])


# ----------------------------------------------------------------------------- fair values for markets
def _win_total_threshold(line: float | None, question: str) -> int | None:
    if line is None or not np.isfinite(line):
        return None
    q = question.lower()
    if float(line).is_integer():
        return int(line) if ("+" in q or "or more" in q or "at least" in q or "over" not in q) else int(line) + 1
    return int(math.floor(line)) + 1


def fair_yes_for_markets(markets: Iterable[PolyMarket], matched: pd.DataFrame, scored: pd.DataFrame,
                         sim: pd.DataFrame | None, bundle: ModelBundle) -> tuple[dict[str, float], dict[str, str]]:
    """Fair P(YES) per market_id and market_id -> game_id, for every market kind we can price."""
    fair: dict[str, float] = {}
    game_ids: dict[str, str] = {}
    by_game = scored.set_index("game_id")
    if not matched.empty:
        for _, m in matched.iterrows():
            gid = m["game_id"]
            if gid not in by_game.index:
                continue
            g = by_game.loc[gid]
            p_home = float(g["fair_prob"])
            fair_margin = float(g["fair_spread"])
            yes_home = m["yes_team"] == g["home_team_c"]
            kind = m["kind"]
            p = None
            if kind == "moneyline":
                p = p_home if yes_home else 1.0 - p_home
            elif kind == "spread":
                line_home = m.get("line_home")
                if line_home is not None and np.isfinite(line_home):
                    p_home_cover = float(norm.cdf((fair_margin - float(line_home)) / bundle.sigma))
                    p = p_home_cover if yes_home else 1.0 - p_home_cover
            elif kind == "total":
                line = m.get("line")
                if line is not None and np.isfinite(line):
                    fair_total = _fair_total(g, bundle)
                    p = float(norm.cdf((fair_total - float(line)) / bundle.sigma_total))  # YES = Over
            if p is not None and np.isfinite(p):
                fair[str(m["market_id"])] = float(np.clip(p, 1e-4, 1 - 1e-4))
                game_ids[str(m["market_id"])] = str(gid)
    if sim is not None:
        wins = sim.attrs.get("wins")
        for mk in markets:
            if mk.kind != "futures" or not mk.team or mk.team not in sim.index:
                continue
            col = FUTURES_COLUMNS.get(mk.futures_type or "")
            p = None
            if col:
                p = float(sim.loc[mk.team, col])
            elif mk.futures_type == "win_total" and wins is not None:
                n = _win_total_threshold(mk.line, mk.question)
                if n is not None:
                    p = win_total_probs(wins, mk.team).get(n, 0.0)
            if p is not None and np.isfinite(p):
                fair[mk.market_id] = float(np.clip(p, 1e-4, 1 - 1e-4))
    return fair, game_ids


def _fair_total(g: pd.Series, bundle: ModelBundle) -> float:
    """Closing total shifted by the devigged over/under price; falls back to the line itself."""
    line = float(g["total_line"]) if pd.notna(g.get("total_line")) else float("nan")
    over, under = g.get("over_odds"), g.get("under_odds")
    if pd.notna(over) and pd.notna(under) and np.isfinite(line):
        p_over = devig([american_to_prob(float(over)), american_to_prob(float(under))], "shin")[0]
        return line + bundle.sigma_total * float(norm.ppf(np.clip(p_over, 1e-4, 1 - 1e-4)))
    return line


# ----------------------------------------------------------------------------- scan / snapshot / clv
def load_markets(client: PolymarketClient, with_books: bool = True) -> list[PolyMarket]:
    events = client.list_nfl_events()
    markets = client.markets_from_events(events)
    return client.enrich_with_books(markets) if with_books else markets


def scan(client: PolymarketClient, games: pd.DataFrame, bundle: ModelBundle, cfg: StrategyConfig = DEFAULT_STRATEGY,
         bankroll: float = 1000.0, n_sims: int = 10000, record_fair: bool = True, log=print) -> dict[str, Any]:
    """Price every reachable NFL market, then find edges, arbitrage and inconsistencies."""
    markets = load_markets(client)
    season = current_season(games)
    feats = score_games(build_features(games, bundle), bundle)
    matched = match_game_markets(markets, games)
    sim = futures(games, bundle, season, n_sims=n_sims, feats=feats, write=False)
    fair, game_ids = fair_yes_for_markets(markets, matched, feats, sim, bundle)
    fees = {m.market_id: fee_for_market(m, cfg.fee_rate) for m in markets}
    opps = find_edges(markets, fair, cfg, bankroll, game_ids=game_ids, fees=fees)
    arbs = find_arbitrage(markets)
    checks = consistency_checks(markets, sim)
    table = opportunities_table(opps)
    priced = _priced_table(markets, fair, game_ids)
    if record_fair and fair:
        _append_fair_values(fair, game_ids)
    ensure_dirs()
    table.to_csv(REPORTS_DIR / "scan_opportunities.csv", index=False)
    priced.to_csv(REPORTS_DIR / "scan_priced_markets.csv", index=False)
    md = _scan_markdown(markets, priced, table, arbs, checks, cfg, bankroll)
    (REPORTS_DIR / "scan.md").write_text(md)
    log(md)
    return {"markets": markets, "fair": fair, "opportunities": opps, "table": table, "arbitrage": arbs,
            "checks": checks, "priced": priced, "sim": sim, "markdown": md}


def _priced_table(markets: list[PolyMarket], fair: Mapping[str, float], game_ids: Mapping[str, str]) -> pd.DataFrame:
    rows = []
    for m in markets:
        if m.market_id not in fair:
            continue
        bid = m.book.best_bid if m.book else None
        ask = m.book.best_ask if m.book else None
        rows.append({"market_id": m.market_id, "kind": m.kind, "type": m.futures_type or m.kind, "question": m.question,
                     "yes_outcome": m.yes_outcome, "game_id": game_ids.get(m.market_id), "fair_yes": fair[m.market_id],
                     "price_yes": m.price_yes, "best_bid": bid, "best_ask": ask,
                     "fair_minus_price": fair[m.market_id] - (ask if ask is not None else m.price_yes),
                     "liquidity": m.liquidity})
    t = pd.DataFrame(rows)
    return t.sort_values("fair_minus_price", key=lambda s: s.abs(), ascending=False) if not t.empty else t


def _scan_markdown(markets, priced, table, arbs, checks, cfg, bankroll) -> str:
    lines = [f"# Polymarket NFL scan {_utcnow()}", "",
             f"{len(markets)} markets loaded, {len(priced)} priced. Bankroll ${bankroll:,.0f}; "
             f"min edge {cfg.min_edge:.0%}, Kelly fraction {cfg.kelly_fraction}, per-position cap {cfg.max_stake_fraction:.0%}, "
             f"weekly cap {cfg.max_weekly_exposure:.0%}, slippage buffer {cfg.slippage_buffer:.2f}, fee {cfg.fee_rate:.2%}.", "",
             "## Opportunities (after fees, slippage, depth and exposure caps)", ""]
    if table.empty:
        lines.append("None. No market clears the edge threshold; that is the normal state of an efficient book.")
    else:
        show = table[[c for c in ("question", "side", "kind", "fair_prob", "price", "effective_price", "edge", "ev",
                                  "stake_fraction", "stake_usd", "liquidity_usd", "reason") if c in table.columns]]
        lines.append(_md(show))
    lines += ["", "## Risk-free inconsistencies", ""]
    lines.append(_md(pd.DataFrame(arbs)) if arbs else "None found.")
    lines += ["", "## Consistency checks (market vs logic / simulation)", ""]
    lines.append(_md(pd.DataFrame(checks)) if checks else "No violations.")
    lines += ["", "## All priced markets (largest fair-vs-price gap first)", ""]
    if not priced.empty:
        lines.append(_md(priced.head(60)))
    lines.append("")
    return "\n".join(lines)


def _append_fair_values(fair: Mapping[str, float], game_ids: Mapping[str, str], ts: str | None = None,
                        path: Path | None = None) -> int:
    ensure_dirs()
    path = Path(path) if path is not None else FAIR_VALUES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = ts or _utcnow()
    with open(path, "a") as fh:
        for mid, p in fair.items():
            fh.write(json.dumps({"ts": ts, "market_id": mid, "fair_prob": p, "game_id": game_ids.get(mid)}) + "\n")
    return len(fair)


def take_snapshot(client: PolymarketClient, games: pd.DataFrame | None = None, bundle: ModelBundle | None = None,
                  path: Path | None = None, fair_path: Path | None = None, n_sims: int = 4000, log=print) -> int:
    """Record every reachable NFL market's book, and the model's fair value when models exist."""
    path = Path(path) if path is not None else SNAPSHOT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    markets = load_markets(client)
    ts = _utcnow()
    n = snapshot(markets, path, ts)
    if games is not None and bundle is not None:
        feats = score_games(build_features(games, bundle), bundle)
        matched = match_game_markets(markets, games)
        sim = futures(games, bundle, current_season(games), n_sims=n_sims, feats=feats, write=False)
        fair, game_ids = fair_yes_for_markets(markets, matched, feats, sim, bundle)
        _append_fair_values(fair, game_ids, ts, path=fair_path)
        log(f"{ts}: {n} snapshot rows, {len(fair)} fair values")
    else:
        log(f"{ts}: {n} snapshot rows")
    return n


def _snapshot_game_map(snaps: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """market_id -> game_id, yes_won, kickoff for recorded moneyline markets."""
    ml = snaps[snaps["kind"] == "moneyline"].dropna(subset=["home", "away"])
    if ml.empty:
        return pd.DataFrame(columns=["market_id", "game_id", "yes_won", "game_date", "gametime", "season", "week"])
    first = ml.sort_values("ts").groupby("market_id").first().reset_index()
    played = games[games["played"]]
    rows = []
    for _, r in first.iterrows():
        when = pd.to_datetime(r["ts"], utc=True).tz_localize(None)
        cand = played[(played["home_team_c"].isin([r["home"], r["away"]])) & (played["away_team_c"].isin([r["home"], r["away"]]))]
        cand = cand[(cand["game_date"] - when).abs() <= pd.Timedelta(days=10)]
        if cand.empty:
            continue
        g = cand.iloc[(cand["game_date"] - when).abs().argsort().iloc[0]]
        market_home_won = g["home_win"] if g["home_team_c"] == r["home"] else 1.0 - g["home_win"]
        rows.append({"market_id": r["market_id"], "game_id": g["game_id"], "yes_won": float(market_home_won),
                     "game_date": g["game_date"], "gametime": g.get("gametime"), "season": g["season"], "week": g["week"],
                     "close_home_shin": g.get("market_shin", np.nan)})
    return pd.DataFrame(rows)


def clv_report(games: pd.DataFrame, bundle: ModelBundle | None, snapshot_path: Path | None = None,
               fair_path: Path | None = None, cfg: BacktestConfig | None = None, log=print) -> BacktestResult | None:
    """Realised closing-line value and P&L on recorded Polymarket moneyline quotes for settled games."""
    snapshot_path = Path(snapshot_path) if snapshot_path is not None else SNAPSHOT_PATH
    fair_path = Path(fair_path) if fair_path is not None else FAIR_VALUES_PATH
    if not Path(snapshot_path).exists():
        log("no snapshots recorded yet; run `nfl-edge snapshot` on a schedule first")
        return None
    snaps = load_snapshots(snapshot_path)
    if "market_shin" not in games.columns:
        games = games.copy()
        games["market_shin"] = market_probs(games, "shin").to_numpy(float)
    gmap = _snapshot_game_map(snaps, games)
    if gmap.empty:
        log("no recorded moneyline market has settled yet")
        return None
    stream = snaps.merge(gmap[["market_id", "game_id"]], on="market_id", how="inner")
    fair_probs = None
    if Path(fair_path).exists():
        fv = pd.read_json(fair_path, lines=True)
        fv = fv[fv["market_id"].astype(str).isin(stream["market_id"].astype(str))]
        if not fv.empty:
            fair_probs = fv[["ts", "market_id", "fair_prob"]].assign(market_id=lambda d: d["market_id"].astype(str))
    cfg = cfg or BacktestConfig(min_edge=DEFAULT_STRATEGY.min_edge, edge_metric="prob")
    mode = "recorded model fair values"
    if fair_probs is None:
        mode = "HINDSIGHT closing line as fair value (diagnostic only: the close is not known at entry)"
        fp = gmap[["market_id", "close_home_shin"]].rename(columns={"close_home_shin": "fair_prob"})
        fair_probs = fp.assign(market_id=lambda d: d["market_id"].astype(str))
    results = gmap[["game_id", "yes_won", "game_date", "gametime", "season", "week"]].drop_duplicates("game_id")
    res = backtest_snapshots(stream, fair_probs=fair_probs, results=results, cfg=cfg)
    md = f"# Polymarket closing-line value report ({mode})\n\n" + report_markdown(res)
    ensure_dirs()
    (REPORTS_DIR / "clv.md").write_text(md)
    log(md)
    return res
