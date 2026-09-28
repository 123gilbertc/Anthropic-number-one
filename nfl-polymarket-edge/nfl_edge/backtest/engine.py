"""Betting backtests against closing moneylines and recorded Polymarket quote streams.

Two entry points share one settlement engine:

* :func:`backtest_vs_closing` -- a model's ``P(home win)`` is compared with the *vigged* closing
  moneyline of every played game.  Whichever side (home or away) carries the larger edge is bet
  when that edge clears ``BacktestConfig.min_edge``.
* :func:`backtest_snapshots` -- the same on a recorded stream of Polymarket quotes: enter at the
  ask of the first pre-kickoff snapshot whose edge clears ``min_edge``, settle on the game result
  and measure closing-line value (CLV) against the last quote before kickoff.

``BacktestConfig.edge_metric`` fixes the unit of ``min_edge`` for *both* entry points: ``"ev"``
(expected value per $1 staked, ``p * decimal_odds - 1``, the CONTRACT default) or ``"prob"``
(``fair_prob - implied price``, the unit of ``StrategyConfig.min_edge``).  The devigged market
baseline behind ``model_clv`` is always rebuilt from the raw moneylines with ``cfg.devig``.

Both entry points track a flat-stake path (every bet risks ``flat_stake`` of the starting
bankroll, which is 1.0) and a fractional-Kelly path.  The Kelly path is a strictly chronological
simulation: a bet is sized off the bankroll *as of its entry* -- the starting bankroll plus the
profit of every bet already resolved at that moment -- so no stake ever depends on an outcome that
was unknown when the bet was placed.  Bets entered at the same instant form a batch (the closing
path places all of a week's bets at once; a snapshot sweep quotes many markets at one timestamp)
whose Kelly fractions are scaled down proportionally when they would push the open exposure past
``max_weekly_exposure``.  Closing-line bets resolve at the end of their game week; snapshot bets
resolve :data:`SETTLEMENT_LAG` after kickoff.  Ties / pushes refund the stake.

No prediction is made here, so the look-ahead rule of CONTRACT.md reduces to: a bet's size may
depend only on bets resolved before it was entered, and settlement only on its own result.
``tests/test_backtest.py`` checks that flipping a later result leaves every earlier row untouched.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from ..config import season_for_date
from ..odds import american_to_prob_array, devig

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BET_COLUMNS",
    "SETTLEMENT_LAG",
    "WEEKS_PER_YEAR",
    "backtest_vs_closing",
    "backtest_snapshots",
    "max_drawdown",
    "bootstrap_roi",
    "nfl_season_week",
    "report_markdown",
]

#: Betting weeks in an NFL year (18 regular-season weeks + ~3 playoff weeks bet on average).
WEEKS_PER_YEAR: float = 21.0

#: A snapshot bet's outcome is treated as known (and its cash returned to the bankroll) this long
#: after kickoff.  NFL games last ~3h15m; an entry placed before this moment on another game never
#: sees the result.
SETTLEMENT_LAG: pd.Timedelta = pd.Timedelta(hours=4)

#: Columns every ``BacktestResult.bets`` frame carries, in this order.  Extra, source-specific
#: columns (``won``, ``bankroll_start``, ``market_id``, ``entry_ts``, ``price``, ``clv``, ...) follow.
BET_COLUMNS: tuple[str, ...] = (
    "game_id", "season", "week", "side", "prob", "dec_odds", "ev",
    "stake_flat", "stake_kelly", "profit_flat", "profit_kelly", "market_prob_devig", "model_clv",
)

_EDGE_TIE_TOL = 1e-12        # edge differences below this are ties -> prefer the home side
_SUPPORTED_BET_TYPES = ("moneyline",)
_DEVIG_METHODS = ("multiplicative", "additive", "power", "shin")
_EDGE_METRICS = ("ev", "prob")
_ET = "America/New_York"


# ============================================================================ configuration
@dataclass
class BacktestConfig:
    """Betting rules for a backtest.

    Attributes
    ----------
    start_season, end_season
        Inclusive season window (``end_season=None`` means "through the latest season").
    min_edge
        Entry threshold, strict (``edge > min_edge``), in the unit set by ``edge_metric``.
        ``-1`` bets every game (useful to measure the vig).
    edge_metric
        ``"ev"``: edge is expected value per $1 staked, ``p * decimal_odds - 1`` (``fair / price - 1``
        for a Polymarket share).  ``"prob"``: edge is ``fair_prob - implied price`` (``p - 1 / dec``
        for a moneyline, ``fair_prob - ask`` for a share), the unit of ``StrategyConfig.min_edge``
        in :mod:`nfl_edge.strategy.edge`.  The same unit and inequality apply to both entry points.
    kelly_fraction, max_stake
        Kelly path: a bet's fraction is ``min(kelly_fraction * full_kelly, max_stake)`` of the
        bankroll at entry, before the exposure cap.
    max_weekly_exposure
        Cap on the Kelly fraction of the bankroll at risk at once (mirrors
        ``StrategyConfig.max_weekly_exposure``).  Bets entered at the same instant are scaled down
        proportionally so that, together with positions still open, they fit under it.  Must be
        in ``(0, 1]``, so the Kelly bankroll can never go negative.
    flat_stake
        Flat path: every bet risks this fraction of the starting bankroll (1.0).
    devig
        Method (see :func:`nfl_edge.odds.devig`) used to rebuild the devigged closing probability
        behind ``market_prob_devig`` / ``model_clv`` from the raw moneylines.  Always applied; the
        ``home_ml_prob``/``away_ml_prob`` columns of a ``load_games`` frame (multiplicative) are
        only reused when ``devig == "multiplicative"``.
    bet_types
        Only ``"moneyline"`` is supported by :func:`backtest_vs_closing`.  :func:`backtest_snapshots`
        keeps only these ``kind`` values when the stream carries a ``kind`` column and results are
        keyed by ``game_id``.
    """

    start_season: int = 2010
    end_season: int | None = None
    min_edge: float = 0.02
    kelly_fraction: float = 0.25
    max_stake: float = 0.03
    flat_stake: float = 0.01
    devig: str = "shin"
    bet_types: tuple[str, ...] = ("moneyline",)
    edge_metric: str = "ev"
    max_weekly_exposure: float = 0.15

    def __post_init__(self) -> None:
        if isinstance(self.bet_types, str):
            self.bet_types = (self.bet_types,)
        self.bet_types = tuple(self.bet_types)
        if self.flat_stake <= 0:
            raise ValueError("flat_stake must be > 0")
        if self.kelly_fraction < 0 or self.max_stake < 0:
            raise ValueError("kelly_fraction and max_stake must be >= 0")
        if not 0.0 < self.max_weekly_exposure <= 1.0:
            raise ValueError("max_weekly_exposure must be in (0, 1]")
        if self.end_season is not None and self.end_season < self.start_season:
            raise ValueError("end_season must be >= start_season")
        if self.devig not in _DEVIG_METHODS:
            raise ValueError(f"unknown devig method {self.devig!r}; choose one of {_DEVIG_METHODS}")
        if self.edge_metric not in _EDGE_METRICS:
            raise ValueError(f"edge_metric must be one of {_EDGE_METRICS}, got {self.edge_metric!r}")
        unknown = [b for b in self.bet_types if b not in _SUPPORTED_BET_TYPES]
        if unknown:
            raise ValueError(f"unsupported bet_types {unknown}; supported: {_SUPPORTED_BET_TYPES}")


@dataclass
class BacktestResult:
    """Output of a backtest.

    ``bets`` has one row per bet (columns :data:`BET_COLUMNS` first), ``summary`` the headline
    numbers (plain ``int``/``float`` values, JSON-serialisable), ``by_season`` a per-season table
    with a plain ``RangeIndex`` and ``season`` as its first column.
    """

    bets: pd.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)
    by_season: pd.DataFrame = field(default_factory=pd.DataFrame)


# ============================================================================ closing lines
def backtest_vs_closing(
    pred: pd.DataFrame, prob_col: str, cfg: BacktestConfig | None = None
) -> BacktestResult:
    """Bet home or away moneylines at the vigged closing price whenever the model sees value.

    Parameters
    ----------
    pred
        Played games with the ``load_games`` columns (``game_id, season, week, home_moneyline,
        away_moneyline, home_win`` are required; ``played``, ``game_date``, ``home_ml_prob``,
        ``away_ml_prob`` are used when present) plus ``prob_col``.
    prob_col
        Column holding the model's ``P(home win)`` for each game.
    cfg
        Betting rules; ``BacktestConfig()`` when omitted.

    For every game the home bet is evaluated at ``american_to_decimal(home_moneyline)`` and the
    away bet at ``american_to_decimal(away_moneyline)``.  The side with the larger edge
    (``cfg.edge_metric``) is bet when its edge exceeds ``cfg.min_edge``.  Games lacking a
    moneyline, a probability or a result are skipped.  Model CLV is ``prob - market_prob_devig``
    where the latter is the closing probability of the side bet, devigged with ``cfg.devig``.
    All of a week's bets are placed at once and resolve together at the end of the week.
    """
    cfg = cfg or BacktestConfig()
    if prob_col not in pred.columns:
        raise KeyError(f"prob_col {prob_col!r} not in frame")
    required = ["game_id", "season", "week", "home_moneyline", "away_moneyline", "home_win"]
    missing = [c for c in required if c not in pred.columns]
    if missing:
        raise KeyError(f"pred is missing required columns {missing}")

    played = pred["played"].astype(bool) if "played" in pred.columns else pred["home_win"].notna()
    mask = (
        played
        & pred["home_win"].notna()
        & pred["home_moneyline"].notna()
        & pred["away_moneyline"].notna()
        & pred[prob_col].notna()
        & (pred["season"] >= cfg.start_season)
    )
    if cfg.end_season is not None:
        mask &= pred["season"] <= cfg.end_season
    g = pred.loc[mask]

    p_home = g[prob_col].to_numpy(dtype=float)
    if len(p_home) and (np.nanmin(p_home) < 0.0 or np.nanmax(p_home) > 1.0):
        raise ValueError(f"{prob_col!r} must hold probabilities in [0, 1]")
    dec_h = _american_to_decimal_array(g["home_moneyline"].to_numpy(dtype=float))
    dec_a = _american_to_decimal_array(g["away_moneyline"].to_numpy(dtype=float))
    ok = np.isfinite(dec_h) & np.isfinite(dec_a) & (dec_h > 1.0) & (dec_a > 1.0)
    g, p_home, dec_h, dec_a = g.loc[ok], p_home[ok], dec_h[ok], dec_a[ok]
    n_games = int(len(g))

    ev_h = p_home * dec_h - 1.0
    ev_a = (1.0 - p_home) * dec_a - 1.0
    if cfg.edge_metric == "prob":
        edge_h = p_home - 1.0 / dec_h
        edge_a = (1.0 - p_home) - 1.0 / dec_a
    else:
        edge_h, edge_a = ev_h, ev_a
    take_home = edge_h >= edge_a - _EDGE_TIE_TOL
    edge = np.where(take_home, edge_h, edge_a)
    ev = np.where(take_home, ev_h, ev_a)
    prob = np.where(take_home, p_home, 1.0 - p_home)
    dec = np.where(take_home, dec_h, dec_a)
    home_win = g["home_win"].to_numpy(dtype=float)
    won = np.where(take_home, home_win, 1.0 - home_win)
    mkt_h, mkt_a = _devigged_market_probs(g, cfg.devig)
    market_prob_devig = np.where(take_home, mkt_h, mkt_a)

    bets = pd.DataFrame(
        {
            "game_id": g["game_id"].to_numpy(),
            "season": g["season"].to_numpy(dtype=int),
            "week": g["week"].to_numpy(dtype=int),
            "side": np.where(take_home, "home", "away"),
            "prob": prob,
            "dec_odds": dec,
            "ev": ev,
            "market_prob_devig": market_prob_devig,
            "model_clv": prob - market_prob_devig,
            "won": won,
        }
    )
    if "game_date" in g.columns:
        bets["game_date"] = g["game_date"].to_numpy()
    if "home_team_c" in g.columns and "away_team_c" in g.columns:
        bets["home_team_c"] = g["home_team_c"].to_numpy()
        bets["away_team_c"] = g["away_team_c"].to_numpy()
    bets = bets.loc[edge > cfg.min_edge]

    # all bets of a (season, week) are entered together at the start of the week and resolve
    # together at its end: entry ordinal 2k, resolution ordinal 2k + 1 for the k-th week
    if len(bets):
        wk = bets.groupby(["season", "week"], sort=True).ngroup().to_numpy(dtype=np.int64)
    else:
        wk = np.zeros(0, dtype=np.int64)
    bets["_entry"] = 2 * wk
    bets["_resolve"] = 2 * wk + 1

    order = ["game_date", "game_id"] if "game_date" in bets.columns else ["game_id"]
    bets = _settle(bets, cfg, order_cols=order)
    summary = _summarize(bets, cfg)
    summary["n_games"] = n_games
    return BacktestResult(bets=bets, summary=summary, by_season=_by_season(bets))


def _american_to_decimal_array(odds: np.ndarray) -> np.ndarray:
    """Vectorised :func:`nfl_edge.odds.american_to_decimal` (NaN in, NaN out; 0 -> NaN)."""
    o = np.asarray(odds, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(o > 0, 1.0 + o / 100.0, 1.0 + 100.0 / np.abs(o))
    return np.where(np.isnan(o) | (o == 0), np.nan, out)


def _devigged_market_probs(g: pd.DataFrame, method: str) -> tuple[np.ndarray, np.ndarray]:
    """Devigged (home, away) closing probabilities, rebuilt from the raw moneylines with ``method``.

    :func:`nfl_edge.odds.devig` is solved once per distinct odds pair.  The frame's own
    ``home_ml_prob``/``away_ml_prob`` (multiplicative, from ``load_games``) are reused only when
    ``method == "multiplicative"``; rows lacking them are rebuilt anyway.
    """
    n = len(g)
    h = np.full(n, np.nan)
    a = np.full(n, np.nan)
    if method == "multiplicative" and "home_ml_prob" in g.columns and "away_ml_prob" in g.columns:
        h = g["home_ml_prob"].to_numpy(dtype=float).copy()
        a = g["away_ml_prob"].to_numpy(dtype=float).copy()
    need = np.isnan(h) | np.isnan(a)
    if need.any():
        raw_h = american_to_prob_array(g["home_moneyline"].to_numpy(dtype=float))[need]
        raw_a = american_to_prob_array(g["away_moneyline"].to_numpy(dtype=float))[need]
        pairs = np.column_stack([raw_h, raw_a])
        uniq, inverse = np.unique(pairs, axis=0, return_inverse=True)
        solved = np.array([devig([ph, pa], method=method) for ph, pa in uniq], dtype=float)
        inverse = np.asarray(inverse).ravel()
        h[need] = solved[inverse, 0]
        a[need] = solved[inverse, 1]
    return h, a


# ============================================================================ snapshots
def backtest_snapshots(
    snapshots: pd.DataFrame,
    fair_probs: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    """Backtest on recorded Polymarket quotes.

    Parameters
    ----------
    snapshots
        One row per (time, market, side) with ``ts`` (ISO / datetime), ``market_id``, ``game_id``
        (unless ``results`` is keyed by ``market_id``), the side and its ask, and, unless
        ``fair_probs`` is given, ``fair_prob`` (the model's probability *of that side* at that
        time).  Two spellings are accepted:

        * hand-built: ``side`` (``"YES"``/``"NO"``) and ``price`` (the ask of that side);
        * native (:func:`nfl_edge.markets.polymarket.load_snapshots`): ``is_yes`` gives the side
          and ``best_ask`` the entry price -- ``price`` (the Gamma outcome price) is then never an
          entry price, and rows without an ask cannot be entered.

        An optional ``mid`` column (side-specific mid) is preferred over the ask as the closing
        reference and as the market probability behind ``model_clv``.
    fair_probs
        Optional table to merge instead of a pre-merged ``fair_prob`` column: ``fair_prob`` keyed by
        ``market_id`` (or ``game_id``), optionally by ``side`` (else ``fair_prob`` is P(YES) and NO
        gets ``1 - fair_prob``), optionally time-varying via a ``ts`` column (merged backward in
        time, so a snapshot only sees fair probabilities computed at or before it).
    results
        ``game_id`` (or ``market_id``) -> ``yes_won`` (1 / 0 / 0.5 for a void or tie) plus the
        kickoff: a ``kickoff`` column (ISO / datetime, UTC when naive), or ``game_date`` with an
        optional ``gametime`` (``"HH:MM"`` Eastern, as in nflverse); a bare ``game_date`` is read as
        midnight UTC, i.e. every quote on game day counts as in-game.  A ``kickoff`` column on the
        snapshot stream is used when ``results`` carries neither; without any the call raises
        ``ValueError`` -- an in-game or post-resolution quote must never serve as an entry or as
        the closing reference.  Optional ``season``/``week`` columns label the game week; missing
        labels are derived from the kickoff with :func:`nfl_season_week`.
    cfg
        Betting rules; ``BacktestConfig()`` when omitted.

    A position is entered at the ask of the *first* snapshot of each (market, side) strictly before
    kickoff whose edge (``cfg.edge_metric``) exceeds ``cfg.min_edge``.  CLV is the last pre-kickoff
    mid (ask when no mid is recorded) of that side minus the entry price.  Kelly stakes are sized
    off the bankroll at the entry timestamp (bets resolve :data:`SETTLEMENT_LAG` after kickoff);
    bets quoted at the same timestamp are a batch under ``cfg.max_weekly_exposure``.  When the
    stream has a ``kind`` column and results are keyed by ``game_id``, only ``cfg.bet_types`` rows
    are settled (a game-level ``yes_won`` says nothing about a spread or total market).
    """
    cfg = cfg or BacktestConfig()
    if results is None:
        raise ValueError("results (game_id -> yes_won) is required to settle snapshot bets")
    for c in ("ts", "market_id"):
        if c not in snapshots.columns:
            raise KeyError(f"snapshots is missing required column {c!r}")
    if "yes_won" not in results.columns:
        raise KeyError("results must have a 'yes_won' column")

    snaps = _normalise_snapshots(snapshots)
    if fair_probs is not None:
        snaps = _attach_fair_probs(snaps, fair_probs)
    if "fair_prob" not in snaps.columns:
        raise KeyError("snapshots needs a 'fair_prob' column or a fair_probs table")
    snaps["fair_prob"] = snaps["fair_prob"].astype(float)

    # ---- results join (by market_id when available, else game_id)
    key = "market_id" if "market_id" in results.columns else "game_id"
    if key not in snaps.columns:
        raise KeyError(f"snapshots lacks {key!r} needed to join results")
    if key == "game_id" and "kind" in snaps.columns:
        snaps = snaps.loc[snaps["kind"].astype(str).isin(cfg.bet_types)]
    res = results.drop_duplicates(subset=[key], keep="last").copy()
    res[key] = res[key].astype(str)
    snaps[key] = snaps[key].astype(str)
    res_cols = [key, "yes_won"] + [c for c in ("season", "week") if c in res.columns and c not in snaps.columns]
    if "kickoff" in res.columns or "game_date" in res.columns:
        res["kickoff"] = _results_kickoff(res)
        res_cols.append("kickoff")
        snaps = snaps.drop(columns=["kickoff"], errors="ignore")
    elif "kickoff" in snaps.columns:                       # the stream itself carries the kickoff
        snaps["kickoff"] = pd.to_datetime(snaps["kickoff"], utc=True, errors="coerce")
    else:
        raise ValueError(
            "results needs a 'kickoff' column (or 'game_date' [+ 'gametime' Eastern]) so that "
            "in-game and post-resolution quotes can be excluded from entries and closing prices"
        )
    snaps = snaps.merge(res[res_cols], on=key, how="left")
    snaps = snaps.sort_values(["market_id", "side", "ts"], kind="mergesort").reset_index(drop=True)

    pre_kick = snaps["kickoff"].notna() & (snaps["ts"] < snaps["kickoff"])
    valid_px = (snaps["price"] > 0.0) & (snaps["price"] < 1.0)
    settled = snaps["yes_won"].notna()
    if cfg.edge_metric == "prob":
        edge = snaps["fair_prob"] - snaps["price"]
    else:
        edge = snaps["fair_prob"] / snaps["price"] - 1.0
    qual = settled & pre_kick & valid_px & snaps["fair_prob"].notna() & (edge > cfg.min_edge)
    entries = snaps.loc[qual].groupby(["market_id", "side"], sort=False).head(1).copy()

    ref_col = "mid" if "mid" in snaps.columns else "price"
    close = (
        snaps.loc[pre_kick & valid_px & snaps[ref_col].notna()]
        .groupby(["market_id", "side"], sort=False)
        .tail(1)[["market_id", "side", ref_col]]
        .rename(columns={ref_col: "close_price"})
    )
    entries = entries.merge(close, on=["market_id", "side"], how="left")

    yes_won = entries["yes_won"].to_numpy(dtype=float)
    is_yes = (entries["side"] == "YES").to_numpy()
    won = np.where(is_yes, yes_won, 1.0 - yes_won)
    price = entries["price"].to_numpy(dtype=float)
    prob = entries["fair_prob"].to_numpy(dtype=float)
    dec = 1.0 / price
    if "mid" in entries.columns:
        mid = entries["mid"].to_numpy(dtype=float)
        market_prob = np.where(np.isfinite(mid), mid, price)
    else:
        market_prob = price
    season, week = _season_week(entries)
    entry_ts = pd.DatetimeIndex(entries["ts"])
    resolve_ts = pd.DatetimeIndex(entries["kickoff"]) + SETTLEMENT_LAG

    bets = pd.DataFrame(
        {
            "game_id": entries["game_id"].to_numpy() if "game_id" in entries.columns else entries["market_id"].to_numpy(),
            "season": season,
            "week": week,
            "side": entries["side"].to_numpy(),
            "prob": prob,
            "dec_odds": dec,
            "ev": prob * dec - 1.0,
            "market_prob_devig": market_prob,
            "model_clv": prob - market_prob,
            "won": won,
            "market_id": entries["market_id"].to_numpy(),
            "entry_ts": entry_ts,
            "price": price,
            "close_price": entries["close_price"].to_numpy(dtype=float),
            "clv": entries["close_price"].to_numpy(dtype=float) - price,
            "kickoff": pd.DatetimeIndex(entries["kickoff"]),
            "_entry": entry_ts.asi8,
            "_resolve": resolve_ts.asi8,
        }
    )
    bets = _settle(bets, cfg, order_cols=["market_id", "side"])
    summary = _summarize(bets, cfg)
    summary["n_markets"] = int(bets["market_id"].nunique()) if len(bets) else 0
    summary["avg_clv"] = float(bets["clv"].mean()) if len(bets) else math.nan
    summary["clv_hit_rate"] = float((bets["clv"] > 0).mean()) if len(bets) else math.nan
    return BacktestResult(bets=bets, summary=summary, by_season=_by_season(bets))


def _normalise_snapshots(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Copy of the stream with UTC ``ts``, a ``side`` column and ``price`` = the side's ask.

    Accepts the hand-built spelling (``side`` + ``price``) and the native
    :func:`nfl_edge.markets.polymarket.load_snapshots` spelling (``is_yes`` + ``best_ask``).
    """
    snaps = snapshots.copy()
    snaps["ts"] = pd.to_datetime(snaps["ts"], utc=True, errors="coerce")
    if "side" not in snaps.columns:
        if "is_yes" not in snaps.columns:
            raise KeyError("snapshots needs a 'side' (YES/NO) column or the native 'is_yes' flag")
        snaps["side"] = np.where(snaps["is_yes"].astype(bool), "YES", "NO")
    snaps["side"] = snaps["side"].astype(str).str.upper()
    bad_side = ~snaps["side"].isin(["YES", "NO"])
    if bad_side.any():
        raise ValueError(f"snapshot side must be YES/NO, got {sorted(snaps.loc[bad_side, 'side'].unique())}")
    if "best_ask" in snaps.columns:
        if "price" in snaps.columns:
            snaps = snaps.rename(columns={"price": "outcome_price"})
        snaps["price"] = pd.to_numeric(snaps["best_ask"], errors="coerce").astype(float)
    elif "price" in snaps.columns:
        snaps["price"] = pd.to_numeric(snaps["price"], errors="coerce").astype(float)
    else:
        raise KeyError("snapshots needs a 'price' (ask of the side) or native 'best_ask' column")
    if "mid" in snaps.columns:
        snaps["mid"] = pd.to_numeric(snaps["mid"], errors="coerce").astype(float)
    snaps["market_id"] = snaps["market_id"].astype(str)
    return snaps.loc[snaps["ts"].notna()].reset_index(drop=True)


def _results_kickoff(res: pd.DataFrame) -> pd.Series:
    """UTC kickoff per results row from ``kickoff``, else ``game_date`` (+ ``gametime`` Eastern)."""
    if "kickoff" in res.columns:
        return pd.to_datetime(res["kickoff"], utc=True, errors="coerce")
    day = pd.to_datetime(res["game_date"], errors="coerce")
    if day.dt.tz is not None:
        day = day.dt.tz_convert("UTC").dt.tz_localize(None)
    day = day.dt.normalize()
    kickoff = day.dt.tz_localize("UTC")          # conservative: midnight UTC of game day
    if "gametime" in res.columns:
        hhmm = res["gametime"].astype("string").str.strip()
        has = hhmm.notna() & hhmm.str.fullmatch(r"\d{1,2}:\d{2}").fillna(False) & day.notna()
        if has.any():
            local = pd.to_datetime(
                day[has].dt.strftime("%Y-%m-%d") + " " + hhmm[has].astype(str), errors="coerce"
            )
            local = local.dt.tz_localize(_ET, ambiguous="NaT", nonexistent="shift_forward")
            kickoff = kickoff.copy()
            kickoff[has] = local.dt.tz_convert("UTC")
    return kickoff


def _attach_fair_probs(snaps: pd.DataFrame, fair_probs: pd.DataFrame) -> pd.DataFrame:
    """Merge a ``fair_prob`` table onto the snapshot stream (see :func:`backtest_snapshots`)."""
    if "fair_prob" not in fair_probs.columns:
        raise KeyError("fair_probs must have a 'fair_prob' column")
    fp = fair_probs.copy()
    key = "market_id" if "market_id" in fp.columns else "game_id"
    if key not in fp.columns or key not in snaps.columns:
        raise KeyError("fair_probs must be keyed by 'market_id' or 'game_id' present in snapshots")
    fp[key] = fp[key].astype(str)
    snaps = snaps.copy()
    snaps[key] = snaps[key].astype(str)
    by = [key]
    if "side" in fp.columns:
        fp["side"] = fp["side"].astype(str).str.upper()
        by.append("side")
    fp = fp.rename(columns={"fair_prob": "_fp"})
    if "ts" in fp.columns:
        fp["ts"] = pd.to_datetime(fp["ts"], utc=True)
        fp = fp.sort_values("ts", kind="mergesort")
        snaps = snaps.sort_values("ts", kind="mergesort")
        merged = pd.merge_asof(snaps, fp[by + ["ts", "_fp"]], on="ts", by=by, direction="backward")
    else:
        merged = snaps.merge(fp[by + ["_fp"]].drop_duplicates(subset=by, keep="last"), on=by, how="left")
    fpv = merged["_fp"].astype(float)
    if "side" not in by:
        fpv = fpv.where(merged["side"] == "YES", 1.0 - fpv)
    if "fair_prob" in merged.columns:
        fpv = fpv.fillna(merged["fair_prob"].astype(float))
    merged["fair_prob"] = fpv
    return merged.drop(columns=["_fp"])


def nfl_season_week(when: Iterable) -> tuple[np.ndarray, np.ndarray]:
    """(season, week) labels for kickoff timestamps, monotone in time within a season.

    ``season`` follows :func:`nfl_edge.config.season_for_date` (January/February belong to the
    previous season).  ``week`` counts 7-day bins (Wednesday-Tuesday, UTC) from the Wednesday
    before the NFL opener -- the Thursday after Labor Day -- so a Sunday in early September is
    week 1 and the January playoff rounds continue the count (19, 20, ...) instead of wrapping to
    the calendar week.  Dates before the opener give week <= 0.  Exact for seasons opening the
    Thursday after Labor Day (2002 onward); only used to label weeks, never to size bets.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(when) if not isinstance(when, (pd.Series, pd.Index, np.ndarray)) else when, utc=True))
    n = len(idx)
    season = np.zeros(n, dtype=int)
    week = np.zeros(n, dtype=int)
    if n == 0:
        return season, week
    if idx.isna().any():
        raise ValueError("cannot derive season/week from a missing kickoff")
    naive = idx.tz_convert("UTC").tz_localize(None)
    season = np.array([season_for_date(d) for d in naive.date], dtype=int)
    days = naive.normalize().values.astype("datetime64[D]").astype(np.int64)   # days since the epoch
    for s in np.unique(season):
        sep1 = date(int(s), 9, 1)
        labor_day = sep1 + timedelta(days=(7 - sep1.weekday()) % 7)      # first Monday of September
        anchor = labor_day + timedelta(days=2)                             # Wednesday before the opener
        anchor_days = (anchor - date(1970, 1, 1)).days
        sel = season == s
        week[sel] = (days[sel] - anchor_days) // 7 + 1
    return season, week


def _season_week(entries: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(season, week) arrays for snapshot entries: explicit columns, else derived from the kickoff."""
    n = len(entries)
    season = pd.Series(np.nan, index=entries.index, dtype=float)
    week = pd.Series(np.nan, index=entries.index, dtype=float)
    if "season" in entries.columns:
        season = pd.to_numeric(entries["season"], errors="coerce").astype(float)
    if "week" in entries.columns:
        week = pd.to_numeric(entries["week"], errors="coerce").astype(float)
    need = (season.isna() | week.isna()).to_numpy()
    if need.any():
        d_season, d_week = nfl_season_week(entries.loc[need, "kickoff"])
        season_v, week_v = season.to_numpy(dtype=float, copy=True), week.to_numpy(dtype=float, copy=True)
        season_v[need] = np.where(np.isnan(season_v[need]), d_season, season_v[need])
        week_v[need] = np.where(np.isnan(week_v[need]), d_week, week_v[need])
        season, week = pd.Series(season_v), pd.Series(week_v)
    if n == 0:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    return season.to_numpy(dtype=int), week.to_numpy(dtype=int)


# ============================================================================ settlement
def _settle(bets: pd.DataFrame, cfg: BacktestConfig, order_cols: Sequence[str]) -> pd.DataFrame:
    """Add stakes and profits for the flat path and the chronological fractional-Kelly path.

    Requires columns ``season, week, dec_odds, ev, won`` (``won`` in {0, 0.5, 1}) and the integer
    ordinals ``_entry`` (when the bet is placed) and ``_resolve`` (when its outcome is known and the
    stake settles; strictly after ``_entry``).  Bets are ordered by (``_entry``, *order_cols*).

    Kelly path: the bankroll starts at 1.0 and is credited with a bet's profit at its ``_resolve``.
    A bet entered at ``t`` is sized off the bankroll after every bet with ``_resolve <= t``.  Its
    fraction is ``min(kelly_fraction * full_kelly, max_stake)``; the bets of one entry instant are
    then scaled down proportionally so that, together with the stakes still open at ``t``, they
    stay within ``max_weekly_exposure`` of that bankroll.  Because the cap is <= 1 the bankroll can
    reach 0 but never go below it.
    """
    cols = [c for c in BET_COLUMNS if c in bets.columns] + [c for c in bets.columns if c not in BET_COLUMNS]
    bets = bets[cols].sort_values(["_entry", *order_cols], kind="mergesort").reset_index(drop=True)
    n = len(bets)
    dec = bets["dec_odds"].to_numpy(dtype=float)
    ev = bets["ev"].to_numpy(dtype=float)
    won = bets["won"].to_numpy(dtype=float)
    entry = bets["_entry"].to_numpy(dtype=np.int64)
    resolve = bets["_resolve"].to_numpy(dtype=np.int64)
    if n and not np.all(resolve > entry):
        raise ValueError("every bet must resolve strictly after its entry")

    # profit multiplier per $1 staked: +(dec-1) on a win, 0 on a push, -1 on a loss
    mult = np.select([won >= 1.0, won == 0.5], [dec - 1.0, 0.0], default=-1.0)
    stake_flat = np.full(n, float(cfg.flat_stake))
    profit_flat = stake_flat * mult

    with np.errstate(divide="ignore", invalid="ignore"):
        full_kelly = np.where(dec > 1.0, ev / (dec - 1.0), 0.0)
    frac = np.minimum(cfg.kelly_fraction * np.clip(full_kelly, 0.0, None), cfg.max_stake)

    bank_start = np.zeros(n)
    stake_kelly = np.zeros(n)
    bank = 1.0
    open_stake = 0.0
    open_heap: list[tuple[int, float, float]] = []       # (resolve ordinal, stake, profit)
    cap = float(cfg.max_weekly_exposure)
    i = 0
    while i < n:
        j = i + 1
        while j < n and entry[j] == entry[i]:
            j += 1
        t = entry[i]
        while open_heap and open_heap[0][0] <= t:
            _, st, pr = heapq.heappop(open_heap)
            bank += pr
            open_stake -= st
        bank = max(bank, 0.0)
        open_stake = max(open_stake, 0.0)
        room = max(0.0, cap - open_stake / bank) if bank > 0.0 else 0.0
        batch = frac[i:j]
        total = float(batch.sum())
        scale = 1.0 if total <= room else (room / total if total > 0.0 else 0.0)
        stakes = batch * scale * bank
        profits = stakes * mult[i:j]
        bank_start[i:j] = bank
        stake_kelly[i:j] = stakes
        # one heap entry per distinct resolution instant of the batch
        r_uniq, r_inv = np.unique(resolve[i:j], return_inverse=True)
        r_inv = np.asarray(r_inv).ravel()
        st_sum = np.bincount(r_inv, weights=stakes, minlength=len(r_uniq))
        pr_sum = np.bincount(r_inv, weights=profits, minlength=len(r_uniq))
        for r, st, pr in zip(r_uniq.tolist(), st_sum.tolist(), pr_sum.tolist()):
            heapq.heappush(open_heap, (r, st, pr))
        open_stake += float(stakes.sum())
        i = j
    profit_kelly = stake_kelly * mult

    bets["stake_flat"] = stake_flat
    bets["stake_kelly"] = stake_kelly
    bets["profit_flat"] = profit_flat
    bets["profit_kelly"] = profit_kelly
    bets["bankroll_start"] = bank_start
    bets = bets.drop(columns=["_entry", "_resolve"])
    ordered = list(BET_COLUMNS) + [c for c in bets.columns if c not in BET_COLUMNS]
    return bets[ordered]


def _weekly(bets: pd.DataFrame) -> pd.DataFrame:
    """Per game-week aggregates in chronological order (weeks with at least one bet).

    ``bankroll_end`` is the Kelly bankroll once every bet of the week has resolved: the starting
    bankroll plus all Kelly profit up to and including that week (never below zero).
    """
    if bets.empty:
        return pd.DataFrame(columns=["n_bets", "profit_flat", "profit_kelly", "bankroll_start", "bankroll_end"])
    w = bets.groupby(["season", "week"], sort=True).agg(
        n_bets=("won", "size"),
        profit_flat=("profit_flat", "sum"),
        profit_kelly=("profit_kelly", "sum"),
    )
    w["bankroll_end"] = np.maximum(1.0 + np.cumsum(w["profit_kelly"].to_numpy(dtype=float)), 0.0)
    w["bankroll_start"] = np.concatenate([[1.0], w["bankroll_end"].to_numpy(dtype=float)[:-1]])
    return w[["n_bets", "profit_flat", "profit_kelly", "bankroll_start", "bankroll_end"]]


def _summarize(bets: pd.DataFrame, cfg: BacktestConfig) -> dict[str, Any]:
    """Headline numbers for a settled ``bets`` frame (plain Python scalars).

    Equity curves are weekly (bankroll after each settled game week, starting at 1.0).  Drawdowns
    are capped at 1.0: a flat-stake path whose cumulative loss exceeds the starting bankroll is
    bust.  ``hit_rate`` excludes pushes; ``weekly_sharpe`` is mean/std of weekly flat profit over
    weeks with bets, annualised by ``sqrt(WEEKS_PER_YEAR)``.
    """
    n = int(len(bets))
    weekly = _weekly(bets)
    staked_flat = float(bets["stake_flat"].sum()) if n else 0.0
    staked_kelly = float(bets["stake_kelly"].sum()) if n else 0.0
    profit_flat = float(bets["profit_flat"].sum()) if n else 0.0
    profit_kelly = float(bets["profit_kelly"].sum()) if n else 0.0
    decided = bets["won"] != 0.5 if n else pd.Series(dtype=bool)
    n_decided = int(decided.sum())
    hit_rate = float((bets.loc[decided, "won"] >= 1.0).mean()) if n_decided else math.nan

    flat_equity = np.concatenate([[1.0], 1.0 + np.cumsum(weekly["profit_flat"].to_numpy(dtype=float))])
    kelly_equity = np.concatenate([[1.0], weekly["bankroll_end"].to_numpy(dtype=float)])
    lo, hi = bootstrap_roi(bets["profit_flat"].to_numpy(), bets["stake_flat"].to_numpy())

    wp = weekly["profit_flat"].to_numpy(dtype=float)
    if len(wp) >= 2 and np.std(wp, ddof=1) > 0:
        weekly_sharpe = float(np.mean(wp) / np.std(wp, ddof=1) * math.sqrt(WEEKS_PER_YEAR))
    else:
        weekly_sharpe = math.nan

    return {
        "n_bets": n,
        "n_weeks": int(len(weekly)),
        "n_pushes": int(n - n_decided),
        "hit_rate": hit_rate,
        "roi_flat": profit_flat / staked_flat if staked_flat > 0 else math.nan,
        "roi_kelly": profit_kelly / staked_kelly if staked_kelly > 0 else math.nan,
        "profit_flat": profit_flat,
        "profit_kelly": profit_kelly,
        "staked_flat": staked_flat,
        "staked_kelly": staked_kelly,
        "final_bankroll_kelly": float(kelly_equity[-1]),
        "max_drawdown_flat": min(1.0, max_drawdown(flat_equity)),
        "max_drawdown_kelly": min(1.0, max_drawdown(kelly_equity)),
        "roi_ci_lo": lo,
        "roi_ci_hi": hi,
        "avg_model_clv": float(bets["model_clv"].mean()) if n else math.nan,
        "avg_ev": float(bets["ev"].mean()) if n else math.nan,
        "avg_dec_odds": float(bets["dec_odds"].mean()) if n else math.nan,
        "weekly_sharpe": weekly_sharpe,
        "start_season": int(bets["season"].min()) if n else cfg.start_season,
        "end_season": int(bets["season"].max()) if n else (cfg.end_season if cfg.end_season is not None else cfg.start_season),
        "min_edge": float(cfg.min_edge),
        "edge_metric": str(cfg.edge_metric),
        "devig": str(cfg.devig),
        "kelly_fraction": float(cfg.kelly_fraction),
        "max_stake": float(cfg.max_stake),
        "max_weekly_exposure": float(cfg.max_weekly_exposure),
        "flat_stake": float(cfg.flat_stake),
    }


def _by_season(bets: pd.DataFrame) -> pd.DataFrame:
    """Per-season table (``RangeIndex``; ``season`` first): n_bets, hit_rate, roi_flat, profit_flat,
    roi_kelly, profit_kelly, avg_model_clv."""
    cols = ["season", "n_bets", "hit_rate", "roi_flat", "profit_flat", "roi_kelly", "profit_kelly", "avg_model_clv"]
    if bets.empty:
        out = pd.DataFrame({c: pd.Series(dtype=int if c in ("season", "n_bets") else float) for c in cols})
        return out[cols]
    grp = bets.groupby("season", sort=True)
    decided = bets["won"] != 0.5
    hits = (bets["won"] >= 1.0) & decided
    out = pd.DataFrame(
        {
            "n_bets": grp.size(),
            "hit_rate": hits.groupby(bets["season"]).sum() / decided.groupby(bets["season"]).sum().replace(0, np.nan),
            "roi_flat": grp["profit_flat"].sum() / grp["stake_flat"].sum(),
            "profit_flat": grp["profit_flat"].sum(),
            "roi_kelly": grp["profit_kelly"].sum() / grp["stake_kelly"].sum().replace(0.0, np.nan),
            "profit_kelly": grp["profit_kelly"].sum(),
            "avg_model_clv": grp["model_clv"].mean(),
        }
    )
    out.insert(0, "season", out.index.astype(int))
    out = out.reset_index(drop=True)
    out["n_bets"] = out["n_bets"].astype(int)
    return out[cols]


# ============================================================================ statistics
def max_drawdown(equity: Iterable[float]) -> float:
    """Largest peak-to-trough decline of an equity curve as a fraction of the running peak.

    ``[1.0, 1.2, 0.9, 1.1, 0.8, 1.3]`` -> ``(1.2 - 0.8) / 1.2``.  A non-decreasing curve (or an
    empty one) gives 0.0.  NaNs are ignored; a non-positive running peak contributes no drawdown.
    """
    eq = np.asarray(list(equity) if not isinstance(equity, (np.ndarray, pd.Series)) else equity, dtype=float).ravel()
    eq = eq[np.isfinite(eq)]
    if eq.size == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (peak - eq) / peak, 0.0)
    return float(np.max(dd))


def bootstrap_roi(
    profits: Iterable[float], stakes: Iterable[float], n: int = 2000, seed: int = 0, ci: float = 0.95
) -> tuple[float, float]:
    """Percentile-bootstrap confidence interval for ROI = sum(profits) / sum(stakes).

    Bets are resampled with replacement ``n`` times.  Returns ``(lo, hi)``; ``(nan, nan)`` when
    there are no bets or nothing was staked.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if not 0.0 < ci < 1.0:
        raise ValueError("ci must be in (0, 1)")
    p = np.asarray(list(profits) if not isinstance(profits, (np.ndarray, pd.Series)) else profits, dtype=float).ravel()
    s = np.asarray(list(stakes) if not isinstance(stakes, (np.ndarray, pd.Series)) else stakes, dtype=float).ravel()
    if p.shape != s.shape:
        raise ValueError("profits and stakes must have the same length")
    m = p.size
    if m == 0 or s.sum() <= 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    rois = np.empty(n, dtype=float)
    chunk = max(1, min(n, int(2_000_000 // m)))
    for start in range(0, n, chunk):
        k = min(chunk, n - start)
        idx = rng.integers(0, m, size=(k, m))
        ps = p[idx].sum(axis=1)
        ss = s[idx].sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            rois[start:start + k] = np.where(ss > 0, ps / ss, np.nan)
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.nanpercentile(rois, [100.0 * alpha, 100.0 * (1.0 - alpha)])
    return float(lo), float(hi)


# ============================================================================ reporting
_SIGNED_PCT_KEYS = ("roi", "clv")            # signed percentages
_PCT_KEYS = ("hit_rate", "max_drawdown")       # unsigned percentages
_INT_KEYS = ("n_", "start_season", "end_season")


def _fmt(key: str, value: Any) -> str:
    """Human-readable cell: percentages for ratios, ints for counts, 4 decimals otherwise."""
    if value is None:
        return "n/a"
    if isinstance(value, (str, bool)):
        return str(value)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(v):
        return "n/a"
    if key == "season" or key.startswith(_INT_KEYS) or key.endswith("_season"):
        return f"{int(round(v)):d}"
    if key.startswith(_SIGNED_PCT_KEYS) or key.endswith("_clv"):
        return f"{100.0 * v:+.2f}%"
    if key.startswith(_PCT_KEYS):
        return f"{100.0 * v:.2f}%"
    if key in ("weekly_sharpe",):
        return f"{v:.2f}"
    return f"{v:.4f}"


def _md_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return lines


def report_markdown(result: BacktestResult) -> str:
    """Render ``summary`` and ``by_season`` as a Markdown report."""
    s = result.summary
    n = int(s.get("n_bets", len(result.bets)))
    lines = ["# Backtest report", ""]
    if n:
        lines.append(
            f"**{n} bets**, seasons {_fmt('start_season', s.get('start_season'))}-"
            f"{_fmt('end_season', s.get('end_season'))}: flat ROI {_fmt('roi_flat', s.get('roi_flat'))} "
            f"(95% CI {_fmt('roi_ci_lo', s.get('roi_ci_lo'))} to {_fmt('roi_ci_hi', s.get('roi_ci_hi'))}), "
            f"hit rate {_fmt('hit_rate', s.get('hit_rate'))}, Kelly ROI {_fmt('roi_kelly', s.get('roi_kelly'))}, "
            f"final Kelly bankroll {_fmt('final_bankroll_kelly', s.get('final_bankroll_kelly'))}, "
            f"avg model CLV {_fmt('avg_model_clv', s.get('avg_model_clv'))}."
        )
    else:
        lines.append("**No bets** cleared the edge threshold.")
    lines += ["", "## Summary", ""]
    lines += _md_table(["Metric", "Value"], ((k, _fmt(k, v)) for k, v in s.items()))
    lines += ["", "## By season", ""]
    bs = result.by_season
    if bs.empty:
        lines.append("_no bets_")
    else:
        cols = [c for c in bs.columns if c != "season"]
        rows = (
            [_fmt("season", row["season"])] + [_fmt(c, row[c]) for c in cols]
            for _, row in bs.iterrows()
        )
        lines += _md_table(["season", *cols], rows)
    lines.append("")
    return "\n".join(lines)
