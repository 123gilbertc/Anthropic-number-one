"""NHL feature construction (feature version ``nhl_v1``).

Features are computed from the *selection team's* perspective so one model
serves both sides. Unavailable features are ``None`` and listed in
``missing``; they are never filled with invented values.

``CRITICAL`` features: without them the model abstains.
"""

from __future__ import annotations

import math

from sports_edge.domain.records import FeatureVector, NHLState, stable_id
from sports_edge.ingest.nhl_state import seconds_remaining_regulation

FEATURE_VERSION = "nhl_v1"
FEATURE_NAMES = (
    "score_diff",
    "seconds_remaining_reg",
    "frac_remaining",
    "score_diff_x_time",
    "is_home",
    "in_overtime",
    "manpower_diff",
    "own_net_empty",
    "opp_net_empty",
    "pregame_logit",
)
CRITICAL = ("score_diff", "seconds_remaining_reg", "frac_remaining")
REG_SECONDS = 3 * 20 * 60


def build_features(
    state: NHLState,
    selection_is_home: bool,
    pregame_prob: float | None,
) -> FeatureVector:
    """``pregame_prob``: pregame win probability for the selection (a prior), or None."""
    diff = state.home_score - state.away_score
    if not selection_is_home:
        diff = -diff
    secs = seconds_remaining_regulation(state)
    frac = None if secs is None else secs / REG_SECONDS
    own_sk, opp_sk = (
        (state.home_skaters, state.away_skaters)
        if selection_is_home
        else (state.away_skaters, state.home_skaters)
    )
    own_en, opp_en = (
        (state.home_net_empty, state.away_net_empty)
        if selection_is_home
        else (state.away_net_empty, state.home_net_empty)
    )
    vals: dict[str, float | None] = {
        "score_diff": float(diff),
        "seconds_remaining_reg": None if secs is None else float(secs),
        "frac_remaining": frac,
        # score lead matters more as time runs out
        "score_diff_x_time": None if frac is None else diff / math.sqrt(frac + 0.02),
        "is_home": 1.0 if selection_is_home else 0.0,
        "in_overtime": 1.0 if state.period >= 4 else 0.0,
        "manpower_diff": None if own_sk is None or opp_sk is None else float(own_sk - opp_sk),
        "own_net_empty": None if own_en is None else float(own_en),
        "opp_net_empty": None if opp_en is None else float(opp_en),
        "pregame_logit": None
        if pregame_prob is None
        else math.log(pregame_prob / (1 - pregame_prob)),
    }
    missing = tuple(k for k, v in vals.items() if v is None)
    return FeatureVector(
        feature_id=stable_id("feat", [state.snapshot_id, selection_is_home, pregame_prob]),
        snapshot_id=state.snapshot_id,
        feature_version=FEATURE_VERSION,
        values=vals,
        missing=missing,
    )


def critical_missing(fv: FeatureVector) -> tuple[str, ...]:
    return tuple(k for k in CRITICAL if fv.values.get(k) is None)
