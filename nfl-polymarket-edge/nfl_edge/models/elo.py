"""Elo win-probability model for NFL games (FiveThirtyEight-style).

The model keeps one rating per franchise (canonical codes, so ratings survive
relocations) and processes games strictly in ``(season, week, game_date,
game_id)`` order. Every prediction for a game uses only ratings built from games
processed *before* it, and ratings are updated only after a game has been
played, which satisfies the look-ahead rule in ``CONTRACT.md``.

Rating mechanics
----------------
* Pre-game adjusted difference (home perspective)::

      elo_diff = (elo_home - elo_away + hfa + rest + qb) * playoff_mult

  where ``hfa`` is 0 for neutral-site games, ``rest`` is ``+/-rest_bonus`` when
  exactly one side is coming off ten or more days of rest, ``qb`` subtracts
  ``qb_change_penalty`` from a side whose listed starter changed, and
  ``playoff_mult`` applies only to non-regular-season games.
* ``elo_prob = 1 / (1 + 10 ** (-elo_diff / 400))`` and
  ``elo_spread = elo_diff / ELO_POINTS_PER_SPREAD_POINT``.
* After a played game the winner gains ``k * mult * (score - elo_prob)`` and the
  loser loses the same amount (zero-sum). With ``mov`` on, ``mult`` is the
  FiveThirtyEight margin-of-victory multiplier
  ``ln(|margin| + 1) * 2.2 / (0.001 * elo_diff_winner + 2.2)`` with
  ``elo_diff_winner`` the adjusted pre-game difference from the winner's
  perspective; ties use a multiplier of 1.0 and a score of 0.5.
* The first time a team appears in a new season its rating is regressed toward
  ``mean`` by ``season_regress``. Franchises that enter the league after the
  first season in the data (HOU in 2002) start at ``mean - 100``.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..config import ELO_MEAN, ELO_POINTS_PER_SPREAD_POINT
from ..teams import CURRENT_TEAMS, canonical

__all__ = [
    "EloParams",
    "EloModel",
    "fit_elo",
    "save_params",
    "load_params",
    "DEFAULT_GRID",
    "RUN_COLUMNS",
    "REQUIRED_COLUMNS",
]

#: Days of rest at or above which a side counts as "rested" (post-bye).
REST_THRESHOLD_DAYS = 10
#: Starting offset for a franchise that first appears after the first season in the data.
NEW_FRANCHISE_OFFSET = -100.0
#: Columns ``EloModel.run`` adds to its output.
RUN_COLUMNS: tuple[str, ...] = ("elo_home_pre", "elo_away_pre", "elo_diff", "elo_prob", "elo_spread")
#: Input columns ``EloModel.run`` needs (all produced by ``nfl_edge.data.load_games``).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "game_id", "season", "week", "game_date", "game_type", "location",
    "home_team_c", "away_team_c", "home_rest", "away_rest", "margin", "played",
    "qb_change_home", "qb_change_away",
)
#: Coordinate-descent grid used by ``fit_elo`` when no grid is supplied.
DEFAULT_GRID: dict[str, list[Any]] = {
    "k": [12, 16, 20, 24, 28, 32],
    "hfa": [35, 45, 55, 65, 75],
    "season_regress": [0.2, 0.33, 0.5],
    "rest_bonus": [0, 15, 25, 40],
    "qb_change_penalty": [0, 20, 40],
    "mov": [True, False],
}
_SORT_KEYS = ["season", "week", "game_date", "game_id"]
_METRICS = ("logloss", "brier", "accuracy")
_EPS = 1e-12


# ------------------------------------------------------------------ parameters
@dataclass
class EloParams:
    """Hyper-parameters of the Elo model. Defaults are close to FiveThirtyEight's NFL Elo."""

    k: float = 20.0                # base update
    hfa: float = 55.0              # home-field Elo points (0 when location == "Neutral")
    mov: bool = True               # 538-style margin multiplier
    season_regress: float = 0.33   # fraction of (rating - mean) removed at season start
    rest_bonus: float = 25.0       # Elo points for the rested side when rest >= 10 and the other < 10
    qb_change_penalty: float = 0.0 # Elo points subtracted when qb_change flag = 1
    playoff_mult: float = 1.0      # multiplies elo_diff in playoff games (538 used 1.2)
    mean: float = ELO_MEAN

    def __post_init__(self) -> None:
        self.k = float(self.k)
        self.hfa = float(self.hfa)
        self.mov = bool(self.mov)
        self.season_regress = float(self.season_regress)
        self.rest_bonus = float(self.rest_bonus)
        self.qb_change_penalty = float(self.qb_change_penalty)
        self.playoff_mult = float(self.playoff_mult)
        self.mean = float(self.mean)
        if self.k <= 0:
            raise ValueError(f"k must be positive, got {self.k}")
        if not 0.0 <= self.season_regress <= 1.0:
            raise ValueError(f"season_regress must be in [0, 1], got {self.season_regress}")
        if self.playoff_mult <= 0:
            raise ValueError(f"playoff_mult must be positive, got {self.playoff_mult}")

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serialisable mapping of the parameters."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EloParams":
        """Build from a mapping, rejecting unknown keys so typos do not silently use defaults."""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown EloParams fields: {sorted(unknown)}")
        return cls(**dict(data))


def _param_field_names() -> list[str]:
    return [f.name for f in fields(EloParams)]


# ----------------------------------------------------------------------- model
class EloModel:
    """Sequential Elo rating engine over an nflverse games table.

    Typical use::

        model = EloModel(EloParams(k=24))
        pred = model.run(load_games())          # adds elo_* columns, no look-ahead
        model.predict("KC", "BUF")              # P(KC beats BUF at home) from current ratings
        model.ratings()                         # {"ARI": 1493.2, ...}
    """

    def __init__(self, params: EloParams | None = None) -> None:
        self.params: EloParams = params if params is not None else EloParams()
        self._ratings: dict[str, float] = {}
        self._last_season: dict[str, int] = {}
        self._first_season: int | None = None
        self._history: list[tuple[str, int, int, str, float, float]] = []

    # ----------------------------------------------------------------- state
    def reset(self) -> None:
        """Forget all ratings and history; the next ``run`` starts from scratch."""
        self._ratings = {}
        self._last_season = {}
        self._first_season = None
        self._history = []

    def ratings(self) -> dict[str, float]:
        """Current post-run ratings keyed by canonical team code (a copy)."""
        return dict(self._ratings)

    def elo_history(self) -> pd.DataFrame:
        """One row per (game, team) processed by the last ``run``.

        Columns: ``game_id, season, week, team, elo_pre, elo_post``. ``elo_pre`` is the rating
        after any season regression and before the game; for unplayed games ``elo_post == elo_pre``.
        """
        return pd.DataFrame(
            self._history, columns=["game_id", "season", "week", "team", "elo_pre", "elo_post"]
        )

    # ----------------------------------------------------------- core maths
    def _adjusted_diff(
        self,
        elo_home: float,
        elo_away: float,
        neutral: bool,
        home_rest: float,
        away_rest: float,
        playoff: bool,
        qb_change_home: int,
        qb_change_away: int,
    ) -> float:
        """Adjusted pre-game Elo difference from the home perspective."""
        p = self.params
        diff = elo_home - elo_away
        if not neutral:
            diff += p.hfa
        home_rested = home_rest >= REST_THRESHOLD_DAYS
        away_rested = away_rest >= REST_THRESHOLD_DAYS
        if home_rested and not away_rested:
            diff += p.rest_bonus
        elif away_rested and not home_rested:
            diff -= p.rest_bonus
        if qb_change_home:
            diff -= p.qb_change_penalty
        if qb_change_away:
            diff += p.qb_change_penalty
        if playoff:
            diff *= p.playoff_mult
        return diff

    @staticmethod
    def _prob(diff: float) -> float:
        return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    def _mov_multiplier(self, margin: float, diff_home: float) -> float:
        """FiveThirtyEight margin-of-victory multiplier; 1.0 for ties or when ``mov`` is off."""
        if not self.params.mov or margin == 0:
            return 1.0
        diff_winner = diff_home if margin > 0 else -diff_home
        # The denominator is positive for any realistic Elo gap (it would need a winner rated
        # 2200 points below its opponent to reach zero); the floor only guards the division.
        denom = max(0.001 * diff_winner + 2.2, 1e-6)
        return math.log(abs(margin) + 1.0) * 2.2 / denom

    def _season_start_rating(self, team: str, season: int) -> float:
        """Rating of ``team`` entering ``season``: regressed, or a fresh start for a new franchise."""
        p = self.params
        last = self._last_season.get(team)
        if last is None:
            rating = p.mean if season == self._first_season else p.mean + NEW_FRANCHISE_OFFSET
        else:
            rating = self._ratings[team]
            if last != season:
                rating = p.mean + (rating - p.mean) * (1.0 - p.season_regress)
        self._ratings[team] = rating
        self._last_season[team] = season
        return rating

    # ------------------------------------------------------------------ run
    def run(self, games: pd.DataFrame) -> pd.DataFrame:
        """Replay ``games`` sequentially and return a copy with Elo predictions.

        ``games`` is the output of ``nfl_edge.data.load_games`` (played and unplayed rows, any
        order). The frame is processed in ``(season, week, game_date, game_id)`` order; the result
        keeps the caller's row order and index. Added columns: ``elo_home_pre``, ``elo_away_pre``
        (post-regression pre-game ratings), ``elo_diff`` (adjusted, home - away), ``elo_prob``
        (P(home win)), ``elo_spread`` (expected home margin) and ``elo_home_post``/``elo_away_post``.
        Unplayed games are predicted from the latest ratings and do not update them.
        """
        missing = [c for c in REQUIRED_COLUMNS if c not in games.columns]
        if missing:
            raise ValueError(f"games is missing required columns: {missing}")
        self.reset()
        n = len(games)
        base = games.reset_index(drop=True)
        order = base.sort_values(_SORT_KEYS, kind="mergesort").index.to_numpy()
        g = base.iloc[order]

        seasons = g["season"].to_numpy(dtype=int).tolist()
        weeks = g["week"].to_numpy(dtype=int).tolist()
        game_ids = g["game_id"].astype(str).tolist()
        homes = g["home_team_c"].astype(str).tolist()
        aways = g["away_team_c"].astype(str).tolist()
        neutrals = (g["location"].astype(str) == "Neutral").tolist()
        playoffs = (g["game_type"].astype(str) != "REG").tolist()
        home_rest = g["home_rest"].fillna(7).to_numpy(dtype=float).tolist()
        away_rest = g["away_rest"].fillna(7).to_numpy(dtype=float).tolist()
        qb_home = g["qb_change_home"].fillna(0).to_numpy(dtype=int).tolist()
        qb_away = g["qb_change_away"].fillna(0).to_numpy(dtype=int).tolist()
        played = g["played"].to_numpy(dtype=bool).tolist()
        margins = g["margin"].to_numpy(dtype=float).tolist()
        if n:
            self._first_season = min(seasons)

        out_home_pre = np.empty(n)
        out_away_pre = np.empty(n)
        out_diff = np.empty(n)
        out_prob = np.empty(n)
        out_home_post = np.empty(n)
        out_away_post = np.empty(n)
        history = self._history
        ratings = self._ratings
        k = self.params.k

        for j in range(n):
            season = seasons[j]
            home = homes[j]
            away = aways[j]
            elo_home = self._season_start_rating(home, season)
            elo_away = self._season_start_rating(away, season)
            diff = self._adjusted_diff(
                elo_home, elo_away, neutrals[j], home_rest[j], away_rest[j], playoffs[j],
                qb_home[j], qb_away[j],
            )
            prob = self._prob(diff)
            if played[j]:
                margin = margins[j]
                if math.isnan(margin):
                    raise ValueError(f"game {game_ids[j]} is marked played but has no margin")
                score = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
                delta = k * self._mov_multiplier(margin, diff) * (score - prob)
                new_home = elo_home + delta
                new_away = elo_away - delta
                ratings[home] = new_home
                ratings[away] = new_away
            else:
                new_home, new_away = elo_home, elo_away
            pos = order[j]
            out_home_pre[pos] = elo_home
            out_away_pre[pos] = elo_away
            out_diff[pos] = diff
            out_prob[pos] = prob
            out_home_post[pos] = new_home
            out_away_post[pos] = new_away
            history.append((game_ids[j], season, weeks[j], home, elo_home, new_home))
            history.append((game_ids[j], season, weeks[j], away, elo_away, new_away))

        out = games.copy()
        out["elo_home_pre"] = out_home_pre
        out["elo_away_pre"] = out_away_pre
        out["elo_diff"] = out_diff
        out["elo_prob"] = out_prob
        out["elo_spread"] = out_diff / ELO_POINTS_PER_SPREAD_POINT
        out["elo_home_post"] = out_home_post
        out["elo_away_post"] = out_away_post
        return out

    # -------------------------------------------------------------- predict
    def _rating_for(self, code: str) -> float:
        team = canonical(code)
        if team in self._ratings:
            return self._ratings[team]
        if team in CURRENT_TEAMS:
            return self.params.mean  # model not run yet: every team sits at the mean
        raise KeyError(f"unknown team code {code!r}")

    def predict(
        self,
        home: str,
        away: str,
        neutral: bool = False,
        home_rest: int = 7,
        away_rest: int = 7,
        playoff: bool = False,
        qb_change_home: int = 0,
        qb_change_away: int = 0,
    ) -> float:
        """P(home wins) from the current ratings with the same adjustments as ``run``.

        Team codes may be any nflverse code (historical codes are canonicalised). A current
        franchise the model has not seen is rated at ``mean``; an unknown code raises ``KeyError``.
        """
        diff = self._adjusted_diff(
            self._rating_for(home), self._rating_for(away), neutral, float(home_rest),
            float(away_rest), playoff, int(qb_change_home), int(qb_change_away),
        )
        return self._prob(diff)


# --------------------------------------------------------------------- scoring
def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    """Mean binary cross-entropy; ``y`` may contain 0.5 for ties."""
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1.0 - _EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def _accuracy(y: np.ndarray, p: np.ndarray) -> float:
    """Share of games picked correctly; ties count half."""
    y = np.asarray(y, dtype=float)
    pick_home = np.asarray(p, dtype=float) >= 0.5
    correct = np.where(y == 0.5, 0.5, (pick_home == (y == 1.0)).astype(float))
    return float(np.mean(correct))


def _score_table(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {"logloss": _log_loss(y, p), "brier": _brier(y, p), "accuracy": _accuracy(y, p), "n": int(len(y))}


# ------------------------------------------------------------------------ fit
def fit_elo(
    games: pd.DataFrame,
    train_seasons: range | Sequence[int] | Iterable[int] = range(2003, 2025),
    grid: dict[str, Sequence[Any]] | None = None,
    metric: str = "logloss",
    passes: int = 2,
    start: EloParams | None = None,
) -> tuple[EloParams, pd.DataFrame]:
    """Tune ``EloParams`` by coordinate descent on walk-forward log-loss.

    Starting from ``start`` (default ``EloParams()``), each pass sweeps every key of ``grid`` in
    turn, evaluates all its candidate values with the other parameters fixed and keeps the best.
    Each evaluation replays the games up to ``max(train_seasons)`` sequentially and scores the
    played games whose season is in ``train_seasons``; earlier seasons only warm up the ratings.
    Every prediction is out-of-sample with respect to the game outcome (sequential replay), but
    the chosen hyper-parameters are of course fitted to ``train_seasons``.

    ``grid`` maps ``EloParams`` field names to candidate values and defines the whole search space
    (``DEFAULT_GRID`` when omitted); fields absent from it stay at their ``start`` value. A key that
    is not a field of ``EloParams`` raises. ``metric`` is ``"logloss"``, ``"brier"`` (minimised) or
    ``"accuracy"`` (maximised).

    Returns ``(best_params, results)`` where ``results`` has one row per distinct parameter
    combination evaluated, with the parameter values, ``logloss``, ``brier``, ``accuracy``, ``n``
    and the coordinate-descent ``pass``/``param`` at which it was first tried, sorted best first.
    """
    if metric not in _METRICS:
        raise ValueError(f"metric must be one of {_METRICS}, got {metric!r}")
    if passes < 1:
        raise ValueError("passes must be >= 1")
    seasons = sorted({int(s) for s in train_seasons})
    if not seasons:
        raise ValueError("train_seasons is empty")
    source = DEFAULT_GRID if grid is None else grid
    unknown = set(source) - set(_param_field_names())
    if unknown:
        raise ValueError(f"grid has unknown EloParams fields: {sorted(unknown)}")
    search: dict[str, list[Any]] = {}
    for key, values in source.items():
        vals = list(values)
        if not vals:
            raise ValueError(f"grid[{key!r}] is empty")
        search[key] = vals
    if not search:
        raise ValueError("grid is empty")

    sub = games[games["season"] <= seasons[-1]]
    mask = (sub["played"] & sub["season"].isin(seasons)).to_numpy()
    if not mask.any():
        raise ValueError("no played games in train_seasons")
    margins = sub.loc[mask, "margin"].to_numpy(dtype=float)
    y = np.where(margins > 0, 1.0, np.where(margins < 0, 0.0, 0.5))
    sign = -1.0 if metric == "accuracy" else 1.0

    cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    def evaluate(params: EloParams, pass_no: int, param: str) -> dict[str, Any]:
        key = tuple(params.to_dict().values())
        if key not in cache:
            pred = EloModel(params).run(sub)
            row: dict[str, Any] = {**params.to_dict(), **_score_table(y, pred["elo_prob"].to_numpy()[mask])}
            row["pass"] = pass_no
            row["param"] = param
            cache[key] = row
            rows.append(row)
        return cache[key]

    current = start if start is not None else EloParams()
    current_score = sign * evaluate(current, 0, "start")[metric]
    for pass_no in range(1, passes + 1):
        improved = False
        for name, values in search.items():
            best_value = getattr(current, name)
            for value in values:
                candidate = replace(current, **{name: value})
                score = sign * evaluate(candidate, pass_no, name)[metric]
                if score < current_score - 1e-15:
                    current_score = score
                    best_value = value
                    improved = True
            current = replace(current, **{name: best_value})
        if not improved:
            break

    results = pd.DataFrame(rows)
    results = results.sort_values(metric, ascending=(sign > 0), kind="mergesort").reset_index(drop=True)
    results.insert(0, "rank", np.arange(1, len(results) + 1))
    return current, results


# ------------------------------------------------------------------- persist
def save_params(params: EloParams, path: str | Path) -> Path:
    """Write ``params`` as JSON to ``path`` (parent directories are created)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(params.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def load_params(path: str | Path) -> EloParams:
    """Read ``EloParams`` from a JSON file written by ``save_params``."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object of EloParams fields")
    return EloParams.from_dict(data)
