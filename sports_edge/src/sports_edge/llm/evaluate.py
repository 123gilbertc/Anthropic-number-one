"""LLM evaluation harness: does a model add value beyond the quant forecast?

Rules (SPEC section 10):
* Only PROSPECTIVE, timestamped, locked reviews count (``locked_before``
  must precede the game's start of play for the reviewed snapshot outcome to
  be unknown). Historical tests are exploratory: models may know results.
* An experimental LLM probability is scored as its own forecast and compared
  with the quant forecast on the *same* decisions (paired, clustered by game).
* Promotion requires enough games and a paired Brier improvement whose 95%
  interval excludes zero, after accounting for cost. Otherwise weight stays 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PairedRow:
    game_id: str
    quant_p: float
    llm_p: float
    outcome: int
    prospective: bool
    cost_usd: float


def promotion_report(rows: list[PairedRow], min_games: int = 200, seed: int = 0) -> dict:
    pro = [r for r in rows if r.prospective]
    games = sorted({r.game_id for r in pro})
    base = {"rows": len(rows), "prospective_rows": len(pro), "games": len(games),
            "decision_weight": 0.0}
    if len(games) < min_games:
        return base | {"status": "INSUFFICIENT_PROSPECTIVE_DATA",
                       "note": f"need >= {min_games} prospective games"}
    by_game: dict[str, list[PairedRow]] = {}
    for r in pro:
        by_game.setdefault(r.game_id, []).append(r)

    def diff(sample: list[str]) -> float:
        rs = [r for g in sample for r in by_game[g]]
        q = np.mean([(r.quant_p - r.outcome) ** 2 for r in rs])
        m = np.mean([(r.llm_p - r.outcome) ** 2 for r in rs])
        return float(q - m)  # > 0 means the LLM forecast is better

    rng = np.random.default_rng(seed)
    point = diff(games)
    boots = [diff(list(rng.choice(games, size=len(games)))) for _ in range(500)]
    lo, hi = np.quantile(boots, [0.025, 0.975])
    cost = sum(r.cost_usd for r in pro)
    status = "CANDIDATE_FOR_PROMOTION" if lo > 0 else "NO_DEMONSTRATED_VALUE"
    return base | {"status": status, "brier_improvement": point, "ci95": (float(lo), float(hi)),
                   "total_cost_usd": cost,
                   "note": "Promotion still requires an explicit, logged human decision."}
