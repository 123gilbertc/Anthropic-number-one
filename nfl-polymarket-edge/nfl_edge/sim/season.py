"""Monte-Carlo rest-of-season simulator: games -> standings -> tiebreakers -> playoff bracket.

Design
------
* Played regular-season results (and, once the regular season is complete, any played playoff
  games) of the requested season are fixed; every remaining regular-season game is a Bernoulli
  draw with the Elo expectation of the supplied ``ratings`` (home-field ``hfa_elo`` unless
  ``location == "Neutral"``).
* "Hot" simulation (``SimConfig.hot``, the default): every simulated season carries its own copy
  of the ratings and updates them after each simulated game with the same 538-style rule the Elo
  model uses (``k`` times the margin-of-victory multiplier times the surprise).  This is how
  FiveThirtyEight ran its playoff odds and it matters for the *tails*: with frozen ratings every
  remaining game is an independent coin with a fixed bias, and the resulting Poisson-binomial win
  totals are markedly too narrow (per-team win SD about 1.9 static vs about 2.6 hot), which would
  show the edge scanner phantom value against long-shot win totals and against favourites' low
  totals.  ``hot=False`` restores the frozen-ratings model (exact Elo probabilities per game);
  ``rating_noise_sd`` adds per-simulation Gaussian rating uncertainty on top of either.
* Outcomes live in one ``(n_sims, n_games)`` matrix of home-win indicators; team wins, division
  / conference records, strength of victory and point differential are all matrix products of
  it with the schedule's one-hot incidence matrices, so the hot path is vectorized numpy.  The
  hot simulation runs in schedule order over batches of games with no team in common (one batch
  per NFL week), so it is still ~20 vectorized steps rather than a per-game loop.
* Standings apply the NFL tiebreaker ladder.  Ties are resolved in batches of equal size: an
  ``(n, k)`` array of tied team indices per simulation feeds gather-based statistics, so Python
  loops run once per (tie size, ladder step), never once per simulation or tied set.
* Seeds feed a vectorized bracket (top seed(s) on a bye, reseeding each round, home field to the
  better seed, neutral Super Bowl) that uses each simulation's end-of-season ratings when hot.

Look-ahead safety
-----------------
* ``SimConfig.as_of`` masks every row of the season dated ``as_of`` or later (and any row with an
  unknown date) as unplayed inside the module, so a backtest passes the full frame plus a date
  and never has to mask columns by hand.
* The ``played`` flag is never trusted on its own: it must agree with ``home_win`` and, when the
  score columns are present, with the scores; played rows need a finite ``home_win`` in
  {0, 0.5, 1} and a finite, sign-consistent ``margin``.  Any disagreement raises ``ValueError``
  rather than leaking hidden results or silently producing NaN standings.
* Played playoff rows are only fixed into the bracket when the regular season is complete;
  a mid-season snapshot that still carries them is warned about and simulated from scratch.

Point differential in simulated games comes from a margin drawn from the normal margin model
conditioned on the simulated winner, so the sixth tiebreaker is meaningful rather than a coin
flip.  Nothing here looks at any game outside the requested season.
"""
from __future__ import annotations

import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri

from ..config import ELO_POINTS_PER_SPREAD_POINT, NFL_MARGIN_SIGMA
from ..odds import elo_diff_to_prob
from ..teams import DIVISIONS, canonical

CONFERENCES: tuple[str, str] = ("AFC", "NFC")
DIVISION_NAMES: tuple[str, ...] = ("East", "North", "South", "West")
DIVISION_ORDER: tuple[tuple[str, str], ...] = tuple(
    (conf, div) for conf in CONFERENCES for div in DIVISION_NAMES
)
FIRST_SUPPORTED_SEASON = 2002  # current eight-division alignment
PLAYOFF_ROUNDS: tuple[str, ...] = ("WC", "DIV", "CON", "SB")
SCORE_COLUMNS: tuple[str, ...] = ("home_score", "away_score", "result", "total")

# Tiebreaker ladders.  "common" = record versus opponents every tied club played.
DIVISION_STEPS: tuple[str, ...] = ("h2h", "division", "common", "conference", "sov", "pdiff", "random")
WILDCARD_STEPS: tuple[str, ...] = ("h2h", "conference", "common", "sov", "pdiff", "random")
_MIN_COMMON_GAMES = {"division": 1, "wildcard": 4}
_TOL = 1e-9


@dataclass
class SimConfig:
    """Simulation settings.

    n_sims: number of Monte-Carlo seasons.
    seed: numpy Generator seed; identical seed + inputs give identical output.
    hfa_elo: home-field advantage in Elo points for non-neutral games (playoffs: better seed).
    playoff_mult: multiplier on the adjusted Elo difference in playoff games (538 used 1.2).
    hot: update each simulation's ratings after every simulated game (538 "hot" simulation) with
        ``k`` and the margin-of-victory multiplier (``mov``), regular season and bracket alike.
        ``False`` freezes ``ratings`` for the whole season, which understates season-total
        variance (see the module docstring).  Pass the fitted ``EloParams`` values for ``k`` and
        ``mov`` so the drift matches the model that produced ``ratings``.
    rating_noise_sd: standard deviation (Elo points) of an independent per-simulation, per-team
        perturbation applied to ``ratings`` before the season starts; 0 disables it.
    as_of: date (or anything ``pd.Timestamp`` accepts); every row of the season dated ``as_of``
        or later, and every row without a date, is treated as unplayed.  ``None`` uses the
        frame's own played state (which is still validated for consistency).
    """

    n_sims: int = 20000
    seed: int = 0
    hfa_elo: float = 55.0
    playoff_mult: float = 1.0
    hot: bool = True
    k: float = 20.0
    mov: bool = True
    rating_noise_sd: float = 0.0
    as_of: str | date | datetime | pd.Timestamp | None = None


@dataclass
class Standings:
    """Per-simulation standings from ``standings_from_results``.

    All team matrices share the column order ``teams``.  ``seeds[:, c, k]`` is the team index of
    seed ``k + 1`` in conference ``CONFERENCES[c]``; ``division_winners[:, d]`` the winner of
    ``divisions[d]``.
    """

    teams: list[str]
    wins: np.ndarray
    games: np.ndarray
    seeds: np.ndarray
    division_winners: np.ndarray
    divisions: list[str]
    n_playoff: int
    n_byes: int


def playoff_format(season: int) -> tuple[int, int]:
    """(playoff teams per conference, first-round byes per conference) for ``season``."""
    return (7, 1) if season >= 2020 else (6, 2)


def _elo_prob_array(diff: np.ndarray) -> np.ndarray:
    """Vectorized twin of ``nfl_edge.odds.elo_diff_to_prob`` (per-simulation matchups)."""
    return 1.0 / (1.0 + np.power(10.0, -np.asarray(diff, dtype=float) / 400.0))


# --------------------------------------------------------------------------- tiebreak engine
class _TiebreakEngine:
    """Vectorized standings arithmetic over a ``(n_sims, n_games)`` home-win matrix.

    Ties are resolved in batches: a batch is ``(sims, G)`` with ``G`` an ``(n, k)`` array of the
    ``k`` tied team indices in each of ``n`` simulations.  Every tiebreak statistic is a gather on
    ``G``, so the amount of Python work depends only on the number of distinct tie *sizes* and
    ladder steps, never on the number of simulations or distinct tied sets.
    """

    def __init__(
        self,
        results: np.ndarray,
        home_idx: np.ndarray,
        away_idx: np.ndarray,
        n_teams: int,
        team_conf: np.ndarray,
        team_div: np.ndarray,
        margins: np.ndarray | None,
        rng: np.random.Generator,
    ) -> None:
        self.W = np.ascontiguousarray(results, dtype=float)
        self.n_sims, self.n_games = self.W.shape
        self.home = home_idx
        self.away = away_idx
        self.n_teams = n_teams
        self.team_div = team_div
        self.rng = rng

        self.H = np.zeros((self.n_games, n_teams))
        self.A = np.zeros((self.n_games, n_teams))
        self.H[np.arange(self.n_games), home_idx] = 1.0
        self.A[np.arange(self.n_games), away_idx] = 1.0
        self.games_between = np.zeros((n_teams, n_teams), dtype=np.int16)  # small dtype: it is gathered per sim
        np.add.at(self.games_between, (home_idx, away_idx), 1)
        np.add.at(self.games_between, (away_idx, home_idx), 1)

        self.games_count = self.H.sum(0) + self.A.sum(0)
        self.wins = self._team_totals(self.W)
        self.win_pct = self.wins / np.maximum(self.games_count, 1.0)

        div_mask = (team_div[home_idx] == team_div[away_idx]).astype(float)
        conf_mask = (team_conf[home_idx] == team_conf[away_idx]).astype(float)
        self.div_games = (self.H * div_mask[:, None]).sum(0) + (self.A * div_mask[:, None]).sum(0)
        self.conf_games = (self.H * conf_mask[:, None]).sum(0) + (self.A * conf_mask[:, None]).sum(0)
        self.div_pct = self._team_totals(self.W, div_mask) / np.maximum(self.div_games, 1.0)
        self.conf_pct = self._team_totals(self.W, conf_mask) / np.maximum(self.conf_games, 1.0)
        self.div_ok = self.div_games > 0
        self.conf_ok = self.conf_games > 0

        self._margins = None if margins is None else np.ascontiguousarray(margins, dtype=float)
        self._sov: np.ndarray | None = None
        self._pdiff: np.ndarray | None = None
        self._h2h2: np.ndarray | None = None

    # ---------------------------------------------------------------- aggregates
    def _team_totals(self, W: np.ndarray, game_weight: np.ndarray | None = None) -> np.ndarray:
        """Per-(sim, team) wins over the (optionally weighted) games."""
        if game_weight is None:
            return W @ self.H + (1.0 - W) @ self.A
        return (W * game_weight) @ self.H + ((1.0 - W) * game_weight) @ self.A

    @property
    def h2h2(self) -> np.ndarray:
        """``(n_sims, n_teams, n_teams)`` int8: twice the wins of the row team over the column team."""
        if self._h2h2 is None:
            m = np.zeros((self.n_sims, self.n_teams, self.n_teams), dtype=np.int8)
            w2 = np.ascontiguousarray(np.rint(2.0 * self.W).T).astype(np.int8)
            for g in range(self.n_games):
                m[:, self.home[g], self.away[g]] += w2[g]
                m[:, self.away[g], self.home[g]] += 2 - w2[g]
            self._h2h2 = m
        return self._h2h2

    @property
    def sov(self) -> np.ndarray:
        """Strength of victory: combined win pct of every opponent a team beat (ties count half)."""
        if self._sov is None:
            num = (self.W * self.wins[:, self.away]) @ self.H + ((1.0 - self.W) * self.wins[:, self.home]) @ self.A
            den = (self.W * self.games_count[self.away]) @ self.H + (
                (1.0 - self.W) * self.games_count[self.home]
            ) @ self.A
            self._sov = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0)
        return self._sov

    @property
    def pdiff(self) -> np.ndarray:
        """Net points per (sim, team); zeros when no margins were supplied."""
        if self._pdiff is None:
            if self._margins is None:
                self._pdiff = np.zeros((self.n_sims, self.n_teams))
            else:
                self._pdiff = self._margins @ self.H - self._margins @ self.A
        return self._pdiff

    # ---------------------------------------------------------------- tiebreak statistics
    def _metric(self, name: str, sims: np.ndarray, G: np.ndarray, rules: str) -> np.ndarray:
        """Tiebreak statistic ``(n, k)`` for batch ``G``; rows where the step does not apply are 0."""
        n, k = G.shape
        rows = np.arange(n)
        metric = np.zeros((n, k))
        if name == "h2h":
            # applicability depends on the schedule only; gather simulated wins for those rows alone
            pair_games = self.games_between[G[:, :, None], G[:, None, :]]  # (n, k, k)
            games = pair_games.sum(2)
            if k == 2 or rules == "division":
                applicable = (games >= 1).all(1)
                a = np.flatnonzero(applicable)
                if a.size:
                    wins = self.h2h2[sims[a][:, None, None], G[a][:, :, None], G[a][:, None, :]].sum(2) / 2.0
                    metric[a] = wins / games[a]
            else:  # wild card, 3+ clubs: only a clean sweep (won or lost every game vs the others) applies
                played_all = (pair_games + np.eye(k, dtype=np.int16)[None] > 0).all(2)  # (n, k)
                applicable = played_all.any(1)
                a = np.flatnonzero(applicable)
                if a.size:
                    wins = self.h2h2[sims[a][:, None, None], G[a][:, :, None], G[a][:, None, :]].sum(2) / 2.0
                    swept_all = (wins >= games[a] - _TOL) & played_all[a]
                    lost_all = (wins <= _TOL) & played_all[a]
                    metric[a] = swept_all.astype(float) - lost_all.astype(float)
        elif name == "division":
            metric = self.div_pct[sims[:, None], G]
            applicable = self.div_ok[G].all(1)
        elif name == "conference":
            metric = self.conf_pct[sims[:, None], G]
            applicable = self.conf_ok[G].all(1)
        elif name == "common":
            schedule = self.games_between[G]  # (n, k, n_teams)
            common = (schedule > 0).all(1)  # opponents every tied club played
            common[rows[:, None], G] = False
            games = (schedule * common[:, None, :]).sum(2)
            applicable = (games >= _MIN_COMMON_GAMES[rules]).all(1)
            a = np.flatnonzero(applicable)
            if a.size:
                wins = (self.h2h2[sims[a][:, None], G[a], :] * common[a][:, None, :]).sum(2) / 2.0
                metric[a] = wins / games[a]
        elif name == "sov":
            metric = self.sov[sims[:, None], G]
            applicable = np.ones(n, dtype=bool)
        elif name == "pdiff":
            metric = self.pdiff[sims[:, None], G]
            applicable = np.ones(n, dtype=bool)
        elif name == "random":
            metric = self.rng.random((n, k))
            applicable = np.ones(n, dtype=bool)
        else:
            raise ValueError(f"unknown tiebreak step {name!r}")
        metric = np.array(metric, dtype=float)
        metric[~applicable] = 0.0  # a non-applicable step leaves every club tied
        return metric

    # ---------------------------------------------------------------- resolution
    def _wildcard_prefilter(self, sims: np.ndarray, G: np.ndarray) -> np.ndarray:
        """Wild-card rule 0: keep one club per division (division ladder), then rank survivors."""
        n, k = G.shape
        divs = self.team_div[G]
        keep = np.ones((n, k), dtype=bool)
        for d in np.unique(divs):
            mask = divs == d
            cnt = mask.sum(1)
            for m in np.unique(cnt[cnt >= 2]):
                r = np.flatnonzero(cnt == m)
                sub = G[r][mask[r]].reshape(-1, m)
                winner = self.resolve(sims[r], sub, "division", 0)
                keep[r] &= ~mask[r] | (G[r] == winner[:, None])
        out = np.empty(n, dtype=np.int64)
        n_keep = keep.sum(1)
        for m in np.unique(n_keep):
            r = np.flatnonzero(n_keep == m)
            survivors = G[r][keep[r]].reshape(-1, m)
            out[r] = self.resolve(sims[r], survivors, "wildcard", 0, prefiltered=True)
        return out

    def resolve(
        self, sims: np.ndarray, G: np.ndarray, rules: str, step: int = 0, prefiltered: bool = False
    ) -> np.ndarray:
        """Winner (team index) of the tie among ``G[s]`` in simulation ``sims[s]`` under ``rules``.

        Each ladder step keeps the clubs with the best statistic.  A unique best wins; if every
        club is still tied the next step applies; if some clubs drop out the ladder restarts from
        step 0 with the survivors (the NFL "revert to step 1" rule).  ``random`` always resolves.
        """
        n, k = G.shape
        if k == 1:
            return G[:, 0].copy()
        if rules == "wildcard" and step == 0 and not prefiltered:
            divs = np.sort(self.team_div[G], axis=1)
            dup = (divs[:, 1:] == divs[:, :-1]).any(1)
            if dup.any():
                out = np.empty(n, dtype=np.int64)
                out[dup] = self._wildcard_prefilter(sims[dup], G[dup])
                clean = ~dup
                if clean.any():
                    out[clean] = self.resolve(sims[clean], G[clean], "wildcard", 0, prefiltered=True)
                return out
        steps = DIVISION_STEPS if rules == "division" else WILDCARD_STEPS
        name = steps[step]
        metric = self._metric(name, sims, G, rules)
        rows = np.arange(n)
        out = G[rows, metric.argmax(1)]  # correct wherever the best statistic is unique
        if name == "random":
            return out
        tied = metric >= metric.max(axis=1, keepdims=True) - _TOL
        n_tied = tied.sum(1)
        all_tied = n_tied == k
        if all_tied.any():
            out[all_tied] = self.resolve(sims[all_tied], G[all_tied], rules, step + 1, prefiltered=True)
        for m in np.unique(n_tied[(n_tied > 1) & ~all_tied]):
            r = np.flatnonzero(n_tied == m)
            survivors = G[r][tied[r]].reshape(-1, m)
            out[r] = self.resolve(sims[r], survivors, rules, 0)
        return out

    def pick(self, cand: np.ndarray, rules: str) -> np.ndarray:
        """Best team per sim among the candidate mask ``cand`` (n_sims, n_teams): win pct, then ties."""
        masked = np.where(cand, self.win_pct, -np.inf)
        tied = cand & (masked >= masked.max(axis=1, keepdims=True) - _TOL)
        n_tied = tied.sum(1)
        out = tied.argmax(1).astype(np.int64)
        for m in np.unique(n_tied[n_tied >= 2]):
            sims = np.flatnonzero(n_tied == m)
            G = np.nonzero(tied[sims])[1].reshape(-1, m)
            out[sims] = self.resolve(sims, G, rules)
        return out


# --------------------------------------------------------------------------- schedule helpers
def _team_index(teams: Sequence[str], codes: pd.Series) -> np.ndarray:
    lookup = {t: i for i, t in enumerate(teams)}
    mapped = codes.map(canonical)
    unknown = sorted(set(mapped[~mapped.isin(lookup)]))
    if unknown:
        raise ValueError(f"teams not in the current division alignment: {unknown}")
    return mapped.map(lookup).to_numpy(dtype=np.int64)


def _as_of_timestamp(as_of: str | date | datetime | pd.Timestamp | None) -> pd.Timestamp | None:
    """``as_of`` as a midnight Timestamp (games dated that day count as unplayed), or None."""
    if as_of is None:
        return None
    ts = pd.Timestamp(as_of)
    if pd.isna(ts):
        raise ValueError(f"as_of must be a valid date, got {as_of!r}")
    return ts.normalize()


def _mask_unplayed(g: pd.DataFrame, rows: np.ndarray) -> pd.DataFrame:
    """Set ``rows`` (boolean mask) to a consistent unplayed state in every result column."""
    g = g.copy()
    g.loc[rows, "played"] = False
    for col in ("home_win", "margin", *SCORE_COLUMNS):
        if col in g.columns:
            g[col] = g[col].astype(float)
            g.loc[rows, col] = np.nan
    return g


def _describe_rows(g: pd.DataFrame, bad: np.ndarray, limit: int = 5) -> str:
    ids = g["game_id"].astype(str) if "game_id" in g.columns else pd.Series(g.index.astype(str), index=g.index)
    shown = ", ".join(ids[bad].head(limit))
    more = f" (+{int(bad.sum()) - limit} more)" if bad.sum() > limit else ""
    return f"{int(bad.sum())} rows: {shown}{more}"


def _validate_played_state(g: pd.DataFrame) -> pd.DataFrame:
    """Return ``g`` with a ``played`` column that is provably consistent with the result columns.

    Played-ness is derived from the data (scores when present, else ``home_win``) and every other
    signal must agree with it; disagreement means the caller masked some columns and not others,
    which would either leak hidden results or feed NaN into the standings.
    """
    if "home_win" not in g.columns:
        raise ValueError("games frame needs a 'home_win' column (see nfl_edge.data.load_games)")
    g = g.copy()
    hw = g["home_win"].to_numpy(dtype=float)
    has_hw = np.isfinite(hw)
    if "home_score" in g.columns and "away_score" in g.columns:
        has_scores = g["home_score"].notna().to_numpy() & g["away_score"].notna().to_numpy()
        bad = has_scores != has_hw
        if bad.any():
            raise ValueError(
                "home_win disagrees with home_score/away_score (partial masking?) on "
                + _describe_rows(g, bad)
            )
        played = has_scores
    else:
        played = has_hw
    if "played" in g.columns:
        flag = g["played"].astype("boolean").fillna(False).to_numpy(dtype=bool)
        bad = flag != played
        if bad.any():
            raise ValueError(
                "'played' flag disagrees with the result columns (stale flag?) on " + _describe_rows(g, bad)
            )
    g["played"] = played
    valid_hw = np.isin(hw[played], (0.0, 0.5, 1.0))
    if not valid_hw.all():
        bad = np.zeros(len(g), dtype=bool)
        bad[np.flatnonzero(played)[~valid_hw]] = True
        raise ValueError("played rows need home_win in {0, 0.5, 1}: " + _describe_rows(g, bad))
    if "margin" in g.columns:
        m = g["margin"].to_numpy(dtype=float)
        sign_ok = np.sign(m[played]) == np.sign(hw[played] - 0.5)
        ok = np.isfinite(m[played]) & sign_ok
        if not ok.all():
            bad = np.zeros(len(g), dtype=bool)
            bad[np.flatnonzero(played)[~ok]] = True
            raise ValueError("played rows need a finite margin agreeing with home_win: " + _describe_rows(g, bad))
    return g


def _season_frames(
    games: pd.DataFrame, season: int, as_of: str | date | datetime | pd.Timestamp | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(regular-season rows sorted by week/date, fixed playoff rows) for ``season``.

    Rows dated ``as_of`` or later are masked as unplayed first; then the played state is
    validated.  Played playoff rows are returned only when the regular season is complete.
    """
    if season < FIRST_SUPPORTED_SEASON:
        raise ValueError(f"season {season} predates the 2002 realignment; DIVISIONS does not apply")
    g = games[games["season"] == season]
    if g.empty:
        raise ValueError(f"no games for season {season}")
    g = g.copy()
    if "home_team_c" not in g.columns:
        g["home_team_c"] = g["home_team"].map(canonical)
        g["away_team_c"] = g["away_team"].map(canonical)
    cutoff = _as_of_timestamp(as_of)
    if cutoff is not None:
        if "game_date" not in g.columns:
            raise ValueError("as_of needs a 'game_date' column in the games frame")
        dates = pd.to_datetime(g["game_date"], errors="coerce")
        future = (dates.isna() | (dates >= cutoff)).to_numpy()
        g = _mask_unplayed(g, future)
    g = _validate_played_state(g)
    sort_cols = [c for c in ("week", "game_date", "game_id") if c in g.columns]
    reg = g[g["game_type"] == "REG"].sort_values(sort_cols).reset_index(drop=True)
    if reg.empty:
        raise ValueError(f"season {season} has no regular-season games")
    post = g[g["game_type"].isin(PLAYOFF_ROUNDS) & g["played"]].reset_index(drop=True)
    if not post.empty and not reg["played"].all():
        warnings.warn(
            f"season {season}: {len(post)} played playoff rows ignored because "
            f"{int((~reg['played']).sum())} regular-season games are unplayed (a real mid-season "
            "state cannot contain playoff results; use SimConfig.as_of to mask by date)",
            UserWarning,
            stacklevel=3,
        )
        post = post.iloc[0:0]
    return reg, post


def _ratings_array(ratings: Mapping[str, float], teams: Sequence[str]) -> np.ndarray:
    canon = {canonical(k): float(v) for k, v in ratings.items()}
    missing = [t for t in teams if t not in canon]
    if missing:
        raise ValueError(f"ratings missing for teams: {missing}")
    arr = np.array([canon[t] for t in teams], dtype=float)
    finite = np.isfinite(arr)
    if not finite.all():
        bad = {t: canon[t] for t, ok in zip(teams, finite) if not ok}
        raise ValueError(f"non-finite ratings for {bad}")
    return arr


def _conditional_margins(home_win: np.ndarray, diff: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Margins for simulated games: N(diff/25, sigma) conditioned on the drawn winner, integer, |m| >= 1.

    ``diff`` is per game ``(n_games,)`` or per simulation and game ``(n_sims, n_games)``.
    """
    if home_win.size == 0:
        return np.zeros_like(home_win, dtype=float)
    mu = np.asarray(diff, dtype=float) / ELO_POINTS_PER_SPREAD_POINT
    if mu.ndim == 1:
        mu = mu[None, :]
    sigma = NFL_MARGIN_SIGMA
    f0 = ndtr(-mu / sigma)  # P(margin < 0) under the unconditional normal model
    u = rng.random(home_win.shape)
    hw = home_win > 0.5
    q = np.where(hw, f0 + u * (1.0 - f0), u * f0)
    q = np.clip(q, 1e-12, 1.0 - 1e-12)
    margin = np.rint(mu + sigma * ndtri(q))
    return np.where(hw, np.maximum(margin, 1.0), np.minimum(margin, -1.0))


def _mov_multiplier(margin: np.ndarray, diff_home: np.ndarray, mov: bool) -> np.ndarray:
    """FiveThirtyEight margin-of-victory multiplier, vectorized twin of ``EloModel._mov_multiplier``."""
    if not mov:
        return np.ones_like(np.asarray(margin, dtype=float))
    margin = np.asarray(margin, dtype=float)
    diff_winner = np.where(margin > 0, diff_home, -diff_home)
    denom = np.maximum(0.001 * diff_winner + 2.2, 1e-6)
    mult = np.log(np.abs(margin) + 1.0) * 2.2 / denom
    return np.where(margin == 0, 1.0, mult)


def _rating_update(
    R: np.ndarray, home: np.ndarray, away: np.ndarray, diff: np.ndarray, p_home: np.ndarray,
    home_win: np.ndarray, margin: np.ndarray, cfg: SimConfig,
) -> None:
    """In-place hot update of the per-simulation ratings ``R`` (n_sims, n_teams) after a batch.

    Two layouts: a schedule batch, where ``home``/``away`` are ``(n,)`` team indices with no team
    repeated (so fancy-index ``+=`` is exact) and the game arrays are ``(n_sims, n)``; or a
    bracket game, where ``home``/``away`` are ``(n_sims,)`` per-simulation matchups and the game
    arrays are ``(n_sims,)``.
    """
    shift = cfg.k * _mov_multiplier(margin, diff, cfg.mov) * (home_win - p_home)
    if shift.ndim == 2:  # schedule batch
        R[:, home] += shift
        R[:, away] -= shift
    else:  # one game per simulation
        rows = np.arange(R.shape[0])
        R[rows, home] += shift
        R[rows, away] -= shift


def _no_repeat_batches(order: np.ndarray, home: np.ndarray, away: np.ndarray) -> list[np.ndarray]:
    """Split games (in ``order``) into consecutive batches with no team appearing twice."""
    batches: list[list[int]] = []
    cur: list[int] = []
    seen: set[int] = set()
    for j in order:
        h, a = int(home[j]), int(away[j])
        if h in seen or a in seen:
            batches.append(cur)
            cur, seen = [], set()
        cur.append(int(j))
        seen.add(h)
        seen.add(a)
    if cur:
        batches.append(cur)
    return [np.asarray(b, dtype=np.int64) for b in batches]


def _simulate_remaining_hot(
    R: np.ndarray, home_idx: np.ndarray, away_idx: np.ndarray, hfa: np.ndarray, unplayed: np.ndarray,
    cfg: SimConfig, rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Sequential (per no-repeat batch) simulation of ``unplayed`` games with per-sim ratings ``R``.

    Returns ``(W, M)`` of shape ``(n_sims, len(unplayed))`` aligned with ``unplayed``; ``R`` is
    updated in place after every batch when ``cfg.hot``.
    """
    S = R.shape[0]
    W = np.empty((S, unplayed.size))
    M = np.empty((S, unplayed.size))
    pos = {int(j): i for i, j in enumerate(unplayed)}
    for batch in _no_repeat_batches(unplayed, home_idx, away_idx):
        h, a = home_idx[batch], away_idx[batch]
        cols = np.array([pos[int(j)] for j in batch])
        diff = R[:, h] - R[:, a] + hfa[batch][None, :]
        p = _elo_prob_array(diff)
        w = (rng.random((S, batch.size)) < p).astype(float)
        m = _conditional_margins(w, diff, rng)
        W[:, cols] = w
        M[:, cols] = m
        if cfg.hot:
            _rating_update(R, h, a, diff, p, w, m, cfg)
    return W, M


def _fixed_playoff_results(post: pd.DataFrame, teams: Sequence[str]) -> dict[str, list[tuple[int, int, int]]]:
    """Played playoff games as {round: [(team_a, team_b, winner), ...]} in team-index space."""
    fixed: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    if post.empty:
        return fixed
    home = _team_index(teams, post["home_team_c"])
    away = _team_index(teams, post["away_team_c"])
    hw = post["home_win"].to_numpy(dtype=float)
    for rnd, h, a, w in zip(post["game_type"], home, away, hw):
        if w in (0.0, 1.0):
            fixed[str(rnd)].append((int(h), int(a), int(h) if w == 1.0 else int(a)))
    return fixed


# --------------------------------------------------------------------------- standings
def standings_from_results(
    games_season: pd.DataFrame,
    wins_matrix: np.ndarray,
    margins: np.ndarray | None = None,
    rng: np.random.Generator | None = None,
    season: int | None = None,
) -> Standings:
    """Standings, division winners and playoff seeds for every simulated set of results.

    games_season: the season's regular-season rows (one per game, ``home_team_c``/``away_team_c``).
    wins_matrix: ``(n_sims, n_games)`` home-win indicators aligned with ``games_season`` rows
        (1 home win, 0 away win, 0.5 tie).
    margins: optional ``(n_sims, n_games)`` home-minus-away margins for the point-differential
        tiebreaker; without it that step is skipped.
    rng: Generator for the final coin flip (default: fresh ``default_rng(0)``).
    season: playoff format selector (defaults to the ``season`` column).

    Division ties: head-to-head, division record, common games, conference record, strength of
    victory, point differential, coin flip.  Wild-card / seeding ties: eliminate all but one club
    per division with the division ladder, then head-to-head (sweep for 3+ clubs), conference
    record, common games (min 4), strength of victory, point differential, coin flip.
    """
    W = np.asarray(wins_matrix, dtype=float)
    if W.ndim != 2 or W.shape[1] != len(games_season):
        raise ValueError(f"wins_matrix must be (n_sims, {len(games_season)}), got {W.shape}")
    if margins is not None and np.shape(margins) != W.shape:
        raise ValueError("margins must have the same shape as wins_matrix")
    if not np.isfinite(W).all():
        raise ValueError("wins_matrix contains non-finite values (unplayed games must be simulated first)")
    if season is None:
        season = int(games_season["season"].iloc[0])
    n_playoff, n_byes = playoff_format(season)
    rng = np.random.default_rng(0) if rng is None else rng

    teams = sorted(DIVISIONS)
    n_teams = len(teams)
    team_conf = np.array([CONFERENCES.index(DIVISIONS[t][0]) for t in teams])
    team_div = np.array([DIVISION_ORDER.index(DIVISIONS[t]) for t in teams])
    home_idx = _team_index(teams, games_season["home_team_c"])
    away_idx = _team_index(teams, games_season["away_team_c"])
    eng = _TiebreakEngine(W, home_idx, away_idx, n_teams, team_conf, team_div, margins, rng)
    S = eng.n_sims

    div_winners = np.empty((S, len(DIVISION_ORDER)), dtype=np.int64)
    for d in range(len(DIVISION_ORDER)):
        cand = np.zeros((S, n_teams), dtype=bool)
        cand[:, team_div == d] = True
        div_winners[:, d] = eng.pick(cand, "division")

    seeds = np.empty((S, len(CONFERENCES), n_playoff), dtype=np.int64)
    rows = np.arange(S)
    for c in range(len(CONFERENCES)):
        conf_divs = np.flatnonzero(np.array([CONFERENCES.index(cd[0]) == c for cd in DIVISION_ORDER]))
        winners = div_winners[:, conf_divs]
        remaining = np.zeros((S, n_teams), dtype=bool)
        remaining[rows[:, None], winners] = True
        winner_mask = remaining.copy()
        for s in range(len(conf_divs)):
            pick = eng.pick(remaining, "wildcard") if s < len(conf_divs) - 1 else remaining.argmax(1)
            seeds[:, c, s] = pick
            remaining[rows, pick] = False
        remaining = (team_conf == c)[None, :] & ~winner_mask
        for s in range(len(conf_divs), n_playoff):
            pick = eng.pick(remaining, "wildcard")
            seeds[:, c, s] = pick
            remaining[rows, pick] = False

    return Standings(
        teams=teams,
        wins=eng.wins,
        games=eng.games_count,
        seeds=seeds,
        division_winners=div_winners,
        divisions=[f"{conf} {div}" for conf, div in DIVISION_ORDER],
        n_playoff=n_playoff,
        n_byes=n_byes,
    )


# --------------------------------------------------------------------------- bracket
def _simulate_bracket(
    st: Standings,
    ratings: np.ndarray,
    cfg: SimConfig,
    uniforms: np.ndarray,
    fixed: Mapping[str, list[tuple[int, int, int]]],
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """(conference champions (n_sims, 2), Super Bowl winner (n_sims,)) from the seeds.

    ``ratings`` is ``(n_teams,)`` (shared) or ``(n_sims, n_teams)`` (per simulation, updated in
    place after every bracket game when ``cfg.hot``; ``rng`` then draws the margins).
    """
    S = st.seeds.shape[0]
    rows = np.arange(S)
    per_sim = ratings.ndim == 2
    hot = cfg.hot and per_sim
    if hot and rng is None:
        raise ValueError("hot bracket simulation needs an rng for the margins")
    col = 0

    def rating(idx: np.ndarray) -> np.ndarray:
        return ratings[rows, idx] if per_sim else ratings[idx]

    def play(
        home: np.ndarray, away: np.ndarray, hs: np.ndarray, as_: np.ndarray, rnd: str, hfa: float
    ) -> tuple[np.ndarray, np.ndarray]:
        nonlocal col
        diff = cfg.playoff_mult * (rating(home) - rating(away) + hfa)
        p_home = _elo_prob_array(diff)
        home_wins = uniforms[:, col] < p_home
        col += 1
        for x, y, w in fixed.get(rnd, []):
            m = ((home == x) & (away == y)) | ((home == y) & (away == x))
            if m.any():
                home_wins[m] = home[m] == w
        winner = np.where(home_wins, home, away)
        winner_seed = np.where(home_wins, hs, as_)
        if hot:
            hw = home_wins.astype(float)
            margin = _conditional_margins(hw[:, None], diff[:, None], rng)[:, 0]
            _rating_update(ratings, home, away, diff, p_home, hw, margin, cfg)
        return winner, winner_seed

    champs = np.empty((S, len(CONFERENCES)), dtype=np.int64)
    n_wc = (st.n_playoff - st.n_byes) // 2
    for c in range(len(CONFERENCES)):
        seeds = st.seeds[:, c, :]
        wc_teams, wc_seeds = [], []
        for g in range(n_wc):
            hi, lo = st.n_byes + g, st.n_playoff - 1 - g
            w, ws = play(seeds[:, hi], seeds[:, lo], np.full(S, hi), np.full(S, lo), "WC", cfg.hfa_elo)
            wc_teams.append(w)
            wc_seeds.append(ws)
        wt = np.stack(wc_teams, axis=1)
        ws = np.stack(wc_seeds, axis=1)
        order = np.argsort(ws, axis=1)  # ascending seed number = best first
        wt = np.take_along_axis(wt, order, axis=1)
        ws = np.take_along_axis(ws, order, axis=1)
        # divisional round: the top seed meets the worst surviving seed (reseeding)
        d1, d1s = play(seeds[:, 0], wt[:, -1], np.zeros(S, dtype=int), ws[:, -1], "DIV", cfg.hfa_elo)
        if st.n_byes == 1:
            d2, d2s = play(wt[:, 0], wt[:, 1], ws[:, 0], ws[:, 1], "DIV", cfg.hfa_elo)
        else:
            d2, d2s = play(seeds[:, 1], wt[:, 0], np.ones(S, dtype=int), ws[:, 0], "DIV", cfg.hfa_elo)
        d1_home = d1s < d2s
        home = np.where(d1_home, d1, d2)
        away = np.where(d1_home, d2, d1)
        champ, _ = play(home, away, np.minimum(d1s, d2s), np.maximum(d1s, d2s), "CON", cfg.hfa_elo)
        champs[:, c] = champ
    sb_winner, _ = play(champs[:, 0], champs[:, 1], np.zeros(S, dtype=int), np.ones(S, dtype=int), "SB", 0.0)
    return champs, sb_winner


def bracket_game_count(season: int) -> int:
    """Number of playoff games simulated per season (13 for the 14-team era, 11 before)."""
    n_playoff, n_byes = playoff_format(season)
    return 2 * ((n_playoff - n_byes) // 2 + 2 + 1) + 1


# --------------------------------------------------------------------------- public API
def simulate_season(
    games: pd.DataFrame,
    season: int,
    ratings: Mapping[str, float],
    cfg: SimConfig | None = None,
) -> pd.DataFrame:
    """Monte-Carlo the rest of ``season`` and return per-team playoff / title probabilities.

    games: output of ``nfl_edge.data.load_games`` (any seasons; only ``season`` is used).
    season: season to simulate (2002 or later).
    ratings: current Elo rating per canonical team code (all 32 required, finite).
    cfg: ``SimConfig`` (default 20k sims, seed 0, 55 Elo home field, hot ratings).

    Played games are fixed - regular season and, once the regular season is complete, playoff
    games already played, so during the postseason the bracket follows the real results (drop
    non-REG rows from ``games`` to get a counterfactual bracket for a finished season).  Set
    ``cfg.as_of`` to time-travel: rows dated ``as_of`` or later are treated as unplayed.  The
    played state of the frame is validated (see the module docstring) and inconsistent frames
    raise ``ValueError``.

    Remaining regular-season games use ``elo_diff_to_prob(rating_home - rating_away + hfa)`` with
    ``hfa = 0`` for neutral sites; with ``cfg.hot`` each simulation's ratings move after every
    simulated game (538 hot simulation) so season totals carry realistic variance.  Playoff games
    give ``hfa`` to the better seed (Super Bowl neutral) and scale the difference by
    ``playoff_mult``.

    Returns a DataFrame indexed by team (sorted canonical codes) with columns ``mean_wins``,
    ``p_playoffs``, ``p_division``, ``p_top_seed``, ``p_conference``, ``p_super_bowl`` and
    ``win_dist`` (dict wins -> probability).  ``attrs`` carries ``season``, ``n_sims``,
    ``teams``, the ``wins`` matrix as a DataFrame (n_sims x teams, for ``win_total_probs``), the
    raw ``seeds`` array, ``hot``, ``as_of`` and ``n_unplayed``.
    """
    cfg = SimConfig() if cfg is None else cfg
    if cfg.n_sims < 1:
        raise ValueError("n_sims must be >= 1")
    if not np.isfinite(cfg.k) or cfg.k < 0:
        raise ValueError("k must be a finite non-negative number")
    if not np.isfinite(cfg.rating_noise_sd) or cfg.rating_noise_sd < 0:
        raise ValueError("rating_noise_sd must be a finite non-negative number")
    reg, post = _season_frames(games, season, as_of=cfg.as_of)
    teams = sorted(DIVISIONS)
    r = _ratings_array(ratings, teams)
    home_idx = _team_index(teams, reg["home_team_c"])
    away_idx = _team_index(teams, reg["away_team_c"])
    rng = np.random.default_rng(cfg.seed)
    S, G = cfg.n_sims, len(reg)

    played = reg["played"].to_numpy(dtype=bool)
    hw = reg["home_win"].to_numpy(dtype=float)
    margin = reg["margin"].to_numpy(dtype=float) if "margin" in reg.columns else np.zeros(G)
    neutral = (reg["location"].astype(str).str.lower() == "neutral").to_numpy() if "location" in reg.columns else np.zeros(G, bool)
    hfa = np.where(neutral, 0.0, cfg.hfa_elo)
    unplayed = np.flatnonzero(~played)
    played_idx = np.flatnonzero(played)

    W = np.empty((S, G))
    M = np.empty((S, G))
    W[:, played_idx] = hw[played_idx][None, :]
    M[:, played_idx] = np.nan_to_num(margin[played_idx])[None, :]
    per_sim = cfg.hot or cfg.rating_noise_sd > 0
    if per_sim:
        R = np.repeat(r[None, :], S, axis=0)
        if cfg.rating_noise_sd > 0:
            R += rng.normal(0.0, cfg.rating_noise_sd, R.shape)
        W[:, unplayed], M[:, unplayed] = _simulate_remaining_hot(R, home_idx, away_idx, hfa, unplayed, cfg, rng)
        bracket_ratings: np.ndarray = R
    else:
        diff = r[home_idx[unplayed]] - r[away_idx[unplayed]] + hfa[unplayed]
        p_home = np.array([elo_diff_to_prob(d) for d in diff], dtype=float)
        W[:, unplayed] = (rng.random((S, unplayed.size)) < p_home[None, :]).astype(float)
        M[:, unplayed] = _conditional_margins(W[:, unplayed], diff, rng)
        bracket_ratings = r
    bracket_u = rng.random((S, bracket_game_count(season)))

    st = standings_from_results(reg, W, margins=M, rng=rng, season=season)
    champs, sb = _simulate_bracket(st, bracket_ratings, cfg, bracket_u, _fixed_playoff_results(post, teams), rng)

    n_teams = len(teams)
    counts = lambda idx: np.bincount(np.asarray(idx).ravel(), minlength=n_teams) / S  # noqa: E731
    out = pd.DataFrame(
        {
            "mean_wins": st.wins.mean(0),
            "p_playoffs": counts(st.seeds),
            "p_division": counts(st.division_winners),
            "p_top_seed": counts(st.seeds[:, :, 0]),
            "p_conference": counts(champs),
            "p_super_bowl": counts(sb),
            "win_dist": [_win_distribution(st.wins[:, i]) for i in range(n_teams)],
        },
        index=pd.Index(teams, name="team"),
    )
    out.attrs.update(
        {
            "season": season,
            "n_sims": S,
            "teams": teams,
            "wins": pd.DataFrame(st.wins, columns=teams),
            "seeds": st.seeds,
            "n_playoff": st.n_playoff,
            "hot": bool(cfg.hot),
            "as_of": _as_of_timestamp(cfg.as_of),
            "n_unplayed": int(unplayed.size),
        }
    )
    return out


def _win_distribution(wins: np.ndarray) -> dict[int | float, float]:
    vals, cnt = np.unique(wins, return_counts=True)
    return {(int(v) if float(v).is_integer() else float(v)): float(c) / wins.size for v, c in zip(vals, cnt)}


def win_total_probs(result_wins_matrix: np.ndarray | pd.DataFrame, team: int | str) -> dict[int, float]:
    """P(regular-season wins >= n) for n = 0..max, for one team of a (n_sims x teams) wins matrix.

    ``team`` is a column label when a DataFrame is passed (e.g. ``simulate_season(...).attrs["wins"]``
    with a canonical code) and a column index for a numpy array.
    """
    if isinstance(result_wins_matrix, pd.DataFrame):
        col = result_wins_matrix[team].to_numpy(dtype=float)
    else:
        arr = np.asarray(result_wins_matrix, dtype=float)
        if arr.ndim != 2:
            raise ValueError("result_wins_matrix must be 2-D (n_sims, n_teams)")
        col = arr[:, int(team)]
    if col.size == 0:
        raise ValueError("empty wins matrix")
    top = int(np.ceil(col.max()))
    return {n: float(np.mean(col >= n)) for n in range(top + 1)}
