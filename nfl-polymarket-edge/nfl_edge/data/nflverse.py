"""nflverse data access with atomic on-disk caching.

All loaders return pandas DataFrames. ``load_games`` is the spine of the system:
one row per game 1999-present with closing spread/total (all seasons) and
moneylines (2007+). Derived columns are documented in ``GAME_DERIVED_COLUMNS``.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

from ..config import CACHE_DIR, FIRST_SEASON, NFLVERSE_BASE, ensure_dirs
from ..odds import american_to_prob_array, devig_two_way_array, spread_to_prob_array
from ..teams import canonical

GAME_DERIVED_COLUMNS = {
    "game_date": "datetime64 of gameday",
    "played": "bool, both scores present",
    "margin": "home_score - away_score (nflverse 'result')",
    "home_win": "1.0 home win, 0.0 away win, 0.5 tie, NaN unplayed",
    "home_team_c / away_team_c": "franchise-canonical codes (STL->LA, SD->LAC, OAK->LV)",
    "home_ml_prob_raw / away_ml_prob_raw": "vig-inclusive implied probabilities from moneylines",
    "home_ml_prob / away_ml_prob": "multiplicatively devigged moneyline probabilities",
    "home_spread_prob": "normal-model P(home win) from closing spread_line",
    "market_prob": "home_ml_prob when available else home_spread_prob (the market baseline)",
    "qb_change_home / qb_change_away": "1 if the listed starting QB differs from the team's previous game",
}


def _download(url: str, dest: Path, refresh: bool = False, timeout: int = 90) -> Path:
    """Download ``url`` to ``dest`` atomically (tmp file + rename) unless cached."""
    ensure_dirs()
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def _release_url(asset: str, filename: str) -> str:
    return f"{NFLVERSE_BASE}/{asset}/{filename}"


def load_games(refresh: bool = False, path: Path | None = None) -> pd.DataFrame:
    """One row per NFL game, 1999-present, with derived model/market columns."""
    dest = path or (CACHE_DIR / "games.csv")
    if path is None:
        _download(_release_url("schedules", "games.csv"), dest, refresh=refresh)
    df = pd.read_csv(dest, low_memory=False)
    return add_derived_columns(df)


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["gameday"], errors="coerce")
    df["played"] = df["home_score"].notna() & df["away_score"].notna()
    df["margin"] = df["home_score"] - df["away_score"]
    df["home_win"] = np.where(
        df["played"], np.where(df["margin"] > 0, 1.0, np.where(df["margin"] < 0, 0.0, 0.5)), np.nan
    )
    df["home_team_c"] = df["home_team"].map(canonical)
    df["away_team_c"] = df["away_team"].map(canonical)

    df["home_ml_prob_raw"] = american_to_prob_array(df["home_moneyline"])
    df["away_ml_prob_raw"] = american_to_prob_array(df["away_moneyline"])
    h, a = devig_two_way_array(df["home_ml_prob_raw"], df["away_ml_prob_raw"])
    df["home_ml_prob"] = h
    df["away_ml_prob"] = a
    df["home_spread_prob"] = spread_to_prob_array(df["spread_line"])
    df["market_prob"] = df["home_ml_prob"].where(df["home_ml_prob"].notna(), df["home_spread_prob"])

    df = df.sort_values(["season", "week", "game_date", "game_id"]).reset_index(drop=True)
    df["qb_change_home"] = _qb_change(df, "home")
    df["qb_change_away"] = _qb_change(df, "away")
    return df


def _qb_change(df: pd.DataFrame, side: str) -> np.ndarray:
    """1 if a team's listed starting QB differs from its previous game's starter (any side)."""
    long = pd.concat(
        [
            df[["game_id", "season", "week", "game_date", "home_team_c", "home_qb_name"]]
            .rename(columns={"home_team_c": "team", "home_qb_name": "qb"}),
            df[["game_id", "season", "week", "game_date", "away_team_c", "away_qb_name"]]
            .rename(columns={"away_team_c": "team", "away_qb_name": "qb"}),
        ]
    ).sort_values(["team", "season", "week", "game_date"])
    long["prev_qb"] = long.groupby("team")["qb"].shift(1)
    long["changed"] = (
        long["qb"].notna() & long["prev_qb"].notna() & (long["qb"] != long["prev_qb"])
    ).astype(int)
    key = long.set_index(["game_id", "team"])["changed"]
    team_col = f"{side}_team_c"
    idx = pd.MultiIndex.from_arrays([df["game_id"], df[team_col]])
    return key.reindex(idx).fillna(0).astype(int).to_numpy()


def load_team_week_stats(
    seasons: Iterable[int] | None = None, refresh: bool = False
) -> pd.DataFrame:
    """Weekly team box-score and EPA stats (nflverse ``stats_team_week_<season>.csv``)."""
    if seasons is None:
        seasons = range(FIRST_SEASON, max(current_season_from_games(), FIRST_SEASON) + 1)
    frames = []
    for s in seasons:
        fn = f"stats_team_week_{s}.csv"
        dest = CACHE_DIR / fn
        try:
            _download(_release_url("stats_team", fn), dest, refresh=refresh)
        except requests.HTTPError:
            continue
        frames.append(pd.read_csv(dest, low_memory=False))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).copy()
    df = df.assign(team_c=df["team"].map(canonical), opponent_c=df["opponent_team"].map(canonical))
    return df


def load_injuries(seasons: Iterable[int], refresh: bool = False) -> pd.DataFrame:
    frames = []
    for s in seasons:
        fn = f"injuries_{s}.csv"
        dest = CACHE_DIR / fn
        try:
            _download(_release_url("injuries", fn), dest, refresh=refresh)
        except requests.HTTPError:
            continue
        frames.append(pd.read_csv(dest, low_memory=False))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_depth_charts(season: int, refresh: bool = False) -> pd.DataFrame:
    fn = f"depth_charts_{season}.csv"
    dest = CACHE_DIR / fn
    _download(_release_url("depth_charts", fn), dest, refresh=refresh)
    return pd.read_csv(dest, low_memory=False)


# ------------------------------------------------------------------ helpers
def current_season_from_games(path: Path | None = None) -> int:
    dest = path or (CACHE_DIR / "games.csv")
    if not dest.exists():
        return FIRST_SEASON
    return int(pd.read_csv(dest, usecols=["season"])["season"].max())


def current_season(games: pd.DataFrame) -> int:
    return int(games["season"].max())


def current_week(games: pd.DataFrame, season: int | None = None) -> int:
    """First regular/post-season week of ``season`` that still has unplayed games."""
    season = season or current_season(games)
    g = games[games["season"] == season]
    pending = g[~g["played"]]
    if pending.empty:
        return int(g["week"].max())
    return int(pending["week"].min())


def upcoming_games(games: pd.DataFrame, season: int | None = None, week: int | None = None) -> pd.DataFrame:
    season = season or current_season(games)
    g = games[(games["season"] == season) & (~games["played"])]
    if week is not None:
        g = g[g["week"] == week]
    return g.copy()


def played_games(games: pd.DataFrame, before_season: int | None = None) -> pd.DataFrame:
    g = games[games["played"]]
    if before_season is not None:
        g = g[g["season"] < before_season]
    return g.copy()
