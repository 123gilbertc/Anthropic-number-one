"""MLB: base-out Markov baseline, forecaster and display.

Baseline model (transparent): each plate appearance ends in one of
OUT / WALK / SINGLE / DOUBLE / TRIPLE / HR with team-specific rates. Runner
advancement is a fixed simple rule set (stated in ``_advance``; no sacrifice
flies, double plays or extra bases taken). Runs to the end of a half-inning are
computed exactly from (outs, bases); the game then rolls forward half-inning by
half-inning, with the walk-off rule (the home team does not bat in the bottom of
the 9th or later once ahead) and extra innings.

Extra innings: regular season starts each extra half-inning with a runner on
second (the "automatic runner"); postseason does not. ASSUMED from general
knowledge, UNVERIFIED until checked against the season's rules. Season type
comes from the schedule (``game.details['season_type']``); unknown -> abstain.

What the baseline does not model (and therefore must not claim): the current
pitcher, bullpen fatigue, park, weather, lineup order, platoon splits. Those are
challenger features that need licensed data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np

from sports_edge.domain.enums import ModelStatus, SettlementRule, Sport
from sports_edge.domain.records import Game, Prediction, stable_id
from sports_edge.ingest.mlb_state import MLBState, MLBStateReducer
from sports_edge.sports.base import FinalOutcome, register

CAP = 15  # runs per half-inning are truncated here (mass accumulates at CAP)
OUTCOMES = ("out", "walk", "single", "double", "triple", "hr")


@dataclass(frozen=True)
class PARates:
    out: float
    walk: float
    single: float
    double: float
    triple: float
    hr: float

    def __post_init__(self) -> None:
        tot = sum(self.as_tuple())
        if abs(tot - 1.0) > 1e-6 or min(self.as_tuple()) < 0 or self.out <= 0.3:
            raise ValueError(f"PA rates must be a distribution with out > 0.3 (sum {tot})")

    def as_tuple(self) -> tuple[float, ...]:
        return (self.out, self.walk, self.single, self.double, self.triple, self.hr)


def _bases_code(b: tuple[int, int, int]) -> int:
    return b[0] | (b[1] << 1) | (b[2] << 2)


def _advance(b: tuple[int, int, int], outcome: str) -> tuple[tuple[int, int, int], int]:
    b1, b2, b3 = b
    if outcome == "walk":  # forced advances only
        if b1 and b2 and b3:
            return (1, 1, 1), 1
        if b1 and b2:
            return (1, 1, 1), 0
        if b1:
            return (1, 1, b3), 0
        return (1, b2, b3), 0
    if outcome == "single":  # runners on 2B and 3B score, runner on 1B to 2B
        return (1, b1, 0), b2 + b3
    if outcome == "double":  # runners on 2B/3B score, runner on 1B to 3B
        return (0, 1, b1), b2 + b3
    if outcome == "triple":
        return (0, 0, 1), b1 + b2 + b3
    if outcome == "hr":
        return (0, 0, 0), 1 + b1 + b2 + b3
    raise ValueError(outcome)


@lru_cache(maxsize=50_000)
def half_inning_runs(rates: PARates, outs: int, bases: tuple[int, int, int]) -> tuple[float, ...]:
    """Distribution of runs scored from (outs, bases) to the end of the half-inning."""
    if outs >= 3:
        out = np.zeros(CAP + 1)
        out[0] = 1.0
        return tuple(out)
    p = dict(zip(OUTCOMES, rates.as_tuple(), strict=True))
    # mass[o][bases_code][runs]
    mass = np.zeros((3, 8, CAP + 1))
    mass[outs, _bases_code(bases), 0] = 1.0
    done = np.zeros(CAP + 1)
    for o in range(outs, 3):
        cur = mass[o].copy()
        for _ in range(400):  # non-out PAs keep the out count; iterate until mass drains
            if cur.sum() < 1e-12:
                break
            nxt = np.zeros_like(cur)
            for code in range(8):
                row = cur[code]
                if not row.any():
                    continue
                b = (code & 1, (code >> 1) & 1, (code >> 2) & 1)
                if o + 1 < 3:
                    mass[o + 1, code] += p["out"] * row
                else:
                    done += p["out"] * row
                for oc in OUTCOMES[1:]:
                    nb, r = _advance(b, oc)
                    shifted = np.zeros(CAP + 1)
                    if r:
                        shifted[r:] = row[:CAP + 1 - r]
                        shifted[CAP] += row[CAP + 1 - r:].sum()
                    else:
                        shifted = row
                    nxt[_bases_code(nb)] += p[oc] * shifted
            cur = nxt
    return tuple(done / done.sum())


SPAN = 60  # run differential range tracked: [-SPAN, SPAN]


def _apply(dist: np.ndarray, runs: tuple[float, ...], sign: int) -> np.ndarray:
    out = np.zeros_like(dist)
    for r, pr in enumerate(runs):
        if pr == 0:
            continue
        if sign > 0:
            out[r:] += pr * dist[:len(dist) - r] if r else pr * dist
            if r:
                out[-1] += pr * dist[len(dist) - r:].sum()
        else:
            out[:len(dist) - r] += pr * dist[r:] if r else pr * dist
            if r:
                out[0] += pr * dist[:r].sum()
    return out


def win_probability(s: MLBState, home: PARates, away: PARates, season_type: str) -> float:
    """P(home team wins) from the state."""
    if s.is_final:
        return 1.0 if s.home_runs > s.away_runs else 0.0
    auto_runner = season_type == "REGULAR"
    dist = np.zeros(2 * SPAN + 1)
    dist[SPAN + max(-SPAN, min(SPAN, s.home_runs - s.away_runs))] = 1.0
    won = lost = 0.0

    def start_bases(inning: int) -> tuple[int, int, int]:
        return (0, 1, 0) if inning >= 10 and auto_runner else (0, 0, 0)

    inning, half = s.inning, s.half
    outs, bases = s.outs, tuple(int(x) for x in s.runners)
    first = True
    for _ in range(80):  # half-innings; extras are summed in closed form below
        if inning >= 10 and not first:
            break
        rates = away if half == "TOP" else home
        st_outs, st_bases = (outs, bases) if first else (0, start_bases(inning))
        dist = _apply(dist, half_inning_runs(rates, st_outs, st_bases), -1 if half == "TOP" else 1)
        first = False
        if half == "TOP":
            if inning >= 9:  # home ahead after the top of the 9th+: game over
                won += dist[SPAN + 1:].sum()
                dist[SPAN + 1:] = 0
            half = "BOTTOM"
        else:
            if inning >= 9:
                won += dist[SPAN + 1:].sum()
                lost += dist[:SPAN].sum()
                dist[SPAN + 1:] = 0
                dist[:SPAN] = 0
            half, inning = "TOP", inning + 1
        if dist.sum() < 1e-12:
            break
    tied = dist.sum()
    if tied > 1e-12:
        if half == "BOTTOM":  # we stopped mid-inning in extras: finish this inning first
            dist = _apply(dist, half_inning_runs(home, 0, start_bases(inning)), 1)
            won += dist[SPAN + 1:].sum()
            lost += dist[:SPAN].sum()
            tied = dist[SPAN]
        a = np.array(half_inning_runs(away, 0, start_bases(10)))
        h = np.array(half_inning_runs(home, 0, start_bases(10)))
        cdf_h = np.cumsum(h)
        w = sum(a[r] * (1 - cdf_h[r]) for r in range(len(a)))
        lo = sum(a[r] * (cdf_h[r - 1] if r else 0.0) for r in range(len(a)))
        won += tied * (w / (w + lo) if w + lo > 0 else 0.5)
        lost += tied * (lo / (w + lo) if w + lo > 0 else 0.5)
    return float(won / (won + lost)) if won + lost > 0 else 0.5


# ------------------------------------------------------------------ strength data


@dataclass
class MLBStrengthBook:
    rates: dict[str, PARates]  # team id -> PA outcome rates
    n_pa: dict[str, int]  # plate appearances behind each rate (for the uncertainty band)
    label: str  # "SYNTHETIC" for fixtures
    as_of: str | None = None

    @classmethod
    def from_meta(cls, meta: dict) -> MLBStrengthBook:
        rates = {k: PARates(**v["rates"]) for k, v in meta.get("teams", {}).items()}
        n = {k: int(v.get("n_pa", 2000)) for k, v in meta.get("teams", {}).items()}
        return cls(rates, n, meta.get("label", "UNKNOWN"), meta.get("as_of"))


@dataclass
class MLBForecaster:
    strength: MLBStrengthBook
    model_version: str = "mlb_markov_v1"
    validity: timedelta = timedelta(seconds=90)
    draws: int = 12

    @property
    def status(self) -> ModelStatus:
        return ModelStatus.SYNTHETIC_ONLY if self.strength.label == "SYNTHETIC" \
            else ModelStatus.UNVALIDATED

    def _posterior_draws(self, team: str) -> list[PARates]:
        """Fixed Dirichlet draws per team (deterministic seed), so the run-distribution
        cache is reused across forecasts instead of recomputed for every state."""
        cache = self.__dict__.setdefault("_draw_cache", {})
        if team not in cache:
            import zlib
            rng = np.random.default_rng(zlib.crc32(team.encode()))
            r, n = self.strength.rates[team], self.strength.n_pa[team]
            out = []
            for _ in range(self.draws):
                v = np.round(rng.dirichlet(np.array(r.as_tuple()) * n), 4)
                v[0] += 1 - v.sum()
                out.append(PARates(*map(float, v)))
            cache[team] = out
        return cache[team]

    def home_probability(self, s: MLBState | None, game: Game
                         ) -> tuple[float, float, float] | str:
        season = game.details.get("season_type")
        if season not in ("REGULAR", "POSTSEASON"):
            return "CRITICAL:SEASON_TYPE_UNKNOWN"
        hr, ar = self.strength.rates.get(game.home_team), self.strength.rates.get(game.away_team)
        if hr is None or ar is None:
            return "STRENGTH_DATA_MISSING"
        if s is None:
            s = MLBState(snapshot_id="pre", game_id=game.game_id, source="pre",
                         source_status="SYNTHETIC_DEMO", as_of_event_time=None,
                         as_of_received_time=game.scheduled_start,
                         last_material_event_time=None, last_material_event_kind=None,
                         applied_seq=None, inning=1, half="TOP", outs=0,
                         runners=(False, False, False), home_runs=0, away_runs=0, balls=None,
                         strikes=None, home_pitcher=None, away_pitcher=None,
                         home_pitcher_pitches=None, away_pitcher_pitches=None,
                         lineup_confirmed=None)
        if s.postponed:
            return "GAME_POSTPONED"
        p = win_probability(s, hr, ar, season)
        hd, ad = self._posterior_draws(game.home_team), self._posterior_draws(game.away_team)
        sims = [win_probability(s, a, b, season) for a, b in zip(hd, ad, strict=True)]
        lo, hi = np.quantile(sims, [0.1, 0.9])
        return p, float(min(lo, p)), float(max(hi, p))

    def predict(self, state, game: Game, selection: str, prior, rule: SettlementRule,
                now: datetime) -> Prediction | str:
        if rule == SettlementRule.MLB_LISTED_PITCHERS and state is not None:
            listed = game.details.get("listed_pitchers")
            started = (state.home_starter, state.away_starter)
            if listed is None or None in started:
                return "CRITICAL:STARTERS_UNKNOWN"
            if tuple(listed) != started:
                return "LISTED_PITCHER_DID_NOT_START"
        out = self.home_probability(state, game)
        if isinstance(out, str):
            return out
        p, lo, hi = out
        if selection == game.away_team:
            p, lo, hi = 1 - p, 1 - hi, 1 - lo
        snap = state.snapshot_id if state is not None else f"pregame:{game.game_id}"
        return Prediction(
            prediction_id=stable_id("pred", [snap, self.model_version, selection]),
            game_id=game.game_id, snapshot_id=snap, selection_team=selection,
            settlement_rule=rule, model_version=self.model_version, model_status=self.status,
            feature_version="mlb_base_out_v1", probability=float(p),
            probability_low=float(min(lo, p)), probability_high=float(max(hi, p)),
            reliability="NONE" if self.status != ModelStatus.VALIDATED else "MEDIUM",
            created_time=now, valid_until=now + self.validity)


# ------------------------------------------------------------------ adapter


@dataclass
class MLBAdapter:
    sport: Sport = Sport.MLB
    default_rule: SettlementRule = SettlementRule.MLB_FULL_GAME_INCL_EXTRAS

    def new_reducer(self, game: Game) -> MLBStateReducer:
        return MLBStateReducer(game.game_id, game.home_team, game.away_team)

    def final_outcome(self, state: MLBState, game: Game, data: dict) -> FinalOutcome:
        if state.postponed:
            return FinalOutcome("POSTPONED", None)
        if state.home_runs == state.away_runs:
            return FinalOutcome("SUSPENDED", None, False, "tied final: suspended or data error")
        w = game.home_team if state.home_runs > state.away_runs else game.away_team
        return FinalOutcome("FINAL", w, False, f"{state.inning} innings",
                            (("starters", (state.home_starter, state.away_starter)),))

    def scoreboard(self, state: MLBState | None, game: Game) -> dict:
        if state is None:
            return {"kind": "mlb", "status": "SCHEDULED",
                    "probables": game.details.get("listed_pitchers")}
        return {"kind": "mlb",
                "status": "FINAL" if state.is_final else "POSTPONED" if state.postponed
                else "LIVE",
                "score": [state.away_runs, state.home_runs],
                "inning": state.inning, "half": state.half, "outs": state.outs,
                "runners": list(state.runners),
                "count": None if state.balls is None else [state.balls, state.strikes],
                "pitchers": [state.away_pitcher, state.home_pitcher],
                "pitch_counts": [state.away_pitcher_pitches, state.home_pitcher_pitches]}

    def features(self, state, game, selection, prior) -> dict[str, float | None]:
        from sports_edge.ingest.mlb_state import build_mlb_features
        if state is None:
            return {}
        return dict(build_mlb_features(state, selection == game.home_team, prior).values)


register(MLBAdapter())
