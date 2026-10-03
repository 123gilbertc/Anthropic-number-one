"""Tennis scoring rules: point -> game -> set -> match, including server rotation.

Supported formats (``final_set``):
* ``TB7``  final set has a 7-point tiebreak at 6-6;
* ``TB10`` final set has a 10-point tiebreak at 6-6;
* ``ADV``  final set has no tiebreak (play on until a two-game lead);
* ``MTB10`` the final set is replaced by a match tiebreak to 10 (common in doubles).

Non-final sets use a 7-point tiebreak at ``tiebreak_at``-all (6-6 by default).
``no_ad``: at deuce the next point wins the game.

Server rotation: alternates each game. In a tiebreak the first server serves one
point, then players alternate every two points. The player who served first in a
tiebreak receives in the first game of the next set.

Which format a given tournament uses is a *provider fact* and must come from the
schedule/match data, never be assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

P1, P2 = "P1", "P2"


def other(p: str) -> str:
    return P2 if p == P1 else P1


@dataclass(frozen=True)
class Format:
    best_of: int  # 3 or 5
    final_set: str  # TB7 / TB10 / ADV / MTB10
    no_ad: bool = False
    tiebreak_at: int = 6
    doubles: bool = False

    def __post_init__(self) -> None:
        if self.best_of not in (1, 3, 5):
            raise ValueError("best_of must be 1, 3 or 5")
        if self.final_set not in ("TB7", "TB10", "ADV", "MTB10"):
            raise ValueError(f"unknown final-set rule {self.final_set}")

    @property
    def sets_to_win(self) -> int:
        return self.best_of // 2 + 1


@dataclass(frozen=True)
class Score:
    sets: tuple[tuple[int, int], ...] = ()  # completed sets (P1 games, P2 games)
    games: tuple[int, int] = (0, 0)
    points: tuple[int, int] = (0, 0)  # points in the current game or tiebreak
    in_tiebreak: bool = False
    tiebreak_target: int | None = None
    server: str = P1
    tiebreak_first_server: str | None = None
    winner: str | None = None

    @property
    def sets_won(self) -> tuple[int, int]:
        a = sum(1 for g1, g2 in self.sets if g1 > g2)
        return a, len(self.sets) - a


def is_final_set(fmt: Format, sc: Score) -> bool:
    return len(sc.sets) == fmt.best_of - 1


def tb_server(first: str, k: int) -> str:
    """Server of tiebreak point k (0-based)."""
    return first if ((k + 1) // 2) % 2 == 0 else other(first)


def _game_won(a: int, b: int, no_ad: bool) -> bool:
    if no_ad and a == 4 and b == 3:
        return True
    return a >= 4 and a - b >= 2


def _start_tiebreak(fmt: Format, sc: Score, target: int) -> Score:
    return replace(sc, in_tiebreak=True, tiebreak_target=target, points=(0, 0),
                   tiebreak_first_server=sc.server)


def _maybe_tiebreak(fmt: Format, sc: Score) -> Score:
    g1, g2 = sc.games
    if g1 == g2 == fmt.tiebreak_at:
        if is_final_set(fmt, sc):
            if fmt.final_set == "TB7":
                return _start_tiebreak(fmt, sc, 7)
            if fmt.final_set == "TB10":
                return _start_tiebreak(fmt, sc, 10)
            return sc  # ADV: play on
        return _start_tiebreak(fmt, sc, 7)
    return sc


def _set_won(fmt: Format, sc: Score, g1: int, g2: int) -> bool:
    hi, lo = max(g1, g2), min(g1, g2)
    if hi >= fmt.tiebreak_at and hi - lo >= 2:
        return True
    return False


def start_set(fmt: Format, sc: Score) -> Score:
    """At the start of a set: MTB10 replaces the final set with a match tiebreak."""
    if fmt.final_set == "MTB10" and is_final_set(fmt, sc) and sc.games == (0, 0) \
            and not sc.in_tiebreak:
        return _start_tiebreak(fmt, sc, 10)
    return sc


def _close_set(fmt: Format, sc: Score, set_games: tuple[int, int], next_server: str) -> Score:
    sets = sc.sets + (set_games,)
    s1 = sum(1 for a, b in sets if a > b)
    s2 = len(sets) - s1
    winner = P1 if s1 == fmt.sets_to_win else P2 if s2 == fmt.sets_to_win else None
    nxt = Score(sets=sets, games=(0, 0), points=(0, 0), server=next_server, winner=winner)
    return nxt if winner else start_set(fmt, nxt)


@dataclass
class PointResult:
    score: Score
    game_won_by: str | None = None
    break_of_serve: bool = False
    set_won_by: str | None = None
    match_won_by: str | None = None
    labels: list[str] = field(default_factory=list)


def play_point(fmt: Format, sc: Score, point_winner: str) -> PointResult:
    if sc.winner is not None:
        raise ValueError("match already decided")
    if point_winner not in (P1, P2):
        raise ValueError(f"bad point winner {point_winner!r}")
    a, b = sc.points
    a, b = (a + 1, b) if point_winner == P1 else (a, b + 1)
    if sc.in_tiebreak:
        target = sc.tiebreak_target or 7
        first = sc.tiebreak_first_server or sc.server
        hi, lo = max(a, b), min(a, b)
        if hi >= target and hi - lo >= 2:
            w = P1 if a > b else P2
            g1, g2 = sc.games
            is_match_tb = fmt.final_set == "MTB10" and is_final_set(fmt, sc) \
                and sc.games == (0, 0)
            set_games = (1, 0) if is_match_tb and w == P1 else (0, 1) if is_match_tb \
                else (g1 + 1, g2) if w == P1 else (g1, g2 + 1)
            nxt = _close_set(fmt, sc, set_games, other(first))
            r = PointResult(nxt, game_won_by=w, set_won_by=w, match_won_by=nxt.winner)
            r.labels.append(f"{'Match tiebreak' if is_match_tb else 'Tiebreak'} won by {w} "
                            f"{max(a, b)}-{min(a, b)}")
            return r
        server = tb_server(first, a + b)
        return PointResult(replace(sc, points=(a, b), server=server))
    if not _game_won(a, b, fmt.no_ad) and not _game_won(b, a, fmt.no_ad):
        return PointResult(replace(sc, points=(a, b)))
    w = P1 if a > b else P2
    brk = w != sc.server
    g1, g2 = sc.games
    g1, g2 = (g1 + 1, g2) if w == P1 else (g1, g2 + 1)
    nxt_server = other(sc.server)
    r = PointResult(sc, game_won_by=w, break_of_serve=brk)
    if brk:
        r.labels.append(f"Break of serve: {w} broke")
    if _set_won(fmt, sc, g1, g2) and not (is_final_set(fmt, sc) and fmt.final_set == "ADV"
                                         and abs(g1 - g2) < 2):
        nxt = _close_set(fmt, sc, (g1, g2), nxt_server)
        r.score, r.set_won_by, r.match_won_by = nxt, w, nxt.winner
        r.labels.append(f"Set won by {w} {g1}-{g2}")
        return r
    nxt = replace(sc, games=(g1, g2), points=(0, 0), server=nxt_server)
    r.score = _maybe_tiebreak(fmt, nxt)
    return r


def valid_score(fmt: Format, sc: Score) -> bool:
    """Coherence check used to reject impossible provider snapshots."""
    if len(sc.sets) > fmt.best_of:
        return False
    s1, s2 = sc.sets_won
    if max(s1, s2) > fmt.sets_to_win:
        return False
    if sc.winner is None and max(s1, s2) == fmt.sets_to_win:
        return False
    g1, g2 = sc.games
    if g1 < 0 or g2 < 0 or sc.points[0] < 0 or sc.points[1] < 0:
        return False
    if not sc.in_tiebreak and not (is_final_set(fmt, sc) and fmt.final_set == "ADV"):
        if max(g1, g2) > fmt.tiebreak_at + 1 or (max(g1, g2) == fmt.tiebreak_at + 1
                                                 and min(g1, g2) < fmt.tiebreak_at - 1):
            return False
    return sc.server in (P1, P2)
