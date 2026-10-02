"""Contract mapping validation and settlement resolution.

The forecast target is the *exact contract's* payout, so the first gate is
always: does the contract we are pricing settle on the same event the model
forecasts?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sports_edge.domain.enums import Outcome, SettlementRule, Sport
from sports_edge.domain.records import Game, MarketMapping

_SPORT_RULES: dict[Sport, set[SettlementRule]] = {
    Sport.NHL: {SettlementRule.NHL_INCLUDING_OT_SO, SettlementRule.NHL_REGULATION_ONLY},
    Sport.MLB: {SettlementRule.MLB_FULL_GAME_INCL_EXTRAS, SettlementRule.MLB_LISTED_PITCHERS},
}


class MappingError(ValueError):
    pass


def validate_mapping(mapping: MarketMapping, game: Game, forecast_rule: SettlementRule) -> None:
    """Raise MappingError unless the contract can be priced by the forecast."""
    if mapping.game_id != game.game_id:
        raise MappingError("mapping points at a different game")
    if mapping.selection_team not in (game.home_team, game.away_team):
        raise MappingError(f"selection {mapping.selection_team!r} is not a team in this game")
    if mapping.settlement_rule not in _SPORT_RULES[game.sport]:
        raise MappingError(f"{mapping.settlement_rule} is not a {game.sport} rule")
    if mapping.settlement_rule != forecast_rule:
        raise MappingError(
            f"contract settles as {mapping.settlement_rule} but forecast targets {forecast_rule}"
        )
    if mapping.settlement_rule == SettlementRule.MLB_LISTED_PITCHERS and not mapping.listed_pitchers:
        raise MappingError("listed-pitcher contract without listed pitchers")
    if mapping.tick_size <= 0:
        raise MappingError("tick size must be positive")


@dataclass(frozen=True)
class FinalResult:
    home_score: int
    away_score: int
    status: Literal["FINAL", "POSTPONED", "CANCELLED", "SUSPENDED"]
    nhl_decided_in: Literal["REG", "OT", "SO"] | None = None
    # NHL regulation-only markets need the score after 60 minutes
    nhl_regulation_home: int | None = None
    nhl_regulation_away: int | None = None
    mlb_actual_starters: tuple[str, str] | None = None  # (home, away)
    mlb_listed_starters: tuple[str, str] | None = None


def resolve(mapping: MarketMapping, game: Game, result: FinalResult) -> tuple[Outcome, str]:
    """Settle a YES position on ``mapping.selection_team``.

    Venue rules are authoritative; this function encodes our *reading* of
    them and is unit-tested per rule. Unhandled situations return PENDING
    rather than guessing.
    """
    sel_home = mapping.selection_team == game.home_team

    if result.status in ("POSTPONED", "CANCELLED"):
        # Most venues void (refund) or roll over; both mean no profit/loss here.
        # Check venue rules per market: some have a resolution deadline instead.
        return Outcome.VOID, f"game {result.status.lower()}: treated as void pending venue rules"
    if result.status == "SUSPENDED":
        return Outcome.PENDING, "suspended game: wait for venue resolution"

    rule = mapping.settlement_rule
    if rule == SettlementRule.NHL_INCLUDING_OT_SO:
        if result.home_score == result.away_score:
            return Outcome.PENDING, "final NHL score tied: data error"
        home_won = result.home_score > result.away_score
        return _wl(home_won == sel_home), f"decided in {result.nhl_decided_in or 'UNKNOWN'}"

    if rule == SettlementRule.NHL_REGULATION_ONLY:
        if result.nhl_regulation_home is None or result.nhl_regulation_away is None:
            return Outcome.PENDING, "regulation score unknown"
        if result.nhl_regulation_home == result.nhl_regulation_away:
            return Outcome.LOSS, "tied after regulation: team-win-in-regulation loses"
        home_won = result.nhl_regulation_home > result.nhl_regulation_away
        return _wl(home_won == sel_home), "decided in regulation"

    if rule in (SettlementRule.MLB_FULL_GAME_INCL_EXTRAS, SettlementRule.MLB_LISTED_PITCHERS):
        if rule == SettlementRule.MLB_LISTED_PITCHERS:
            if result.mlb_actual_starters is None or result.mlb_listed_starters is None:
                return Outcome.PENDING, "starter information unknown"
            if result.mlb_actual_starters != result.mlb_listed_starters:
                return Outcome.VOID, "listed pitcher did not start"
        if result.home_score == result.away_score:
            return Outcome.PENDING, "tied final MLB score: suspended/data error"
        home_won = result.home_score > result.away_score
        return _wl(home_won == sel_home), "full game including extra innings"

    return Outcome.PENDING, f"no resolver for {rule}"


def _wl(won: bool) -> Outcome:
    return Outcome.WIN if won else Outcome.LOSS
