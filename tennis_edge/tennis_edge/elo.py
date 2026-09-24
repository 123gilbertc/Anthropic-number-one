"""Surface-aware Elo for tennis.

Why Elo? In published comparisons (Kovalchik 2016, "Searching for the GOAT of
tennis win prediction") a well-tuned Elo beat every ranking-, regression- and
point-based model at predicting ATP match winners. It's the strongest simple
baseline there is, and bookmakers still beat it, so treat it as a filter.

Details:
  * K factor shrinks with experience: K = k_base / (matches + offset) ** shape
    (the FiveThirtyEight tennis formula).
  * Separate overall + per-surface ratings, blended at prediction time.
  * Best-of-5 matches: Elo is fit mostly on best-of-3, so we back out an
    implied per-set win probability and re-compound it over 5 sets.
  * Players who've been out for a while get pulled back toward the mean
    (injury/rust).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .data import SURFACES, Match
from .names import player_key

START = 1500.0


@dataclass
class EloParams:
    k_base: float = 250.0
    k_offset: float = 5.0
    k_shape: float = 0.4
    surface_weight: float = 0.5   # 0 = ignore surface, 1 = surface only
    inactivity_days: int = 120    # start decaying toward mean after this gap
    inactivity_decay: float = 0.15  # fraction of distance-to-START lost per inactive year
    retirement_update: bool = False  # update ratings on RET/W.O. results?


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def bo3_to_set_prob(p3: float) -> float:
    """Invert P(win best-of-3) = s^2 (3 - 2s) for the per-set probability s."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid * mid * (3 - 2 * mid) < p3:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def set_prob_to_bo5(s: float) -> float:
    q = 1 - s
    return s ** 3 * (1 + 3 * q + 6 * q * q)


def bo3_to_bo5(p3: float) -> float:
    return set_prob_to_bo5(bo3_to_set_prob(p3))


class Elo:
    def __init__(self, params: Optional[EloParams] = None):
        self.p = params or EloParams()
        self.rating: dict[str, float] = defaultdict(lambda: START)
        self.surf: dict[str, dict[str, float]] = {s: defaultdict(lambda: START) for s in SURFACES}
        self.n: dict[str, int] = defaultdict(int)
        self.n_surf: dict[str, dict[str, int]] = {s: defaultdict(int) for s in SURFACES}
        self.last: dict[str, date] = {}
        self.names: dict[str, str] = {}

    # ---------- prediction ----------
    def _k(self, n: int) -> float:
        return self.p.k_base / (n + self.p.k_offset) ** self.p.k_shape

    def _decayed(self, key: str, rating: float, on: Optional[date]) -> float:
        last = self.last.get(key)
        if on is None or last is None:
            return rating
        gap = (on - last).days
        if gap <= self.p.inactivity_days:
            return rating
        years = (gap - self.p.inactivity_days) / 365.0
        keep = (1 - self.p.inactivity_decay) ** years
        return START + (rating - START) * keep

    def blended(self, key: str, surface: str, on: Optional[date] = None) -> float:
        w = self.p.surface_weight
        r = (1 - w) * self.rating[key] + w * self.surf[surface][key]
        return self._decayed(key, r, on)

    def prob_keys(self, ka: str, kb: str, surface: str = "hard", best_of: int = 3,
                  on: Optional[date] = None) -> float:
        """P(ka beats kb) where both are already player keys."""
        p = expected(self.blended(ka, surface, on), self.blended(kb, surface, on))
        return bo3_to_bo5(p) if best_of == 5 else p

    def prob(self, a: str, b: str, surface: str = "hard", best_of: int = 3,
             on: Optional[date] = None) -> float:
        """P(player a beats player b), given display names from any source."""
        return self.prob_keys(player_key(a), player_key(b), surface, best_of, on)

    def experience(self, name: str) -> int:
        return self.n.get(player_key(name), 0)

    def known(self, name: str) -> bool:
        return self.experience(name) > 0

    # ---------- training ----------
    def update(self, m: Match) -> None:
        if not m.completed and not self.p.retirement_update:
            return
        w, l, s = m.wkey, m.lkey, m.surface
        for k in (w, l):  # apply inactivity decay permanently before update
            if k in self.last:
                self.rating[k] = self._decayed(k, self.rating[k], m.date)
                self.surf[s][k] = self._decayed(k, self.surf[s][k], m.date)
        e = expected(self.rating[w], self.rating[l])
        es = expected(self.surf[s][w], self.surf[s][l])
        self.rating[w] += self._k(self.n[w]) * (1 - e)
        self.rating[l] -= self._k(self.n[l]) * (1 - e)
        self.surf[s][w] += self._k(self.n_surf[s][w]) * (1 - es)
        self.surf[s][l] -= self._k(self.n_surf[s][l]) * (1 - es)
        for k, name in ((w, m.winner), (l, m.loser)):
            self.n[k] += 1
            self.n_surf[s][k] += 1
            self.last[k] = m.date
            self.names[k] = name

    def fit(self, matches: Iterable[Match]) -> "Elo":
        for m in matches:
            self.update(m)
        return self

    def top(self, n: int = 20, surface: Optional[str] = None, min_matches: int = 20):
        rows = []
        for k, cnt in self.n.items():
            if cnt < min_matches:
                continue
            r = self.surf[surface][k] if surface else self.rating[k]
            rows.append((self.names.get(k, k), round(r, 1), cnt))
        return sorted(rows, key=lambda x: -x[1])[:n]


def log_loss(p: float, y: int) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))
