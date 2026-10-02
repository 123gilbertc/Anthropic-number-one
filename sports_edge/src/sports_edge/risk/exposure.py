"""Paper risk limits and exposure accounting.

Rules (all enforced, none overridable by an LLM):

* The per-team cap covers the *total* position cost: initial entry, every
  addition, and fees. It is not "$X more on each dip".
* Cash spent on open positions is locked until settlement. Unsettled
  proceeds can never fund another purchase.
* Stakes never scale up after losses (no Martingale) or after wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sports_edge.domain.enums import Reason

ZERO = Decimal("0")


@dataclass(frozen=True)
class RiskLimits:
    bankroll: Decimal
    per_team_cap: Decimal  # includes fees
    per_game_cap: Decimal
    portfolio_cap: Decimal  # total open cost across all games
    daily_spend_cap: Decimal
    cash_reserve: Decimal  # never spend below this
    label: str = "custom"

    @staticmethod
    def example_1000_bankroll_100_cap() -> RiskLimits:
        """The $1,000 bankroll / $100 per-team example discussed by the user.

        Paper scenario only. A $100 cap is 10% of bankroll on one team in one
        game: that is high single-position concentration, not a safe default.
        """
        return RiskLimits(
            bankroll=Decimal("1000"),
            per_team_cap=Decimal("100"),
            per_game_cap=Decimal("100"),
            portfolio_cap=Decimal("500"),
            daily_spend_cap=Decimal("300"),
            cash_reserve=Decimal("200"),
            label="EXAMPLE_1000_100 (10% single-team concentration; paper only)",
        )


@dataclass
class ExposureLedger:
    limits: RiskLimits
    cash: Decimal = ZERO  # free cash; set from bankroll in __post_init__
    open_cost_by_team: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    open_cost_by_game: dict[str, Decimal] = field(default_factory=dict)
    spend_by_day: dict[date, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cash == ZERO:
            self.cash = self.limits.bankroll

    @property
    def open_cost(self) -> Decimal:
        return sum(self.open_cost_by_game.values(), ZERO)

    def remaining(self, game_id: str, team: str, day: date) -> tuple[Decimal, tuple[Reason, ...]]:
        """Max additional all-in dollars allowed, and which limits are binding at zero."""
        lim = self.limits
        candidates = {
            Reason.TEAM_CAP_REACHED: lim.per_team_cap
            - self.open_cost_by_team.get((game_id, team), ZERO),
            Reason.GAME_CAP_REACHED: lim.per_game_cap - self.open_cost_by_game.get(game_id, ZERO),
            Reason.PORTFOLIO_CAP_REACHED: lim.portfolio_cap - self.open_cost,
            Reason.DAILY_LIMIT_REACHED: lim.daily_spend_cap - self.spend_by_day.get(day, ZERO),
            Reason.INSUFFICIENT_CASH: self.cash - lim.cash_reserve,
        }
        room = max(ZERO, min(candidates.values()))
        binding = tuple(r for r, v in candidates.items() if v <= ZERO)
        return room, binding

    def record_purchase(
        self, game_id: str, team: str, day: date, cost: Decimal, fees: Decimal
    ) -> None:
        total = cost + fees
        room, binding = self.remaining(game_id, team, day)
        if total > room:
            raise ValueError(f"purchase {total} exceeds remaining room {room} ({binding})")
        self.cash -= total
        key = (game_id, team)
        self.open_cost_by_team[key] = self.open_cost_by_team.get(key, ZERO) + total
        self.open_cost_by_game[game_id] = self.open_cost_by_game.get(game_id, ZERO) + total
        self.spend_by_day[day] = self.spend_by_day.get(day, ZERO) + total

    def record_settlement(self, game_id: str, team: str, payout: Decimal) -> None:
        """Release locked cost and credit payout. Only now does cash become spendable."""
        key = (game_id, team)
        locked = self.open_cost_by_team.pop(key, ZERO)
        self.open_cost_by_game[game_id] = self.open_cost_by_game.get(game_id, ZERO) - locked
        if self.open_cost_by_game[game_id] <= ZERO:
            self.open_cost_by_game.pop(game_id)
        self.cash += payout
