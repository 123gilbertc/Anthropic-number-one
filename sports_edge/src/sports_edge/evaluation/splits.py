"""Chronological, whole-game splits with leakage guards, plus an experiment log.

Every snapshot of a game lands in exactly one partition. Partitions are
ordered in time: train < validation < test < holdout. The final holdout is
only read by ``touch_holdout``, which records each access in the
experiment log so repeated peeking is visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


class LeakageError(AssertionError):
    pass


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    holdout: pd.DataFrame


def chronological_split(df: pd.DataFrame, fractions=(0.5, 0.2, 0.15, 0.15),
                        time_col: str = "game_start", game_col: str = "game_id") -> Split:
    if abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("fractions must sum to 1")
    games = df.groupby(game_col)[time_col].min().sort_values()
    n = len(games)
    cuts = [0]
    acc = 0.0
    for f in fractions:
        acc += f
        cuts.append(round(acc * n))
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        ids = set(games.index[a:b])
        parts.append(df[df[game_col].isin(ids)])
    split = Split(*parts)
    assert_no_leakage(split, time_col, game_col)
    return split


def assert_no_leakage(split: Split, time_col: str = "game_start",
                      game_col: str = "game_id") -> None:
    parts = [split.train, split.validation, split.test, split.holdout]
    seen: set = set()
    for p in parts:
        ids = set(p[game_col])
        if ids & seen:
            raise LeakageError("a game appears in more than one partition")
        seen |= ids
    for earlier, later in zip(parts[:-1], parts[1:], strict=True):
        if len(earlier) and len(later) and earlier[time_col].max() > later[time_col].min():
            raise LeakageError("partitions overlap in time")


class ExperimentLog:
    """Append-only JSONL log of every evaluation run, including holdout touches."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, **fields) -> None:
        rec = {"time": datetime.now(UTC).isoformat(), "kind": kind, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def holdout_touches(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for line in self.path.read_text().splitlines()
                   if json.loads(line)["kind"] == "HOLDOUT_TOUCH")

    def touch_holdout(self, split: Split, reason: str) -> pd.DataFrame:
        self.log("HOLDOUT_TOUCH", reason=reason, prior_touches=self.holdout_touches())
        return split.holdout
