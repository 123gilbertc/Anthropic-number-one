"""MLB normalized game state and features (historical/replay follow-on).

Same policies as NHL: duplicates ignored; corrections, gaps and late events
flag PENDING_RECONCILIATION until a full SNAPSHOT; unknown values stay None.

Events: SNAPSHOT, PITCH (count update), PLAY (outs/runners/runs after a play),
PITCHER_CHANGE, HALF_END, GAME_END, POSTPONED.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from sports_edge.domain.records import FeatureVector, GameStateBase, stable_id
from sports_edge.ingest.nhl_state import InvalidEvent, NormalizedGameEvent
from sports_edge.sports.base import BaseReducer


class MLBState(GameStateBase):
    inning: int
    half: Literal["TOP", "BOTTOM"]
    outs: int
    runners: tuple[bool, bool, bool]  # 1B, 2B, 3B occupied
    home_runs: int
    away_runs: int
    balls: int | None
    strikes: int | None
    home_pitcher: str | None
    away_pitcher: str | None
    home_pitcher_pitches: int | None
    away_pitcher_pitches: int | None
    lineup_confirmed: bool | None
    home_starter: str | None = None  # first pitcher seen: listed-pitcher settlement
    away_starter: str | None = None
    postponed: bool = False

    def coherent(self) -> bool:
        if not 0 <= self.outs <= 3 or self.inning < 1 or self.home_runs < 0 \
                or self.away_runs < 0:
            return False
        if self.balls is not None and not 0 <= self.balls <= 4:
            return False
        if self.strikes is not None and not 0 <= self.strikes <= 3:
            return False
        return True

    def material_key(self) -> tuple:
        return (self.inning, self.half, self.outs, self.runners, self.home_runs,
                self.away_runs, self.home_pitcher, self.away_pitcher, self.is_final,
                self.postponed)


MATERIAL = frozenset({"PLAY", "PITCHER_CHANGE", "HALF_END", "SNAPSHOT", "GAME_END",
                      "POSTPONED"})


@dataclass
class MLBStateReducer(BaseReducer):
    state_cls: ClassVar = MLBState
    prefix: ClassVar[str] = "mlb"
    material_types: ClassVar[frozenset[str]] = MATERIAL

    def initial_fields(self) -> dict[str, Any]:
        return dict(inning=1, half="TOP", outs=0, runners=(False, False, False), home_runs=0,
                    away_runs=0, balls=None, strikes=None, home_pitcher=None,
                    away_pitcher=None, home_pitcher_pitches=None, away_pitcher_pitches=None,
                    lineup_confirmed=None)

    def reduce(self, u: dict[str, Any], ev: NormalizedGameEvent) -> None:
        d = ev.data
        t = ev.type
        flags = set(u["pending_reconciliation"])
        if t == "SNAPSHOT":
            for k in ("inning", "half", "outs", "home_runs", "away_runs", "balls", "strikes",
                      "home_pitcher", "away_pitcher", "home_pitcher_pitches",
                      "away_pitcher_pitches", "lineup_confirmed"):
                if k in d:
                    u[k] = d[k]
            if "runners" in d:
                u["runners"] = tuple(bool(x) for x in d["runners"])
        elif t == "PITCH":
            u["balls"], u["strikes"] = d.get("balls"), d.get("strikes")
            key = "home_pitcher_pitches" if u["half"] == "TOP" else "away_pitcher_pitches"
            if u[key] is not None:
                u[key] += 1
        elif t == "PLAY":
            u["outs"] = d["outs"]
            u["runners"] = tuple(bool(x) for x in d["runners"])
            runs = int(d.get("runs", 0))
            batting = self.away if u["half"] == "TOP" else self.home
            u["away_runs" if u["half"] == "TOP" else "home_runs"] += runs
            u["balls"] = u["strikes"] = 0 if d.get("pa_complete") else u["balls"]
            if runs:
                self.last_labels.append(
                    f"{d.get('desc', 'Run' if runs == 1 else 'Runs')}: {batting} +{runs}")
        elif t == "PITCHER_CHANGE":
            side = "home" if d["team"] == self.home else "away"
            u[f"{side}_pitcher"] = d.get("pitcher")
            u[f"{side}_pitcher_pitches"] = 0 if d.get("pitcher") else None
            flag = f"PITCHER_UNKNOWN_{d['team']}"
            flags.discard(flag)
            if d.get("pitcher") is None:
                flags.add(flag)
            self.last_labels.append(f"Pitching change {d['team']}: "
                                    f"{d.get('pitcher') or 'UNKNOWN'}")
        elif t == "HALF_END":
            u["outs"], u["runners"] = 0, (False, False, False)
            if u["half"] == "TOP":
                u["half"] = "BOTTOM"
            else:
                u["half"], u["inning"] = "TOP", u["inning"] + 1
        elif t == "GAME_END":
            u["is_final"] = True
        elif t == "POSTPONED":
            u["postponed"] = True
        else:
            raise InvalidEvent(f"unknown MLB event {t}")
        if t == "SNAPSHOT":
            flags = set()
        if not (0 <= u["outs"] <= 3) or u["inning"] < 1:
            raise InvalidEvent("invalid outs/inning")
        for side in ("home", "away"):
            if u[f"{side}_starter"] is None and u[f"{side}_pitcher"] is not None:
                u[f"{side}_starter"] = u[f"{side}_pitcher"]
        u["pending_reconciliation"] = tuple(sorted(flags))


MLB_FEATURE_VERSION = "mlb_v1"
MLB_CRITICAL = ("run_diff", "innings_remaining_frac", "outs")


def build_mlb_features(s: MLBState, selection_is_home: bool,
                       pregame_prob: float | None) -> FeatureVector:
    diff = s.home_runs - s.away_runs
    if not selection_is_home:
        diff = -diff
    # half-innings left for the game as a fraction of 18 (extra innings -> small floor)
    halves_done = (s.inning - 1) * 2 + (1 if s.half == "BOTTOM" else 0) + s.outs / 3
    frac = max(0.0, (18 - halves_done) / 18)
    batting_home = s.half == "BOTTOM"
    base_code = sum(int(b) << i for i, b in enumerate(s.runners))
    vals: dict[str, float | None] = {
        "run_diff": float(diff),
        "innings_remaining_frac": frac,
        "run_diff_x_time": diff / math.sqrt(frac + 0.03),
        "outs": float(s.outs),
        "base_state": float(base_code),
        "selection_batting": 1.0 if batting_home == selection_is_home else 0.0,
        "is_home": 1.0 if selection_is_home else 0.0,
        "extra_innings": 1.0 if s.inning > 9 else 0.0,
        "opp_pitcher_pitches": (float(s.home_pitcher_pitches) if not selection_is_home
                                else float(s.away_pitcher_pitches))
        if (s.home_pitcher_pitches if not selection_is_home else s.away_pitcher_pitches)
        is not None else None,
        "pregame_logit": None if pregame_prob is None
        else math.log(pregame_prob / (1 - pregame_prob)),
    }
    return FeatureVector(
        feature_id=stable_id("feat", [s.snapshot_id, selection_is_home, pregame_prob]),
        snapshot_id=s.snapshot_id, feature_version=MLB_FEATURE_VERSION, values=vals,
        missing=tuple(k for k, v in vals.items() if v is None))
