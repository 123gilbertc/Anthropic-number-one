"""EPA-based team strength: per-game efficiency, pre-game ratings, and a logistic win model.

Pipeline
--------
1. :func:`team_game_epa` turns nflverse weekly team stats into one row per (game_id, team_c)
   with offensive EPA per play and the defensive EPA per play *allowed* (the opponent's
   offensive EPA per play in the same game, obtained by a self-join on ``game_id``).
2. :func:`epa_ratings` walks every team's schedule chronologically and computes, for every
   row of ``load_games()`` (played or not), the **pre-game** offensive and defensive rating:
   an exponentially-weighted mean of prior observations (weight ``0.5 ** (age / halflife)``,
   age counted in games), shrunk toward the league mean with ``prior_games`` pseudo-games,
   and carried into a new season with weight ``carryover``. Observations are opponent-adjusted
   iteratively: pass ``j`` subtracts the opponent's pass ``j-1`` pre-game rating from each raw
   observation; pass 0 uses raw numbers. Nothing after a game's kickoff ever feeds its rating.
3. :func:`epa_prob` maps ``epa_diff`` (home net minus away net) to P(home win) through a
   logistic ``expit(scale * epa_diff + hfa * is_home)``. ``scale`` alone comes from
   :func:`fit_epa_scale` (the contract form, maximum likelihood); ``(scale, hfa)`` jointly from
   :func:`fit_epa_hfa`. Read "Home-field advantage" below before using the contract form as a
   stand-alone fair probability.

Data notes (verified on ``data/cache/stats_team_week_2024.csv``)
-----------------------------------------------------------------
* ``passing_epa`` **includes sacks**. ``receiving_epa`` counts targets only, so the gap
  ``passing_epa - receiving_epa`` isolates non-target pass plays: regressing it on
  ``sacks_suffered`` gives a slope of -1.76 EPA per sack (r = -0.74; intercept -0.6 from
  throwaways/spikes and nflfastR's ``qb_epa`` fumble crediting). Super Bowl LIX check: KC took
  6 sacks, ``passing_epa`` -14.2 vs ``receiving_epa`` -2.4, i.e. -2.0 EPA per sack. Hence
  ``off_plays = attempts + carries + sacks_suffered`` and ``off_epa = passing_epa + rushing_epa``
  are a consistent numerator/denominator pair.
* 16 Jacksonville games in 2001-2002 carry only the opponent's row, and that row holds both
  teams' totals combined (63 attempts + 57 carries in ``2001_01_PIT_JAX``). Such rows get
  ``def_epa_pp = NaN`` from the self-join and are skipped as observations by
  :func:`epa_ratings`, which requires both sides of a game. ``1999_09_PHI_CAR`` has an extra
  row with a null team; rows without a team or game_id are dropped.
* ``passing_cpoe`` is missing before 2006 (no completion-probability model); ``cpoe`` is NaN there.
* Three played games (``1999_01_BAL_STL``, ``2000_03_SD_KC``, ``2000_06_BUF_MIA``) have no
  weekly stats at all; they contribute no observation and do not advance a team's clock.

League mean
-----------
The shrinkage target for season ``S`` is the mean offensive EPA per play over the previous
``LEAGUE_MEAN_SEASONS`` seasons (strictly before ``S``); the first season falls back to
``DEFAULT_LEAGUE_MEAN`` (0.0). Offense and defense share the target (league offense scored is
league defense allowed), so it cancels exactly in ``epa_diff`` and only shapes the absolute
ratings and the opponent adjustment.

Home-field advantage
--------------------
``epa_diff`` is symmetric in the two teams, so the contract form ``epa_prob(epa_diff, scale)``
returns exactly 0.5 for equally rated teams: it is **home-field blind**. Measured on played
2015-2024 games with a walk-forward scale its mean is 0.502 against an empirical home-win rate
of 0.551, i.e. every calibration decile under-predicts the home side by about 5 points (the
in-sample 2003-2024 mean residual ``y - p`` is +0.062). That bias exceeds any edge worth
trading, so a consumer that wants a fair probability from this module alone must either

* pass ``hfa`` (logits, applied to non-neutral games only) from :func:`fit_epa_hfa`, which fits
  ``scale`` and ``hfa`` jointly by maximum likelihood. On 2003-2024 ``hfa`` ~ 0.28 logits
  (P(home | equal ratings) ~ 0.57); the mean residual over home games is then zero by the score
  equation, and the walk-forward 2015-2024 log-loss improves from 0.6477 to 0.6434 (accuracy
  0.625 -> 0.629); or
* feed the contract-form probability into ``ensemble.LogisticStacker(use_intercept=True)``,
  whose intercept absorbs the same offset.

``hfa`` is fitted on played games only and, for a walk-forward evaluation, on seasons strictly
before the test season, exactly like ``scale``. The historical home edge has drifted down
(~0.33 logits fitting through 2014, ~0.29 through 2023), so a long window slightly overstates
it for the current season; that is the honest trade-off against a noisier short window.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import expit

from ..teams import canonical

#: Output columns of :func:`team_game_epa` (keys first, then the contract metrics).
TEAM_GAME_COLUMNS: tuple[str, ...] = (
    "game_id", "team_c", "opponent_c", "season", "week", "season_type",
    "off_plays", "off_epa", "off_epa_pp", "def_epa_pp", "pass_epa_pp", "rush_epa_pp", "cpoe",
)
#: Columns :func:`epa_ratings` appends to the games frame.
RATING_COLUMNS: tuple[str, ...] = (
    "home_off_epa", "home_def_epa", "away_off_epa", "away_def_epa",
    "epa_diff", "epa_n_home", "epa_n_away",
)
#: Seasons of history behind the pre-season league-mean prior.
LEAGUE_MEAN_SEASONS: int = 3
#: Prior used when no earlier season exists (first season in the data).
DEFAULT_LEAGUE_MEAN: float = 0.0
#: Default fit window for :func:`fit_epa_scale` (played games 2003-2024).
DEFAULT_SCALE_SEASONS: range = range(2003, 2025)

_STAT_COLUMNS = (
    "attempts", "completions", "carries", "sacks_suffered", "passing_epa", "rushing_epa", "passing_cpoe",
)
_REQUIRED_TEAM_WEEK = ("game_id", "team", "opponent_team", "season", "week", "season_type") + _STAT_COLUMNS
_REQUIRED_GAMES = ("game_id", "season", "week", "game_date", "home_team_c", "away_team_c")
_PROB_EPS = 1e-12
#: Search bounds for ``scale`` (logit slope) and ``hfa`` (logits) in the MLE fits.
_SCALE_BOUNDS: tuple[float, float] = (0.0, 500.0)
_HFA_BOUNDS: tuple[float, float] = (-5.0, 5.0)


# ----------------------------------------------------------------------------- per-game EPA
def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """Element-wise ``num / den`` with NaN where the denominator is zero or missing."""
    den = den.astype(float)
    return num.astype(float) / den.where(den > 0)


def team_game_epa(team_week: pd.DataFrame) -> pd.DataFrame:
    """Collapse nflverse weekly team stats to one row per ``(game_id, team_c)``.

    Parameters
    ----------
    team_week:
        Output of :func:`nfl_edge.data.load_team_week_stats` (regular + post season). The
        canonical ``team_c``/``opponent_c`` columns are derived if absent.

    Returns
    -------
    pd.DataFrame
        Columns :data:`TEAM_GAME_COLUMNS`:
        ``off_plays`` = attempts + carries + sacks suffered; ``off_epa`` = passing + rushing EPA
        (sacks included, see module notes); ``off_epa_pp`` = off_epa / off_plays;
        ``def_epa_pp`` = the opponent's ``off_epa_pp`` in the same game (NaN when the opponent's
        row is missing); ``pass_epa_pp`` = passing EPA per dropback (attempts + sacks);
        ``rush_epa_pp`` = rushing EPA per carry; ``cpoe`` = attempt-weighted completion percentage
        over expectation (NaN before 2006). Rows without a game_id or team are dropped;
        duplicate rows for one (game, team) are summed.
    """
    missing = [c for c in _REQUIRED_TEAM_WEEK if c not in team_week.columns]
    if missing:
        raise ValueError(f"team_week is missing required columns: {missing}")

    keep = team_week["game_id"].notna() & team_week["team"].notna()
    df = team_week.loc[keep, list(_REQUIRED_TEAM_WEEK)].copy()
    df["team_c"] = (
        team_week.loc[keep, "team_c"] if "team_c" in team_week.columns else df["team"].map(canonical)
    )
    df["opponent_c"] = (
        team_week.loc[keep, "opponent_c"] if "opponent_c" in team_week.columns
        else df["opponent_team"].map(canonical)
    )
    for c in _STAT_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    has_cpoe = df["passing_cpoe"].notna()
    df["_cpoe_num"] = (df["passing_cpoe"] * df["attempts"]).where(has_cpoe)
    df["_cpoe_den"] = df["attempts"].where(has_cpoe)

    keys = ["game_id", "team_c"]
    grouped = df.groupby(keys, sort=False)
    meta = grouped[["opponent_c", "season", "week", "season_type"]].first()
    sums = grouped[
        ["attempts", "completions", "carries", "sacks_suffered", "passing_epa", "rushing_epa",
         "_cpoe_num", "_cpoe_den"]
    ].sum(min_count=1)
    out = meta.join(sums).reset_index()

    out["off_plays"] = out["attempts"] + out["carries"] + out["sacks_suffered"]
    out["off_epa"] = out["passing_epa"] + out["rushing_epa"]
    out["off_epa_pp"] = _safe_div(out["off_epa"], out["off_plays"])
    out["pass_epa_pp"] = _safe_div(out["passing_epa"], out["attempts"] + out["sacks_suffered"])
    out["rush_epa_pp"] = _safe_div(out["rushing_epa"], out["carries"])
    out["cpoe"] = _safe_div(out["_cpoe_num"], out["_cpoe_den"])

    # Defensive EPA allowed = the opponent's offensive EPA per play in the same game.
    opp = out[["game_id", "team_c", "off_epa_pp"]].rename(
        columns={"team_c": "opponent_c", "off_epa_pp": "def_epa_pp"}
    )
    out = out.merge(opp, on=["game_id", "opponent_c"], how="left")

    out = out.sort_values(["season", "week", "game_id", "team_c"], kind="mergesort").reset_index(drop=True)
    out["season"] = out["season"].astype(int)
    out["week"] = out["week"].astype(int)
    return out[list(TEAM_GAME_COLUMNS)]


# ------------------------------------------------------------------------------- ratings
def _league_means(long: pd.DataFrame, seasons: Iterable[int]) -> dict[int, float]:
    """Pre-season league mean of offensive EPA/play for each season (previous seasons only)."""
    valid = long[long["valid"]]
    by_season = valid.groupby("season")["off_obs"].agg(["sum", "count"])
    out: dict[int, float] = {}
    for s in seasons:
        window = by_season[(by_season.index < s) & (by_season.index >= s - LEAGUE_MEAN_SEASONS)]
        n = float(window["count"].sum())
        out[int(s)] = float(window["sum"].sum() / n) if n > 0 else DEFAULT_LEAGUE_MEAN
    return out


def _ew_pregame(
    x: np.ndarray,
    valid: np.ndarray,
    new_season: np.ndarray,
    decay: float,
    prior_games: float,
    carryover: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Pre-game shrunk exponentially-weighted means, one pass over the (team, position) grid.

    ``x`` holds centered observations (NaN where none), ``valid`` marks usable observations and
    ``new_season`` marks the first slot of a new season. The loop runs over positions only
    (about 550), with every team updated at once. For each slot the pre-game value is
    ``S / (W + prior_games)`` where ``S = sum(w_j * x_j)``, ``W = sum(w_j)`` over earlier
    observations with ``w_j = decay ** age``; ``W`` is returned as the effective sample size.
    """
    n_teams, n_pos = x.shape
    S = np.zeros(n_teams)
    W = np.zeros(n_teams)
    pre = np.zeros((n_teams, n_pos))
    n_eff = np.zeros((n_teams, n_pos))
    for p in range(n_pos):
        if p:
            reset = new_season[:, p]
            S = np.where(reset, S * carryover, S)
            W = np.where(reset, W * carryover, W)
        denom = W + prior_games
        pre[:, p] = np.divide(S, denom, out=np.zeros(n_teams), where=denom > 0)
        n_eff[:, p] = W
        m = valid[:, p]
        xp = np.where(m, x[:, p], 0.0)
        S = np.where(m, decay * S + xp, S)
        W = np.where(m, decay * W + 1.0, W)
    return pre, n_eff


def epa_ratings(
    games: pd.DataFrame,
    team_game: pd.DataFrame,
    halflife: float = 5.0,
    prior_games: float = 6.0,
    carryover: float = 0.5,
    iters: int = 5,
) -> pd.DataFrame:
    """Pre-game opponent-adjusted EPA ratings for every row of ``games``.

    Parameters
    ----------
    games:
        Output of :func:`nfl_edge.data.load_games` (played and unplayed rows, any subset).
    team_game:
        Output of :func:`team_game_epa`. Only rows whose ``game_id`` is in ``games`` are used,
        and only games with both teams' rows count as observations.
    halflife:
        Observation weight halves every ``halflife`` games (age counted in the team's games).
    prior_games:
        Pseudo-observations at the league mean added to every estimate (shrinkage strength).
    carryover:
        Multiplier on the accumulated weight at a season boundary (0 = fresh start, 1 = none).
    iters:
        Total number of passes. Pass 0 uses raw observations; pass ``j >= 1`` adjusts each
        observation by the opponent's pass ``j-1`` pre-game rating for that same game
        (offense minus the opponent's defensive rating, defense minus the opponent's offensive
        rating, both relative to the league mean). ``iters=1`` gives unadjusted ratings.

    Returns
    -------
    pd.DataFrame
        ``games`` (same rows, order and index) plus :data:`RATING_COLUMNS`: ``home_off_epa``,
        ``home_def_epa``, ``away_off_epa``, ``away_def_epa`` (EPA/play; higher offense is better,
        lower defense is better), ``epa_diff`` = (home_off - home_def) - (away_off - away_def),
        and ``epa_n_home``/``epa_n_away`` = effective number of prior observations behind the
        rating (sum of decayed weights, 0 before a team's first game). Rows without history get
        the league-mean prior and ``epa_diff = 0``. An empty ``games`` frame returns an empty
        frame with the same columns appended.

    Look-ahead: the rating for a game uses only that team's earlier games (strictly earlier
    ``game_date``) and, through the opponent adjustment, opponents' earlier games. The league
    mean for season ``S`` uses seasons before ``S`` only.
    """
    if halflife <= 0:
        raise ValueError("halflife must be positive")
    if prior_games < 0:
        raise ValueError("prior_games must be non-negative")
    if not 0.0 <= carryover <= 1.0:
        raise ValueError("carryover must be in [0, 1]")
    if int(iters) < 1:
        raise ValueError("iters must be >= 1")
    missing = [c for c in _REQUIRED_GAMES if c not in games.columns]
    if missing:
        raise ValueError(f"games is missing required columns: {missing}")
    for c in ("game_id", "team_c", "off_epa_pp", "def_epa_pp"):
        if c not in team_game.columns:
            raise ValueError(f"team_game is missing required column {c!r}; use team_game_epa()")

    n_games = len(games)
    if n_games == 0:
        # A slate with no rows (offseason, post-Super-Bowl, a filter miss): hand back the empty
        # frame with the rating columns attached instead of numpy's empty-reduction error.
        out = games.copy()
        for c in RATING_COLUMNS:
            out[c] = np.empty(0, dtype=float)
        return out
    base = games.reset_index(drop=True)

    # Long table: one row per (game, side). side 0 = home, 1 = away.
    sides = []
    for side, col in ((0, "home_team_c"), (1, "away_team_c")):
        part = pd.DataFrame({
            "g": np.arange(n_games),
            "side": side,
            "game_id": base["game_id"].to_numpy(),
            "team": base[col].to_numpy(),
            "season": base["season"].to_numpy().astype(int),
            "week": base["week"].to_numpy(),
            "game_date": pd.to_datetime(base["game_date"]).to_numpy(),
        })
        sides.append(part)
    long = pd.concat(sides, ignore_index=True)

    obs = (
        team_game.loc[team_game["game_id"].isin(base["game_id"]), ["game_id", "team_c", "off_epa_pp", "def_epa_pp"]]
        .drop_duplicates(["game_id", "team_c"])
        .set_index(["game_id", "team_c"])
    )
    idx = pd.MultiIndex.from_arrays([long["game_id"], long["team"]])
    long["off_obs"] = obs["off_epa_pp"].reindex(idx).to_numpy(dtype=float)
    long["def_obs"] = obs["def_epa_pp"].reindex(idx).to_numpy(dtype=float)
    long["valid"] = np.isfinite(long["off_obs"]) & np.isfinite(long["def_obs"])

    mu_map = _league_means(long, sorted(long["season"].unique()))
    long["mu"] = long["season"].map(mu_map).astype(float)

    # Chronological position of each game within its team's schedule.
    long = long.sort_values(["team", "game_date", "season", "week", "g"], kind="mergesort").reset_index(drop=True)
    teams = pd.Index(sorted(long["team"].unique()))
    t_idx = teams.get_indexer(long["team"])
    p_idx = long.groupby("team", sort=False).cumcount().to_numpy()
    n_teams, n_pos = len(teams), int(p_idx.max()) + 1

    def to_grid(values: np.ndarray, fill: float) -> np.ndarray:
        grid = np.full((n_teams, n_pos), fill, dtype=float)
        grid[t_idx, p_idx] = values
        return grid

    valid_grid = to_grid(long["valid"].to_numpy(dtype=float), 0.0) > 0.5
    season_grid = to_grid(long["season"].to_numpy(dtype=float), -1.0)
    new_season = np.zeros((n_teams, n_pos), dtype=bool)
    new_season[:, 1:] = (season_grid[:, 1:] != season_grid[:, :-1]) & (season_grid[:, 1:] >= 0)

    off_c = long["off_obs"].to_numpy() - long["mu"].to_numpy()
    def_c = long["def_obs"].to_numpy() - long["mu"].to_numpy()
    off_c_grid = to_grid(off_c, np.nan)
    def_c_grid = to_grid(def_c, np.nan)

    g_arr = long["g"].to_numpy()
    side_arr = long["side"].to_numpy()
    opp_side = 1 - side_arr
    decay = 0.5 ** (1.0 / float(halflife))

    def run_pass(x_off: np.ndarray, x_def: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        pre_off, n_eff = _ew_pregame(x_off, valid_grid, new_season, decay, prior_games, carryover)
        pre_def, _ = _ew_pregame(x_def, valid_grid, new_season, decay, prior_games, carryover)
        return pre_off, pre_def, n_eff

    pre_off, pre_def, n_eff = run_pass(off_c_grid, def_c_grid)
    for _ in range(1, int(iters)):
        # Opponent's pre-game (centered) rating for the very same game, from the previous pass.
        off_by_game = np.full((n_games, 2), np.nan)
        def_by_game = np.full((n_games, 2), np.nan)
        off_by_game[g_arr, side_arr] = pre_off[t_idx, p_idx]
        def_by_game[g_arr, side_arr] = pre_def[t_idx, p_idx]
        adj_off = off_c - def_by_game[g_arr, opp_side]
        adj_def = def_c - off_by_game[g_arr, opp_side]
        pre_off, pre_def, n_eff = run_pass(to_grid(adj_off, np.nan), to_grid(adj_def, np.nan))

    off_by_game = np.full((n_games, 2), np.nan)
    def_by_game = np.full((n_games, 2), np.nan)
    n_by_game = np.full((n_games, 2), np.nan)
    mu_by_game = np.full(n_games, np.nan)
    off_by_game[g_arr, side_arr] = pre_off[t_idx, p_idx]
    def_by_game[g_arr, side_arr] = pre_def[t_idx, p_idx]
    n_by_game[g_arr, side_arr] = n_eff[t_idx, p_idx]
    mu_by_game[g_arr] = long["mu"].to_numpy()

    out = games.copy()
    out["home_off_epa"] = mu_by_game + off_by_game[:, 0]
    out["home_def_epa"] = mu_by_game + def_by_game[:, 0]
    out["away_off_epa"] = mu_by_game + off_by_game[:, 1]
    out["away_def_epa"] = mu_by_game + def_by_game[:, 1]
    out["epa_diff"] = (off_by_game[:, 0] - def_by_game[:, 0]) - (off_by_game[:, 1] - def_by_game[:, 1])
    out["epa_n_home"] = n_by_game[:, 0]
    out["epa_n_away"] = n_by_game[:, 1]
    return out


# ------------------------------------------------------------------------- probability
def _as_neutral(neutral) -> np.ndarray:
    """Coerce a neutral-site indicator to a boolean array.

    Accepts booleans / 0-1 numbers (NaN counts as not neutral) or ``location`` strings, where
    only ``"Neutral"`` (case-insensitive) is neutral; ``None``/NA entries are not neutral.
    """
    n = np.asarray(neutral)
    if n.dtype.kind == "f":
        return np.nan_to_num(n, nan=0.0).astype(bool)
    if n.dtype.kind in "OUS":
        def one(v) -> bool:
            if isinstance(v, str):
                return v.strip().lower() == "neutral"
            if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
                return False
            return bool(v)
        return np.fromiter((one(v) for v in n.ravel()), dtype=bool, count=n.size).reshape(n.shape)
    return n.astype(bool)


def neutral_site(games: pd.DataFrame) -> np.ndarray:
    """Boolean array, True where ``games["location"] == "Neutral"`` (Super Bowl, London, ...).

    All False when the frame has no ``location`` column, so every game counts as a home game.
    This is the ``neutral`` argument :func:`epa_prob` expects for a ``load_games()`` frame.
    """
    if "location" not in games.columns:
        return np.zeros(len(games), dtype=bool)
    return _as_neutral(games["location"].to_numpy())


def epa_prob(epa_diff, scale: float, hfa: float = 0.0, neutral=None) -> np.ndarray:
    """P(home win) = logistic(``scale`` * ``epa_diff`` + ``hfa`` * is_home).

    Parameters
    ----------
    epa_diff:
        Scalar, array or Series of home-net minus away-net ratings from :func:`epa_ratings`.
    scale:
        Logit slope, from :func:`fit_epa_scale` or :func:`fit_epa_hfa`.
    hfa:
        Home-field term in logits, added to every game that is not neutral. The default 0.0 is
        the contract form ``epa_prob(epa_diff, scale)``, which is home-field blind and biased
        ~5 points toward the away side (module notes); pass the value from :func:`fit_epa_hfa`
        for a stand-alone fair probability.
    neutral:
        Optional neutral-site indicator broadcastable to ``epa_diff``: booleans, 0/1 numbers or
        ``location`` strings (``"Neutral"``); :func:`neutral_site` builds it from a games frame.
        ``None`` treats every game as a home game.

    Returns
    -------
    np.ndarray
        Probabilities with the broadcast shape of ``epa_diff`` and ``neutral`` (0-d for scalars).
    """
    x = np.asarray(epa_diff, dtype=float) * float(scale)
    hfa = float(hfa)
    if neutral is None:
        x = x + hfa
    else:
        home = 1.0 - _as_neutral(neutral).astype(float)
        try:
            x = x + hfa * home
        except ValueError as exc:
            raise ValueError(
                f"neutral (shape {home.shape}) must be broadcastable to epa_diff (shape {x.shape})"
            ) from exc
    return np.asarray(expit(x), dtype=float)


def _fit_arrays(
    games: pd.DataFrame, seasons: Iterable[int] | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(epa_diff, home_win, is_home)`` arrays of the played games in ``seasons``."""
    for c in ("epa_diff", "home_win", "season"):
        if c not in games.columns:
            raise ValueError(f"games is missing required column {c!r}; run epa_ratings() first")
    season_set = set(int(s) for s in (DEFAULT_SCALE_SEASONS if seasons is None else seasons))
    mask = games["season"].isin(season_set) & games["home_win"].notna() & games["epa_diff"].notna()
    fit = games.loc[mask]
    if fit.empty:
        raise ValueError("no played games with epa_diff in the requested seasons")
    x = fit["epa_diff"].to_numpy(dtype=float)
    y = fit["home_win"].to_numpy(dtype=float)
    h = 1.0 - neutral_site(fit).astype(float)
    return x, y, h


def _nll(y: np.ndarray, z: np.ndarray) -> float:
    """Mean binomial negative log-likelihood of logits ``z`` (ties, y = 0.5, count half)."""
    p = np.clip(expit(z), _PROB_EPS, 1.0 - _PROB_EPS)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log1p(-p)))


def _fit_scale(x: np.ndarray, y: np.ndarray, offset) -> float:
    """Bounded Brent search for the scale minimising ``_nll(y, scale * x + offset)``."""
    res = minimize_scalar(
        lambda s: _nll(y, s * x + offset), bounds=_SCALE_BOUNDS, method="bounded", options={"xatol": 1e-6}
    )
    if not res.success:
        raise RuntimeError(f"scale fit did not converge: {res.message}")
    return float(res.x)


def fit_epa_scale(games: pd.DataFrame, seasons: Iterable[int] | None = None) -> float:
    """Maximum-likelihood ``scale`` for the contract form ``epa_prob(epa_diff, scale)``.

    Parameters
    ----------
    games:
        Output of :func:`epa_ratings` (needs ``epa_diff``, ``home_win`` and ``season``).
    seasons:
        Seasons to fit on; defaults to :data:`DEFAULT_SCALE_SEASONS` (2003-2024). For a
        walk-forward evaluation pass only seasons strictly before the test season.

    Returns
    -------
    float
        The scale minimising the mean binomial log-loss (ties count half) with no home-field
        term. Bounded to ``[0, 500]``; the loss is convex in ``scale`` so the bounded Brent
        search is exact. The result is home-field blind (module notes); :func:`fit_epa_hfa`
        fits ``scale`` and ``hfa`` together and is what a stand-alone probability should use.
    """
    x, y, _ = _fit_arrays(games, seasons)
    return _fit_scale(x, y, 0.0)


def fit_epa_hfa(games: pd.DataFrame, seasons: Iterable[int] | None = None) -> tuple[float, float]:
    """Joint maximum-likelihood ``(scale, hfa)`` for :func:`epa_prob` on played games.

    Fits ``P(home win) = expit(scale * epa_diff + hfa * is_home)`` with ``is_home = 0`` for
    ``location == "Neutral"`` rows (:func:`neutral_site`; all ones when the column is absent,
    making ``hfa`` a plain intercept). The loss is a two-feature logistic regression without
    intercept and hence convex; L-BFGS-B with the analytic gradient starts from the
    :func:`fit_epa_scale` solution and ``hfa = 0`` and converges in ~15 iterations. Bounds:
    ``scale`` in ``[0, 500]``, ``hfa`` in ``[-5, 5]`` logits (keeps degenerate tiny fits finite).

    Parameters
    ----------
    games:
        Output of :func:`epa_ratings` (needs ``epa_diff``, ``home_win``, ``season``; ``location``
        is used when present).
    seasons:
        Seasons to fit on; defaults to :data:`DEFAULT_SCALE_SEASONS`. Walk-forward: seasons
        strictly before the test season, exactly as for :func:`fit_epa_scale`.

    Returns
    -------
    tuple[float, float]
        ``(scale, hfa)``. On 2003-2024: scale ~ 8.4, hfa ~ 0.28 logits (P(home | equal
        ratings) ~ 0.57). At the optimum the mean residual over home games is exactly zero.
    """
    x, y, h = _fit_arrays(games, seasons)

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = theta[0] * x + theta[1] * h
        resid = expit(z) - y
        return _nll(y, z), np.array([np.mean(resid * x), np.mean(resid * h)])

    x0 = np.array([_fit_scale(x, y, 0.0), 0.0])
    res = minimize(
        objective, x0, jac=True, method="L-BFGS-B", bounds=[_SCALE_BOUNDS, _HFA_BOUNDS],
        options={"ftol": 1e-14, "gtol": 1e-10, "maxiter": 500},
    )
    if not res.success and float(np.max(np.abs(res.jac))) > 1e-6:
        raise RuntimeError(f"(scale, hfa) fit did not converge: {res.message}")
    return float(res.x[0]), float(res.x[1])
