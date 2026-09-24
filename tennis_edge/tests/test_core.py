import csv
import json
import random
from datetime import date, timedelta

import pytest

from tennis_edge.backtest import BacktestConfig, run
from tennis_edge.betting import implied_from_decimal, kelly_binary, size_bet
from tennis_edge.data import Match, read_csv
from tennis_edge.elo import Elo, bo3_to_bo5, expected
from tennis_edge.names import player_key
from tennis_edge.polymarket import _parse_event, guess_best_of, guess_surface


def test_player_keys_match_across_sources():
    assert player_key("Jannik Sinner") == player_key("Sinner J.") == "sinner j"
    assert player_key("Felix Auger-Aliassime") == player_key("Auger-Aliassime F.")
    assert player_key("Alex de Minaur") == player_key("De Minaur A.")
    assert player_key("Iga Świątek") == player_key("Swiatek I.")


def test_bo5_amplifies_favourite():
    assert bo3_to_bo5(0.5) == pytest.approx(0.5, abs=1e-6)
    assert bo3_to_bo5(0.7) > 0.7
    assert bo3_to_bo5(0.3) < 0.3


def test_kelly_and_sizing():
    assert kelly_binary(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_binary(0.5, 0.6) == 0
    bet = size_bet("A", 0.6, 0.5, 1000, kelly_fraction=0.25, max_bet_pct=0.02, min_edge=0.03)
    assert bet.stake == 20.0  # capped at 2%
    assert size_bet("A", 0.52, 0.5, 1000, min_edge=0.03) is None
    a, b = implied_from_decimal(1.9, 1.9)
    assert a == pytest.approx(0.5)


def synth(n_players=60, n_matches=12000, seed=1, noise=0.0):
    """Players with hidden true skill; market odds = true prob (+ optional noise) + 5% vig."""
    rng = random.Random(seed)
    letters = "abcdefghij"
    surname = lambda i: "P" + "".join(letters[int(c)] for c in f"{i:03d}")
    skill = {f"{surname(i)} X.": rng.gauss(1500, 150) for i in range(n_players)}
    names = list(skill)
    d = date(2015, 1, 1)
    out = []
    for i in range(n_matches):
        a, b = rng.sample(names, 2)
        p = expected(skill[a], skill[b])
        pm = min(max(p + rng.gauss(0, noise), 0.02), 0.98)
        w, l = (a, b) if rng.random() < p else (b, a)
        pw = pm if w == a else 1 - pm
        out.append(Match(d + timedelta(days=i // 20), w, l, "hard",
                         odds_w=1 / (pw * 1.025), odds_l=1 / ((1 - pw) * 1.025)))
    return out, skill


def test_elo_recovers_true_skill_ordering():
    matches, skill = synth()
    elo = Elo().fit(matches)
    assert len(elo.n) == len(skill)
    best = max(skill, key=skill.get)
    worst = min(skill, key=skill.get)
    assert elo.prob(best, worst) > 0.85


def test_backtest_cannot_beat_a_perfect_market():
    matches, _ = synth(noise=0.0)
    res = run(matches, BacktestConfig(start=date(2016, 1, 1), min_matches=20))
    assert res.n_pred > 1000
    assert res.model_ll / res.n_pred < 0.62   # model is actually predicting
    assert res.market_ll < res.model_ll       # perfect market is sharper
    assert res.flat_profit / max(res.bets, 1) < 0.02  # no free money


def test_backtest_finds_edge_against_a_noisy_market():
    matches, _ = synth(noise=0.12, seed=3, n_matches=20000)
    res = run(matches, BacktestConfig(start=date(2016, 1, 1), min_matches=20, w_model=0.6))
    assert res.bets > 200
    assert res.model_ll < res.market_ll
    assert res.flat_profit / res.bets > 0.03


def test_tennis_data_csv(tmp_path):
    p = tmp_path / "2024.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Tournament", "Surface", "Round", "Best of", "Winner", "Loser",
                    "Comment", "B365W", "B365L", "PSW", "PSL"])
        w.writerow(["26/05/2024", "French Open", "Clay", "1st Round", "5", "Sinner J.",
                    "Eubanks C.", "Completed", "1.01", "20", "1.02", "21.5"])
        w.writerow(["27/05/2024", "French Open", "Clay", "1st Round", "5", "Nadal R.",
                    "Zverev A.", "Retired", "", "", "", ""])
    ms = read_csv(str(p))
    assert ms[0].surface == "clay" and ms[0].best_of == 5 and ms[0].odds_w == 1.02
    assert ms[1].completed is False and ms[1].odds_w is None


def test_polymarket_event_parsing():
    ev = {"title": "Wimbledon: Sinner vs Alcaraz", "slug": "wim-sinner-alcaraz",
          "markets": [
              {"question": "Sinner vs. Alcaraz", "conditionId": "c1", "active": True,
               "outcomes": json.dumps(["Jannik Sinner", "Carlos Alcaraz"]),
               "outcomePrices": json.dumps(["0.45", "0.55"]),
               "clobTokenIds": json.dumps(["t1", "t2"]), "liquidity": "5000"},
              {"question": "Sinner vs. Alcaraz: Set 1 winner", "conditionId": "c2",
               "outcomes": json.dumps(["Jannik Sinner", "Carlos Alcaraz"])},
              {"question": "Will Sinner win Wimbledon?", "conditionId": "c3",
               "outcomes": json.dumps(["Yes", "No"])},
          ]}
    mks = _parse_event(ev, None, set())
    assert len(mks) == 1
    m = mks[0]
    assert m.surface == "grass" and m.best_of == 5
    assert m.outcomes[0].mid == 0.45 and m.outcomes[1].token_id == "t2"
    assert guess_best_of("WTA Wimbledon") == 3
    assert guess_surface("Mutua Madrid Open") == "clay"
