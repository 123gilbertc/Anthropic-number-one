"""Tennis match-win probability: an exact point -> game -> set -> match Markov model.

Inputs are the two serve-point probabilities: ``s1`` = P(P1 wins a point on P1's
serve) and ``s2`` = P(P2 wins a point on P2's serve). Given those, the model is
pure rules arithmetic (tested against closed forms in ``tests/test_sports_rules.py``).
Points are assumed independent and identically distributed per server - the
standard baseline assumption, known to be imperfect (momentum, fatigue, pressure
points are not modelled).

Strength: serve/return point rates per player and surface, shrunk toward the
surface average with ``k`` pseudo-points (small samples get pulled to the mean):

    s1 = avg_serve + (serve1* - avg_serve) - (return2* - avg_return)

The averages and player rates are *data*. Without them the model abstains;
it never invents a player's strength. Uncertainty: Beta posterior draws of each
rate, propagated through the exact model (10th/90th percentile band).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache

import numpy as np

from sports_edge.sports.tennis_rules import P1, P2, Format, Score, is_final_set, other, tb_server

# ------------------------------------------------------------------ games and tiebreaks


@lru_cache(maxsize=200_000)
def p_game(s: float, a: int = 0, b: int = 0, no_ad: bool = False) -> float:
    """P(server wins the game) from server points a, receiver points b."""
    if no_ad and a == 3 and b == 3:
        return s
    if a >= 4 and a - b >= 2:
        return 1.0
    if b >= 4 and b - a >= 2:
        return 0.0
    if a >= 3 and b >= 3:
        if a == b:  # deuce
            return s * s / (s * s + (1 - s) * (1 - s))
        if a > b:  # advantage server
            return s + (1 - s) * p_game(s, 3, 3, no_ad)
        return s * p_game(s, 3, 3, no_ad)
    return s * p_game(s, a + 1, b, no_ad) + (1 - s) * p_game(s, a, b + 1, no_ad)


@lru_cache(maxsize=200_000)
def p_tiebreak(s1: float, s2: float, a: int, b: int, first: str, target: int) -> float:
    """P(P1 wins the tiebreak) from P1 points a, P2 points b."""
    if a >= target and a - b >= 2:
        return 1.0
    if b >= target and b - a >= 2:
        return 0.0
    if a == b and a >= target - 1:
        # The next two points are served one each (see tb_server), so the state
        # repeats every two points: P1 wins from here with p_both / (p_both + q_both).
        win2 = s1 * (1 - s2)
        lose2 = (1 - s1) * s2
        return win2 / (win2 + lose2) if win2 + lose2 > 0 else 0.5
    srv = tb_server(first, a + b)
    p = s1 if srv == P1 else 1 - s2  # P(P1 wins this point)
    return p * p_tiebreak(s1, s2, a + 1, b, first, target) + \
        (1 - p) * p_tiebreak(s1, s2, a, b + 1, first, target)


# ------------------------------------------------------------------ sets

# A set result: {(winner, first server of the next set): probability}
SetDist = dict[tuple[str, str], float]


def _add(d: SetDist, k: tuple[str, str], v: float) -> None:
    if v:
        d[k] = d.get(k, 0.0) + v


def _scale(d: SetDist, f: float) -> SetDist:
    return {k: v * f for k, v in d.items()}


def _merge(*ds: SetDist) -> SetDist:
    out: SetDist = {}
    for d in ds:
        for k, v in d.items():
            _add(out, k, v)
    return out


def _tiebreak_set(s1: float, s2: float, a: int, b: int, first: str, target: int) -> SetDist:
    p = p_tiebreak(s1, s2, a, b, first, target)
    nxt = other(first)  # the first tiebreak server receives first in the next set
    return {(P1, nxt): p, (P2, nxt): 1 - p}


@lru_cache(maxsize=200_000)
def _set_from_game_start(s1: float, s2: float, g1: int, g2: int, server: str, mode: str,
                         no_ad: bool, tb_at: int) -> tuple[tuple[tuple[str, str], float], ...]:
    """mode: 'TB7' / 'TB10' / 'ADV' (behaviour at tb_at-all)."""
    hi, lo = max(g1, g2), min(g1, g2)
    if hi >= tb_at and hi - lo >= 2:
        return (((P1 if g1 > g2 else P2, server), 1.0),)
    if g1 == g2 == tb_at and mode in ("TB7", "TB10"):
        return tuple(_tiebreak_set(s1, s2, 0, 0, server, 7 if mode == "TB7" else 10).items())
    if mode == "ADV" and g1 == g2 and g1 >= tb_at - 1:
        h1, h2 = p_game(s1, no_ad=no_ad), p_game(s2, no_ad=no_ad)
        # two games: server, then the other; both to one player ends the set
        if server == P1:
            w, lose = h1 * (1 - h2), (1 - h1) * h2
        else:
            w, lose = (1 - h2) * h1, h2 * (1 - h1)
        tot = w + lose
        pw = w / tot if tot else 0.5
        return (((P1, server), pw), ((P2, server), 1 - pw))
    hold = p_game(s1 if server == P1 else s2, no_ad=no_ad)
    p1_wins_game = hold if server == P1 else 1 - hold
    nxt = other(server)
    win = dict(_set_from_game_start(s1, s2, g1 + 1, g2, nxt, mode, no_ad, tb_at))
    lose = dict(_set_from_game_start(s1, s2, g1, g2 + 1, nxt, mode, no_ad, tb_at))
    return tuple(_merge(_scale(win, p1_wins_game), _scale(lose, 1 - p1_wins_game)).items())


def set_dist(fmt: Format, sc: Score, s1: float, s2: float) -> SetDist:
    """Outcome distribution of the *current* set from a mid-set score."""
    final = is_final_set(fmt, sc)
    mode = (fmt.final_set if final else "TB7")
    if sc.in_tiebreak:
        return _tiebreak_set(s1, s2, sc.points[0], sc.points[1],
                             sc.tiebreak_first_server or sc.server, sc.tiebreak_target or 7)
    if mode == "MTB10":  # a match tiebreak not yet started
        return _tiebreak_set(s1, s2, 0, 0, sc.server, 10)
    g1, g2 = sc.games
    a, b = sc.points
    srv = sc.server
    hold = p_game(s1 if srv == P1 else s2, a if srv == P1 else b, b if srv == P1 else a,
                  fmt.no_ad)
    p1_wins_game = hold if srv == P1 else 1 - hold
    nxt = other(srv)
    win = dict(_set_from_game_start(s1, s2, g1 + 1, g2, nxt, mode, fmt.no_ad, fmt.tiebreak_at))
    lose = dict(_set_from_game_start(s1, s2, g1, g2 + 1, nxt, mode, fmt.no_ad, fmt.tiebreak_at))
    return _merge(_scale(win, p1_wins_game), _scale(lose, 1 - p1_wins_game))


def _fresh_set(fmt: Format, sets_done: int, server: str, s1: float, s2: float) -> SetDist:
    final = sets_done == fmt.best_of - 1
    mode = fmt.final_set if final else "TB7"
    if mode == "MTB10":
        return _tiebreak_set(s1, s2, 0, 0, server, 10)
    return dict(_set_from_game_start(s1, s2, 0, 0, server, mode, fmt.no_ad, fmt.tiebreak_at))


def p_match(fmt: Format, sc: Score, s1: float, s2: float) -> float:
    """P(P1 wins the match) from score ``sc``."""
    if sc.winner is not None:
        return 1.0 if sc.winner == P1 else 0.0
    need = fmt.sets_to_win
    w1, w2 = sc.sets_won

    @cache
    def from_set_start(a: int, b: int, server: str) -> float:
        if a == need:
            return 1.0
        if b == need:
            return 0.0
        tot = 0.0
        for (w, nxt), p in _fresh_set(fmt, a + b, server, s1, s2).items():
            tot += p * from_set_start(a + (w == P1), b + (w == P2), nxt)
        return tot

    tot = 0.0
    for (w, nxt), p in set_dist(fmt, sc, s1, s2).items():
        tot += p * from_set_start(w1 + (w == P1), w2 + (w == P2), nxt)
    return tot


# ------------------------------------------------------------------ strength


@dataclass(frozen=True)
class PlayerRates:
    """Serve/return points won on a surface, with the number of points observed."""

    serve_won: float
    serve_n: int
    return_won: float
    return_n: int


@dataclass(frozen=True)
class SurfaceAverages:
    serve_won: float  # tour average serve points won on this surface
    shrink_points: int = 400  # pseudo-points pulling small samples to the average


def _shrink(rate: float, n: int, avg: float, k: int) -> tuple[float, float]:
    """Posterior mean and the Beta parameters' total (for sampling)."""
    a = rate * n + avg * k
    return a / (n + k), float(n + k)


def serve_probabilities(r1: PlayerRates, r2: PlayerRates, avg: SurfaceAverages,
                        ) -> tuple[float, float]:
    ret_avg = 1 - avg.serve_won
    f1, _ = _shrink(r1.serve_won, r1.serve_n, avg.serve_won, avg.shrink_points)
    f2, _ = _shrink(r2.serve_won, r2.serve_n, avg.serve_won, avg.shrink_points)
    g1, _ = _shrink(r1.return_won, r1.return_n, ret_avg, avg.shrink_points)
    g2, _ = _shrink(r2.return_won, r2.return_n, ret_avg, avg.shrink_points)
    s1 = avg.serve_won + (f1 - avg.serve_won) - (g2 - ret_avg)
    s2 = avg.serve_won + (f2 - avg.serve_won) - (g1 - ret_avg)
    return float(np.clip(s1, 0.30, 0.95)), float(np.clip(s2, 0.30, 0.95))


def band(fmt: Format, sc: Score, r1: PlayerRates, r2: PlayerRates, avg: SurfaceAverages,
         draws: int = 60, seed: int = 11) -> tuple[float, float, float]:
    """(point, low, high): point from posterior means; band from posterior draws."""
    s1, s2 = serve_probabilities(r1, r2, avg)
    point = p_match(fmt, sc, round(s1, 4), round(s2, 4))
    rng = np.random.default_rng(seed)
    ret_avg = 1 - avg.serve_won
    k = avg.shrink_points

    def draw(rate, n, prior):
        m, tot = _shrink(rate, n, prior, k)
        return float(rng.beta(m * tot, (1 - m) * tot))

    sims = []
    for _ in range(draws):
        q1 = PlayerRates(draw(r1.serve_won, r1.serve_n, avg.serve_won), 10**6,
                         draw(r1.return_won, r1.return_n, ret_avg), 10**6)
        q2 = PlayerRates(draw(r2.serve_won, r2.serve_n, avg.serve_won), 10**6,
                         draw(r2.return_won, r2.return_n, ret_avg), 10**6)
        a1, a2 = serve_probabilities(q1, q2, SurfaceAverages(avg.serve_won, 0))
        sims.append(p_match(fmt, sc, round(a1, 3), round(a2, 3)))
    lo, hi = np.quantile(sims, [0.1, 0.9])
    return point, float(min(lo, point)), float(max(hi, point))
