"""Evidence behind an assessment: controlled sensitivity, changes, gaps, invalidation.

Numeric "drivers" come from *controlled sensitivity*: the real model is re-run on the
same state with one input neutralized (score level, man advantage, serve, base-out
state, pregame strength...). The difference is what that input contributes **in this
model**. It is model-dependent and not a causal claim, and every driver says so.
No driver is written from prose; if the model cannot be re-run, there are no drivers.

What-if: the same mechanism with user-chosen, sport-valid hypothetical states. The
result is labelled SIMULATION and touches nothing (no alerts, no portfolio, no history).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from sports_edge.domain.enums import Sport
from sports_edge.domain.records import Game, Prediction

MODEL_DEPENDENT = ("Model-dependent sensitivity: how much this input moves *this model's* "
                   "estimate, holding the rest of the state fixed. Not proof of causation.")

# Inputs each sport's baseline does not use or cannot see yet (shown as gaps, never guessed)
NOT_CONNECTED: dict[Sport, list[str]] = {
    Sport.NHL: ["Confirmed lineups (NOT CONNECTED)", "Shot-quality data (NOT CONNECTED)",
                "Penalty time remaining beyond man advantage (not modelled)",
                "Rest / travel (NOT CONNECTED)"],
    Sport.NFL: ["Injury and inactive reports (NOT CONNECTED)", "Weather (NOT CONNECTED)",
                "Efficiency metrics (NOT CONNECTED)", "Rule version (ASSUMED, unverified)"],
    Sport.TENNIS: ["Retirement / injury risk (not modelled)",
                   "Momentum, fatigue and pressure points (not modelled: points assumed "
                   "independent)", "Head-to-head and recent form (NOT CONNECTED)"],
    Sport.MLB: ["Current pitcher and bullpen quality (not modelled in the baseline)",
                "Park and weather (NOT CONNECTED)", "Lineup order and platoon splits "
                "(NOT CONNECTED)", "Ball-strike count (not modelled)"],
}

INVALIDATING_EVENTS: dict[Sport, str] = {
    Sport.NHL: "a goal, penalty, power-play end or goalie change",
    Sport.NFL: "a score, turnover, change of possession or quarterback change",
    Sport.TENNIS: "the end of the current game (especially a break of serve) or a set",
    Sport.MLB: "a run, a change in outs or runners, or a pitching change",
}


def _neutralizations(game: Game, state: Any, selection: str) -> list[tuple[str, str, Any, Any]]:
    """(key, label, hypothetical state, prior override or ...) per sport."""
    out: list[tuple[str, str, Any, Any]] = []
    s = state
    sport = game.sport
    keep = ...  # sentinel: keep the real prior
    if s is None:
        return [("pregame_strength", "Pregame strength estimate", None, 0.5)]
    if sport == Sport.NHL:
        if s.home_score != s.away_score:
            lo = min(s.home_score, s.away_score)
            out.append(("score", f"Score margin ({s.away_score}–{s.home_score})",
                        s.model_copy(update={"home_score": lo, "away_score": lo}), keep))
        if s.home_skaters is not None and s.away_skaters is not None \
                and s.home_skaters != s.away_skaters:
            out.append(("manpower", f"Man advantage ({s.away_skaters}v{s.home_skaters})",
                        s.model_copy(update={"home_skaters": 5, "away_skaters": 5}), keep))
        if s.home_net_empty or s.away_net_empty:
            out.append(("empty_net", "Empty net",
                        s.model_copy(update={"home_net_empty": False,
                                             "away_net_empty": False}), keep))
        out.append(("pregame_strength", "Pregame team-strength prior", s, 0.5))
    elif sport == Sport.NFL:
        if s.home_score != s.away_score:
            lo = min(s.home_score, s.away_score)
            out.append(("score", f"Score margin ({s.away_score}–{s.home_score})",
                        s.model_copy(update={"home_score": lo, "away_score": lo}), keep))
        if s.possession is not None and s.yardline_100 is not None:
            out.append(("field_position", f"Field position ({s.possession} needs "
                                          f"{s.yardline_100} yards)",
                        s.model_copy(update={"yardline_100": 50, "down": 1, "distance": 10}),
                        keep))
            out.append(("possession", f"Possession ({s.possession})",
                        s.model_copy(update={"possession": None, "down": None,
                                             "distance": None, "yardline_100": None}), keep))
        if s.home_timeouts is not None and s.away_timeouts is not None \
                and s.home_timeouts != s.away_timeouts:
            t = max(s.home_timeouts, s.away_timeouts)
            out.append(("timeouts", f"Timeouts ({s.away_timeouts}–{s.home_timeouts})",
                        s.model_copy(update={"home_timeouts": t, "away_timeouts": t}), keep))
        out.append(("pregame_strength", "Pregame team-strength prior", s, 0.5))
    elif sport == Sport.TENNIS:
        w1 = sum(1 for a, b in s.sets if a > b)
        w2 = len(s.sets) - w1
        if w1 != w2:
            k = min(w1, w2)
            sets = tuple([(6, 4)] * k + [(4, 6)] * k)
            out.append(("sets", f"Sets ({w1}–{w2})", s.model_copy(update={"sets": sets}), keep))
        if s.games_p1 != s.games_p2 and not s.in_tiebreak:
            g = min(s.games_p1, s.games_p2)
            out.append(("games", f"Games in this set ({s.games_p1}–{s.games_p2})",
                        s.model_copy(update={"games_p1": g, "games_p2": g}), keep))
        if (s.points_p1, s.points_p2) != (0, 0) and not s.in_tiebreak:
            out.append(("points", "Points in the current game",
                        s.model_copy(update={"points_p1": 0, "points_p2": 0}), keep))
        out.append(("strength", "Serve/return strength ratings", s, "EQUAL_STRENGTH"))
    elif sport == Sport.MLB:
        if s.home_runs != s.away_runs:
            lo = min(s.home_runs, s.away_runs)
            out.append(("score", f"Run margin ({s.away_runs}–{s.home_runs})",
                        s.model_copy(update={"home_runs": lo, "away_runs": lo}), keep))
        if any(s.runners) or s.outs:
            out.append(("base_out", "Base-out state (runners and outs)",
                        s.model_copy(update={"runners": (False, False, False), "outs": 0}),
                        keep))
        out.append(("strength", "Team plate-appearance ratings", s, "EQUAL_STRENGTH"))
    return out


def _equal_strength(forecaster, game: Game):
    """A copy of the forecaster where both participants have the average rating."""
    from sports_edge.sports.mlb import MLBForecaster, MLBStrengthBook, PARates
    from sports_edge.sports.tennis import TennisForecaster, TennisStrengthBook
    from sports_edge.sports.tennis_model import PlayerRates

    if isinstance(forecaster, TennisForecaster):
        sb = forecaster.strength
        surface = game.details.get("surface")
        a, b = sb.rates.get((game.home_team, surface)), sb.rates.get((game.away_team, surface))
        if a is None or b is None:
            return None
        m = PlayerRates((a.serve_won + b.serve_won) / 2, (a.serve_n + b.serve_n) // 2,
                        (a.return_won + b.return_won) / 2, (a.return_n + b.return_n) // 2)
        rates = dict(sb.rates)
        rates[(game.home_team, surface)] = rates[(game.away_team, surface)] = m
        return replace(forecaster, strength=TennisStrengthBook(rates, sb.averages, sb.label))
    if isinstance(forecaster, MLBForecaster):
        sb = forecaster.strength
        a, b = sb.rates.get(game.home_team), sb.rates.get(game.away_team)
        if a is None or b is None:
            return None
        avg = [(x + y) / 2 for x, y in zip(a.as_tuple(), b.as_tuple(), strict=True)]
        avg[0] += 1 - sum(avg)
        m = PARates(*avg)
        rates = dict(sb.rates)
        rates[game.home_team] = rates[game.away_team] = m
        n = dict(sb.n_pa)
        return MLBForecaster(MLBStrengthBook(rates, n, sb.label), forecaster.model_version,
                             forecaster.validity, forecaster.draws)
    return None


def sensitivity(forecaster, game: Game, state: Any, selection: str, prior: float | None,
                rule, now: datetime, base: Prediction) -> list[dict]:
    """Drivers for ``selection``: positive pp = the input pushes the estimate up."""
    out = []
    for key, label, hyp, prior_override in _neutralizations(game, state, selection):
        f = forecaster
        use_prior = prior
        if prior_override == "EQUAL_STRENGTH":
            f = _equal_strength(forecaster, game)
            if f is None:
                continue
        elif prior_override is not ...:
            if prior is None:
                continue  # no prior in use: nothing to neutralize
            use_prior = prior_override
        hyp_state = hyp if hyp is None else hyp.model_copy(
            update={"snapshot_id": f"whatif:{key}:{getattr(hyp, 'snapshot_id', '')}"})
        try:
            alt = f.predict(hyp_state, game, selection, use_prior, rule, now)
        except Exception as e:  # a model that cannot run on this state: say so, no number
            out.append({"key": key, "label": label, "available": False,
                        "reason": type(e).__name__})
            continue
        if not isinstance(alt, Prediction):
            out.append({"key": key, "label": label, "available": False, "reason": alt})
            continue
        delta = (base.probability - alt.probability) * 100
        out.append({"key": key, "label": label, "available": True,
                    "delta_pp": round(delta, 2),
                    "estimate_with_input": round(base.probability, 4),
                    "estimate_neutralized": round(alt.probability, 4),
                    "kind": "MODEL", "note": MODEL_DEPENDENT})
    return sorted(out, key=lambda d: -abs(d.get("delta_pp", 0.0)))


# ------------------------------------------------------------------ what-if

WHATIF_FIELDS: dict[Sport, dict[str, type]] = {
    Sport.NHL: {"home_score": int, "away_score": int, "period": int,
                "seconds_remaining_in_period": int, "home_skaters": int, "away_skaters": int,
                "home_net_empty": bool, "away_net_empty": bool},
    Sport.NFL: {"home_score": int, "away_score": int, "quarter": int,
                "seconds_remaining_in_quarter": int, "possession": str, "down": int,
                "distance": int, "yardline_100": int, "home_timeouts": int,
                "away_timeouts": int},
    Sport.TENNIS: {"games_p1": int, "games_p2": int, "points_p1": int, "points_p2": int,
                   "server": str, "sets": list},
    Sport.MLB: {"home_runs": int, "away_runs": int, "inning": int, "half": str, "outs": int,
                "runners": list},
}


class WhatIfError(ValueError):
    pass


def what_if(forecaster, game: Game, state: Any, edits: dict, prior_by_side: dict,
            rule, now: datetime) -> dict:
    if state is None:
        raise WhatIfError("no observed game state yet: what-if needs a live state to edit")
    allowed = WHATIF_FIELDS[game.sport]
    unknown = set(edits) - set(allowed)
    if unknown:
        raise WhatIfError(f"fields not editable for {game.sport.value}: {sorted(unknown)}")
    upd: dict[str, Any] = {}
    for k, v in edits.items():
        if k == "runners":
            if not (isinstance(v, list) and len(v) == 3):
                raise WhatIfError("runners must be [1B, 2B, 3B]")
            upd[k] = tuple(bool(x) for x in v)
        elif k == "sets":
            try:
                upd[k] = tuple((int(a), int(b)) for a, b in v)
            except (TypeError, ValueError) as e:
                raise WhatIfError("sets must be [[p1, p2], ...]") from e
        elif k in ("possession",):
            if v not in (game.home_team, game.away_team, None):
                raise WhatIfError("possession must be one of the two teams")
            upd[k] = v
        elif k == "server":
            if v not in ("P1", "P2"):
                raise WhatIfError("server must be P1 or P2")
            upd[k] = v
        elif k == "half":
            if v not in ("TOP", "BOTTOM"):
                raise WhatIfError("half must be TOP or BOTTOM")
            upd[k] = v
        elif allowed[k] is bool:
            upd[k] = bool(v)
        else:
            try:
                upd[k] = int(v)
            except (TypeError, ValueError) as e:
                raise WhatIfError(f"{k} must be an integer") from e
            if upd[k] < 0:
                raise WhatIfError(f"{k} cannot be negative")
    hyp = state.model_copy(update={**upd, "snapshot_id": "whatif:user",
                                   "is_final": False, "pending_reconciliation": ()})
    # validate with the sport's own rules and record type
    try:
        hyp = type(state).model_validate(hyp.model_dump())
    except Exception as e:
        raise WhatIfError(f"not a valid {game.sport.value} state: {e}") from e
    if not hyp.coherent():
        raise WhatIfError(f"not a valid {game.sport.value} state under the sport's rules")
    if game.sport == Sport.TENNIS:
        from sports_edge.sports.tennis_rules import valid_score
        fmt = hyp.fmt()
        if fmt is None or not valid_score(fmt, hyp.score()):
            raise WhatIfError("not a valid tennis score for this match format")
    sides = {}
    for side in (game.home_team, game.away_team):
        out = forecaster.predict(hyp, game, side, prior_by_side.get(side), rule, now)
        sides[side] = {"available": isinstance(out, Prediction),
                       "probability": out.probability if isinstance(out, Prediction) else None,
                       "low": out.probability_low if isinstance(out, Prediction) else None,
                       "high": out.probability_high if isinstance(out, Prediction) else None,
                       "reason": None if isinstance(out, Prediction) else out}
    return {"label": "SIMULATION", "note": "Hypothetical state run through the real model. "
            "It does not change the observed game, alerts, decisions or your portfolio.",
            "state": hyp.model_dump(mode="json"), "estimates": sides}
