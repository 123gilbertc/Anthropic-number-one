"""SYNTHETIC NHL game generator.

Purpose: exercise the training / evaluation / replay plumbing end to end
before real historical data is licensed. Nothing trained on this data may
produce value alerts: models fitted here get ``ModelStatus.SYNTHETIC_ONLY``.

The process is a deliberately simple goal-scoring simulation (Poisson goals
whose rate depends on hidden team strength and manpower). It is not a
description of real hockey and its numbers mean nothing about real edges.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from sports_edge.features.nhl import FEATURE_NAMES

BASE_GOALS_PER_SEC = 3.0 / 3600  # per team, roughly 3 goals per 60 minutes


def simulate_season(n_games: int, seed: int = 0, snapshot_every: int = 120,
                    start: datetime = datetime(2024, 10, 1, tzinfo=UTC)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        strength_home = rng.normal(0, 0.15)
        strength_away = rng.normal(0, 0.15)
        home_adv = 0.05
        lam_h = BASE_GOALS_PER_SEC * np.exp(strength_home - strength_away + home_adv)
        lam_a = BASE_GOALS_PER_SEC * np.exp(strength_away - strength_home)
        # a noisy pregame estimate of the true strength gap (the "prior")
        true_gap = (lam_h - lam_a) / BASE_GOALS_PER_SEC
        pregame = float(1 / (1 + np.exp(-(1.1 * true_gap + rng.normal(0, 0.15)))))
        pregame = min(max(pregame, 0.05), 0.95)
        hs = as_ = 0
        pp_h = pp_a = 0  # seconds of power play remaining
        snaps = []
        for t in range(3600):
            if t % snapshot_every == 0:
                manpower = (1 if pp_h > 0 else 0) - (1 if pp_a > 0 else 0)
                snaps.append((t, hs, as_, manpower))
            mh = 1.8 if pp_h > 0 else 0.6 if pp_a > 0 else 1.0
            ma = 1.8 if pp_a > 0 else 0.6 if pp_h > 0 else 1.0
            if rng.random() < lam_h * mh:
                hs += 1
                pp_h = 0
            if rng.random() < lam_a * ma:
                as_ += 1
                pp_a = 0
            pp_h, pp_a = max(0, pp_h - 1), max(0, pp_a - 1)
            if pp_h == pp_a == 0 and rng.random() < 1 / 450:
                if rng.random() < 0.5:
                    pp_h = 120
                else:
                    pp_a = 120
        if hs == as_:
            home_wins = rng.random() < lam_h / (lam_h + lam_a)
        else:
            home_wins = hs > as_
        game_start = start + timedelta(days=g // 6, hours=g % 6)
        for t, h, a, mp in snaps:
            secs = 3600 - t
            for is_home in (1, 0):
                diff = (h - a) if is_home else (a - h)
                frac = secs / 3600
                p = pregame if is_home else 1 - pregame
                rows.append({
                    "game_id": f"SYN{g:05d}",
                    "game_start": game_start,
                    "is_home": float(is_home),
                    "score_diff": float(diff),
                    "seconds_remaining_reg": float(secs),
                    "frac_remaining": frac,
                    "score_diff_x_time": diff / np.sqrt(frac + 0.02),
                    "in_overtime": 0.0,
                    "manpower_diff": float(mp if is_home else -mp),
                    "own_net_empty": 0.0,
                    "opp_net_empty": 0.0,
                    "pregame_logit": float(np.log(p / (1 - p))),
                    "y": int(home_wins if is_home else not home_wins),
                })
    df = pd.DataFrame(rows)
    assert set(FEATURE_NAMES) <= set(df.columns)
    return df
