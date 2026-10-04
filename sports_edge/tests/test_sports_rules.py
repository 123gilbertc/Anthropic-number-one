"""Sport rules, reducers and baseline models for tennis, NFL and MLB.

The probability models are checked against closed forms and against Monte Carlo
simulation of the *rules engine*, so the model and the scorer cannot silently
disagree. None of this says anything about predictive value on real games.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from sports_edge.contracts.settlement import FinalResult, resolve
from sports_edge.domain.enums import Outcome, SettlementRule, SourceStatus, Sport
from sports_edge.domain.records import BookLevel, Game, MarketMapping
from sports_edge.ingest.mlb_state import MLBState
from sports_edge.ingest.nhl_state import InvalidEvent, NormalizedGameEvent
from sports_edge.pricing.ev import expected_value
from sports_edge.pricing.fees import KalshiQuadraticFee
from sports_edge.pricing.fills import walk_asks
from sports_edge.sports.mlb import PARates, half_inning_runs, win_probability
from sports_edge.sports.nfl import NFLReducer
from sports_edge.sports.tennis import TennisForecaster, TennisReducer, TennisStrengthBook
from sports_edge.sports.tennis_model import p_game, p_match, p_tiebreak
from sports_edge.sports.tennis_rules import P1, P2, Format, Score, play_point, start_set, tb_server

T = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)


def ev(gid, typ, seq, data=None, eid=None, dt=None):
    t = T + timedelta(seconds=dt if dt is not None else seq)
    return NormalizedGameEvent(game_id=gid, source="t", source_status=SourceStatus.REPLAY,
                               type=typ, provider_event_id=eid or f"{gid}-{seq}", seq=seq,
                               event_time=t, published_time=t, received_time=t,
                               data=data or {})


# ------------------------------------------------------------------ tennis rules


def test_game_probability_matches_closed_form():
    for p in (0.55, 0.6, 0.7):
        q = 1 - p
        closed = p**4 * (1 + 4 * q + 10 * q**2) + 20 * p**3 * q**3 * p**2 / (1 - 2 * p * q)
        assert p_game(p) == pytest.approx(closed, abs=1e-12)
    assert p_game(0.6, 3, 3, no_ad=True) == pytest.approx(0.6)


def test_symmetric_players_are_even_and_stronger_server_is_favoured():
    for fmt in (Format(3, "TB7"), Format(5, "TB10"), Format(5, "ADV"), Format(3, "MTB10")):
        assert p_match(fmt, Score(), 0.63, 0.63) == pytest.approx(0.5, abs=1e-9)
        assert p_match(fmt, Score(), 0.66, 0.62) > 0.5
    # best-of-five amplifies the stronger player's edge
    assert p_match(Format(5, "TB7"), Score(), 0.66, 0.62) > \
        p_match(Format(3, "TB7"), Score(), 0.66, 0.62)


def test_tiebreak_server_rotation_and_win_by_two():
    assert [tb_server(P1, k) for k in range(7)] == [P1, P2, P2, P1, P1, P2, P2]
    assert p_tiebreak(0.6, 0.6, 7, 5, P1, 7) == 1.0
    assert 0 < p_tiebreak(0.6, 0.6, 6, 6, P1, 7) < 1
    fmt = Format(3, "TB7")
    sc = Score(games=(6, 6), in_tiebreak=True, tiebreak_target=7, server=P1,
               tiebreak_first_server=P1, points=(6, 5))
    r = play_point(fmt, sc, P1)
    assert r.set_won_by == P1 and r.score.sets == ((7, 6),)
    assert r.score.server == P2  # first tiebreak server receives first in the next set


def test_set_and_final_set_formats():
    fmt = Format(3, "ADV")
    sc = Score(sets=((6, 4), (4, 6)), games=(6, 6), server=P1)
    assert not sc.in_tiebreak
    sc = start_set(fmt, sc)
    assert not sc.in_tiebreak  # advantage final set: no tiebreak at 6-6
    mtb = Format(3, "MTB10", no_ad=True, doubles=True)
    s2 = start_set(mtb, Score(sets=((6, 4), (3, 6)), server=P2))
    assert s2.in_tiebreak and s2.tiebreak_target == 10
    # 7-5 ends a set, 6-5 does not
    r = play_point(Format(3, "TB7"), Score(games=(6, 5), points=(3, 0), server=P1), P1)
    assert r.set_won_by == P1 and r.score.sets == ((7, 5),)
    r = play_point(Format(3, "TB7"), Score(games=(5, 5), points=(3, 0), server=P1), P1)
    assert r.set_won_by is None and r.score.games == (6, 5)


def test_markov_model_agrees_with_rules_engine_simulation():
    """Simulate matches point by point with the scorer; compare with the exact model."""
    fmt = Format(3, "TB7")
    start = Score(sets=((4, 6),), games=(3, 2), points=(1, 2), server=P2)
    s1, s2 = 0.66, 0.63
    exact = p_match(fmt, start, s1, s2)
    rng = np.random.default_rng(5)
    wins, n = 0, 4000
    for _ in range(n):
        sc = start
        while sc.winner is None:
            pw = s1 if sc.server == P1 else 1 - s2
            sc = play_point(fmt, sc, P1 if rng.random() < pw else P2).score
        wins += sc.winner == P1
    se = (exact * (1 - exact) / n) ** 0.5
    assert abs(wins / n - exact) < 4 * se


def test_tennis_reducer_flags_and_retirement():
    g = "TEN"
    r = TennisReducer(g, "A", "B")
    r.apply(ev(g, "POINT", 1, {"winner": P1}))
    assert "FORMAT_UNKNOWN" in r.state.pending_reconciliation
    r.apply(ev(g, "SNAPSHOT", 2, {"best_of": 3, "final_set": "TB7", "no_ad": False,
                                  "doubles": False, "surface": "HARD", "server": P1}))
    assert r.state.pending_reconciliation == ()
    for i in range(4):
        r.apply(ev(g, "POINT", 3 + i, {"winner": P2}))
    assert (r.state.games_p1, r.state.games_p2) == (0, 1)  # a break of serve
    assert any("Break" in x for x in r.last_labels)
    res = r.apply(ev(g, "POINT", 7, {"winner": P1}))
    assert not res.material  # a point inside a game is not a material event
    assert r.apply(ev(g, "POINT", 7, {"winner": P1})).note == "duplicate"
    r.apply(ev(g, "POINT", 9, {"winner": P1, "expect": {"games": [5, 5], "sets": []}}))
    assert {"FEED_GAP", "SCORE_MISMATCH"} <= set(r.state.pending_reconciliation)
    r.apply(ev(g, "RETIRED", 10, {"player": P2}))
    assert r.state.is_final and r.state.winner == P1 and r.state.termination == "RETIRED"


def test_tennis_retirement_settlement_rules_and_doubles_abstain():
    game = Game(game_id="TEN", sport=Sport.TENNIS, season="2026", home_team="A",
                away_team="B", scheduled_start=T, status="LIVE", source="t",
                details={"best_of": 3, "final_set": "MTB10", "no_ad": True, "doubles": True,
                         "surface": "HARD"})
    res = FinalResult(0, 0, "FINAL", winner="A", termination="RETIRED")
    m = lambda rule: MarketMapping(mapping_id="m", game_id="TEN", venue="SYNTHETIC",  # noqa: E731
                                   contract_id="C", selection_team="A", settlement_rule=rule,
                                   rules_text_hash=None, tick_size=Decimal("0.01"),
                                   verified_by="t")
    assert resolve(m(SettlementRule.TENNIS_MATCH_RETIREMENT_ADVANCER_WINS), game, res)[0] == \
        Outcome.WIN
    assert resolve(m(SettlementRule.TENNIS_MATCH_RETIREMENT_VOID), game, res)[0] == Outcome.VOID
    f = TennisForecaster(TennisStrengthBook({}, {}, "SYNTHETIC"))
    assert f.predict(None, game, "A", None, SettlementRule.TENNIS_MATCH_RETIREMENT_VOID, T) == \
        "DOUBLES_MODEL_NOT_ENABLED"
    singles = game.model_copy(update={"details": {**game.details, "doubles": False,
                                                  "final_set": "TB7", "no_ad": False}})
    out = f.predict(None, singles, "A", None, SettlementRule.TENNIS_MATCH_RETIREMENT_VOID, T)
    assert out.startswith("STRENGTH_DATA_MISSING")  # never invents a player's strength


# ------------------------------------------------------------------ NFL


def test_nfl_reducer_rules():
    g = "NFL"
    r = NFLReducer(g, "H", "A", season_type="REGULAR")
    r.apply(ev(g, "SNAPSHOT", 1, {"quarter": 1, "seconds_remaining_in_quarter": 900,
                                  "home_score": 0, "away_score": 0, "possession": "H",
                                  "down": 1, "distance": 10, "yardline_100": 75}))
    with pytest.raises(InvalidEvent):
        r.apply(ev(g, "SCORE", 2, {"team": "H", "points": 5}))
    r.apply(ev(g, "SCORE", 2, {"team": "H", "points": 6}))
    assert r.state.home_score == 6 and r.last_labels
    r.apply(ev(g, "TURNOVER", 3, {"gaining_team": "A", "yardline_100": 60}))
    assert r.state.possession == "A" and r.state.down == 1
    assert r.state.last_material_event_kind == "TURNOVER"
    for i in range(4):
        r.apply(ev(g, "TIMEOUT", 4 + i, {"team": "A"}))
    assert "TIMEOUT_COUNT_UNKNOWN" in r.state.pending_reconciliation  # a 4th timeout


def test_nfl_tie_settles_by_contract_rule_and_postseason_cannot_tie():
    game = Game(game_id="NFL", sport=Sport.NFL, season="2026", home_team="H", away_team="A",
                scheduled_start=T, status="LIVE", source="t")
    tie = FinalResult(20, 20, "FINAL", tie=True)
    mk = lambda rule: MarketMapping(mapping_id="m", game_id="NFL", venue="SYNTHETIC",  # noqa: E731
                                    contract_id="C", selection_team="H", settlement_rule=rule,
                                    rules_text_hash=None, tick_size=Decimal("0.01"),
                                    verified_by="t")
    assert resolve(mk(SettlementRule.NFL_INCL_OT_TIE_VOID), game, tie)[0] == Outcome.VOID
    assert resolve(mk(SettlementRule.NFL_INCL_OT_TIE_LOSES), game, tie)[0] == Outcome.LOSS
    r = NFLReducer("P", "H", "A", season_type="POSTSEASON")
    r.apply(ev("P", "SNAPSHOT", 1, {"quarter": 5, "seconds_remaining_in_quarter": 0,
                                    "home_score": 10, "away_score": 10}))
    with pytest.raises(InvalidEvent):
        r.apply(ev("P", "GAME_END", 2))


def test_void_probability_enters_expected_value():
    fee = KalshiQuadraticFee()
    fill = walk_asks((BookLevel(price=Decimal("0.50"), quantity=100),), 10, fee)
    plain = expected_value(fill, 0.5, 0.5, fee)
    with_void = expected_value(fill, 0.5, 0.5, fee, probability_void=0.05)
    # a refund on a tie is worth something: EV rises by about p_void * cost
    assert with_void.ev_point > plain.ev_point
    assert with_void.ev_point - plain.ev_point == pytest.approx(
        Decimal("0.05") * fill.cost, abs=Decimal("0.01"))
    with pytest.raises(ValueError):
        expected_value(fill, 0.6, 0.6, fee, probability_void=0.5)


# ------------------------------------------------------------------ MLB


def _mlb(**k):
    base = dict(snapshot_id="x", game_id="g", source="s", source_status="SYNTHETIC_DEMO",
                as_of_event_time=None, as_of_received_time=T, last_material_event_time=None,
                last_material_event_kind=None, applied_seq=None, inning=1, half="TOP", outs=0,
                runners=(False, False, False), home_runs=0, away_runs=0, balls=None,
                strikes=None, home_pitcher=None, away_pitcher=None, home_pitcher_pitches=None,
                away_pitcher_pitches=None, lineup_confirmed=None)
    base.update(k)
    return MLBState(**base)


R = PARates(0.68, 0.09, 0.15, 0.045, 0.005, 0.03)


def test_half_inning_distribution_and_monte_carlo():
    from sports_edge.sports.mlb import _advance
    d = np.array(half_inning_runs(R, 0, (0, 0, 0)))
    assert d.sum() == pytest.approx(1.0) and d[0] > 0.6
    rng = np.random.default_rng(1)
    runs = []
    for _ in range(20000):
        outs, b, r = 0, (0, 0, 0), 0
        while outs < 3:
            oc = rng.choice(["out", "walk", "single", "double", "triple", "hr"],
                            p=np.array(R.as_tuple()))
            if oc == "out":
                outs += 1
            else:
                b, x = _advance(b, str(oc))
                r += x
        runs.append(r)
    mean = float(np.dot(np.arange(len(d)), d))
    assert abs(np.mean(runs) - mean) < 4 * np.std(runs) / np.sqrt(len(runs))


def test_mlb_win_probability_rules():
    assert win_probability(_mlb(), R, R, "REGULAR") == pytest.approx(0.5, abs=1e-9)
    assert win_probability(_mlb(is_final=True, home_runs=3, away_runs=2), R, R, "REGULAR") == 1
    lead = win_probability(_mlb(inning=9, half="TOP", outs=2, home_runs=3, away_runs=2), R, R,
                           "REGULAR")
    assert 0.8 < lead < 1
    # extras: regular season starts with a runner on 2nd; postseason does not.
    # With equal teams both are 50/50 from the top of the 10th with the bases as given,
    # and the batting-last team benefits from the walk-off in the bottom half.
    reg = win_probability(_mlb(inning=10, half="TOP", runners=(False, True, False)), R, R,
                          "REGULAR")
    post = win_probability(_mlb(inning=10, half="TOP"), R, R, "POSTSEASON")
    assert post == pytest.approx(0.5, abs=1e-9)
    assert reg == pytest.approx(0.5, abs=1e-9)
