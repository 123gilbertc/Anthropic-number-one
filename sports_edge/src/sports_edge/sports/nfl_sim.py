"""SYNTHETIC NFL game simulator and synthetic-only model training.

Everything here is invented to exercise plumbing: drive outcome probabilities,
play counts and clock use are rough guesses, not estimates. Models trained on this
output are ``SYNTHETIC_ONLY`` and are never alert-eligible outside the explicit
mechanics demo. Real models need licensed play-by-play history.

The simulator emits the same normalized events the live adapter would, and the
training rows are built by replaying those events through the real ``NFLReducer``
and ``build_features`` (one code path for training and live).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

QUARTER = 900


@dataclass
class SimEvent:
    t: int  # game seconds elapsed (OT continues past 3600)
    type: str
    data: dict = field(default_factory=dict)


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def simulate(rng: np.random.Generator, home: str, away: str, edge: float,
             season_type: str = "REGULAR") -> tuple[list[SimEvent], dict]:
    """``edge``: home strength advantage in logit units (synthetic)."""
    ev: list[SimEvent] = []
    score = {home: 0, away: 0}
    reg_end = 4 * QUARTER
    ot_len = 600 if season_type == "REGULAR" else 900
    opening = home if rng.random() < 0.5 else away
    poss, yl, t = opening, 75, 0
    period_end = QUARTER  # end of the current quarter / OT period
    period = 1
    ot_possessions: set[str] = set()
    timeouts = {home: 3, away: 3}

    def other(team: str) -> str:
        return away if team == home else home

    def clock(tt: int) -> dict:
        return {"quarter": period, "seconds_remaining_in_quarter": max(0, period_end - tt)}

    ev.append(SimEvent(0, "SNAPSHOT", {**clock(0), "home_score": 0, "away_score": 0,
                                       "possession": poss, "down": 1, "distance": 10,
                                       "yardline_100": yl, "home_timeouts": 3,
                                       "away_timeouts": 3, "season_type": season_type,
                                       "home_qb": f"{home}-QB1", "away_qb": f"{away}-QB1"}))

    def end_period() -> str:
        """Emit QUARTER_END; returns 'continue', 'half', 'ot' or 'over'."""
        nonlocal period, period_end, t
        t = period_end
        nxt = period + 1
        if nxt == 5 and score[home] != score[away]:
            return "over"  # regulation ended with a winner: no overtime period
        if nxt >= 6 and season_type == "REGULAR":
            return "over"  # the single regular-season OT period expired
        ev.append(SimEvent(t, "QUARTER_END", {}))
        period = nxt
        if period <= 4:
            period_end = period * QUARTER
            if period == 3:
                timeouts[home] = timeouts[away] = 3
            return "half" if period == 3 else "continue"
        period_end = reg_end + (period - 4) * ot_len
        return "ot"

    while True:
        if period >= 5:
            ot_possessions.add(poss)
        s = edge if poss == home else -edge
        fp = (100 - yl) / 100
        p_td = _sig(-1.6 + 1.6 * fp + 0.9 * s)
        p_fg = _sig(-1.4 + 2.2 * fp + 0.4 * s) * (1 - p_td)
        r = rng.random()
        outcome = "TD" if r < p_td else "FG" if r < p_td + p_fg else \
            "TO" if r < p_td + p_fg + 0.12 else "PUNT"
        plays = int(rng.integers(3, 11))
        target = 0 if outcome == "TD" else max(1, int(rng.normal(25 if outcome == "FG" else 55, 8)))
        cur, down, dist = yl, 1, 10
        interrupted = None
        for i in range(plays):
            t += int(rng.integers(25, 42))
            if t >= period_end:
                interrupted = end_period()
                if interrupted in ("half", "over", "ot") or \
                        (interrupted == "ot" and season_type == "REGULAR" and period >= 6):
                    break
            step = (cur - target) / max(1, plays - i)
            step = int(max(-5, step + rng.normal(0, 3)))
            cur = int(max(1, min(99, cur - step)))
            dist -= step
            if dist <= 0:
                down, dist = 1, min(10, cur)
            else:
                down = min(4, down + 1)
            ev.append(SimEvent(t, "PLAY", {**clock(t), "possession": poss, "down": down,
                                           "distance": max(1, dist), "yardline_100": cur}))
            if rng.random() < 0.02 and timeouts[other(poss)] > 0:
                timeouts[other(poss)] -= 1
                ev.append(SimEvent(t, "TIMEOUT", {**clock(t), "team": other(poss)}))
        if interrupted == "over":
            break
        if interrupted == "half":
            poss, yl = other(opening), 75
            continue
        if interrupted == "ot":
            if season_type == "REGULAR" and period >= 6:
                break
            if period == 5:  # start of overtime: coin toss
                ot_possessions = set()
                poss, yl = (home if rng.random() < 0.5 else away), 75
                continue
        if outcome in ("TD", "FG"):
            pts = 6 if outcome == "TD" else 3
            score[poss] += pts
            ev.append(SimEvent(t, "SCORE", {**clock(t), "team": poss, "points": pts,
                                            "kind": outcome}))
            if outcome == "TD" and rng.random() < 0.94:
                score[poss] += 1
                ev.append(SimEvent(t, "SCORE", {**clock(t), "team": poss, "points": 1,
                                                "kind": "PAT"}))
            nxt, yl = other(poss), 75
        elif outcome == "TO":
            nxt, yl = other(poss), int(max(10, min(90, 100 - cur)))
            ev.append(SimEvent(t, "TURNOVER", {**clock(t), "gaining_team": nxt,
                                               "yardline_100": yl,
                                               "kind": "INT" if rng.random() < .6 else "FUMBLE"}))
        else:
            nxt, yl = other(poss), int(max(10, min(95, 100 - max(1, cur - 40))))
            ev.append(SimEvent(t, "PLAY", {**clock(t), "possession": nxt, "down": 1,
                                           "distance": 10, "yardline_100": yl}))
        if period >= 5 and len(ot_possessions) >= 2 and score[home] != score[away]:
            break
        poss = nxt
    tie = score[home] == score[away]
    ev.append(SimEvent(t, "GAME_END", {}))
    return ev, {"home_score": score[home], "away_score": score[away], "tie": tie}


def training_frame(n_games: int, seed: int = 3):
    """Rows of (features, labels) built through the real reducer + features."""
    from datetime import UTC, datetime, timedelta

    import pandas as pd

    from sports_edge.domain.enums import SourceStatus, Sport
    from sports_edge.domain.records import Game
    from sports_edge.ingest.nhl_state import NormalizedGameEvent
    from sports_edge.sports.nfl import FEATURE_NAMES, NFLReducer, build_features

    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 9, 7, 17, 0, tzinfo=UTC)
    rows = []
    for g in range(n_games):
        edge = float(rng.normal(0.15, 0.5))
        season = "REGULAR" if rng.random() < 0.85 else "POSTSEASON"
        evs, res = simulate(rng, "H", "A", edge, season)
        game = Game(game_id=f"SIMNFL{g}", sport=Sport.NFL, season="SYN", home_team="H",
                    away_team="A", scheduled_start=t0, status="SCHEDULED", source="sim",
                    details={"season_type": season})
        prior = float(_sig(edge * 1.1))
        red = NFLReducer(game.game_id, "H", "A", season_type=season)
        for i, e in enumerate(evs):
            at = t0 + timedelta(seconds=e.t + i * 0.001)
            red.apply(NormalizedGameEvent(game.game_id, "sim", SourceStatus.SYNTHETIC_DEMO,
                                          e.type, f"e{i}", i + 1, at, at, at, e.data))
            if e.type == "GAME_END" or (i % 3 and e.type == "PLAY"):
                continue
            s = red.state
            for sel, p in (("H", prior), ("A", 1 - prior)):
                fv = build_features(s, game, sel, p)
                won = (res["home_score"] > res["away_score"]) == (sel == "H") and not res["tie"]
                rows.append({**{k: fv.values[k] for k in FEATURE_NAMES}, "y": int(won),
                             "tie": int(res["tie"]), "game_id": g, "sel": sel})
    return pd.DataFrame(rows)


def train_synthetic_nfl(n_games: int = 400, seed: int = 3):
    """Chronological split by game id (train / validation for calibration)."""
    from sports_edge.domain.enums import ModelStatus
    from sports_edge.domain.records import ModelVersion
    from sports_edge.forecast.models import StateModel
    from sports_edge.sports.nfl import FEATURE_NAMES, FEATURE_VERSION, NFLForecaster

    df = training_frame(n_games, seed).fillna(0.0)
    cut = int(n_games * 0.75)
    tr, va = df[df.game_id < cut], df[df.game_id >= cut]
    feats = tuple(FEATURE_NAMES)
    win = StateModel("logistic", feats, {"C": 1.0}, n_bootstrap=6).fit(tr, va, "y")
    tie = None
    if tr["tie"].sum() >= 5:
        tie = StateModel("logistic", feats, {"C": 0.5}, n_bootstrap=0,
                         calibration="none").fit(tr, None, "tie")
    v = ModelVersion(model_version="nfl_logit_syn_v1", kind="logistic+tie",
                     status=ModelStatus.SYNTHETIC_ONLY, feature_version=FEATURE_VERSION,
                     uses_market_inputs=False,
                     trained_on=f"{n_games} SYNTHETIC simulated games (nfl_sim)",
                     train_range=("0", str(cut - 1)), validation_range=(str(cut), str(n_games)),
                     hyperparameters={"C": 1.0}, calibration="sigmoid")
    return NFLForecaster(win, tie, v)
