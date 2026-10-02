"""Plain-English explanations of reason codes, shown next to every signal."""

from sports_edge.domain.enums import Reason

TEXT: dict[Reason, str] = {
    Reason.MAPPING_INVALID: "The contract could not be matched to this game and team.",
    Reason.SETTLEMENT_MISMATCH: "The contract settles on a different event than the model forecasts.",
    Reason.GAME_STATE_STALE: "The game feed has not updated recently enough.",
    Reason.GAME_STATE_INCOHERENT: "The game state is internally inconsistent.",
    Reason.GAME_FEED_NOT_CONNECTED: "No authorized live game feed is connected for this data.",
    Reason.MARKET_INACTIVE: "The market is closed or the game is over.",
    Reason.BOOK_INVALID: "The order book is out of sync and awaiting a fresh snapshot.",
    Reason.BOOK_STALE: "The market connection has gone quiet.",
    Reason.MARKET_SETTLING: "A goal or similar event just happened; prices may not have caught up.",
    Reason.SPREAD_TOO_WIDE: "The gap between buy and sell prices is too wide.",
    Reason.DEPTH_INSUFFICIENT: "Not enough contracts are offered at usable prices.",
    Reason.REFERENCE_PRE_EVENT: "The sportsbook quote was published before the latest game event.",
    Reason.REFERENCE_STALE: "The sportsbook quote is too old or has no publication time.",
    Reason.REFERENCE_DISAGREEMENT: "The sportsbook view differs sharply from the model: investigate.",
    Reason.PENDING_RECONCILIATION: "A late, missing or corrected game event (or unknown goalie)"
                                   " must be confirmed first.",
    Reason.CRITICAL_FEATURE_MISSING: "Key information (score or clock) is missing, so the model abstains.",
    Reason.MODEL_NOT_VALIDATED: "No validated model is loaded, so no value signal can be produced.",
    Reason.PREDICTION_STALE: "The probability estimate is for an older game state.",
    Reason.EV_BELOW_MARGIN: "After costs, the expected value does not clear the required margin.",
    Reason.CONSERVATIVE_EV_NEGATIVE: "At the cautious end of the estimate, the trade loses money.",
    Reason.DIP_DETECTED: "The price dropped enough to start an evaluation (not an approval).",
    Reason.DIP_NOT_PERSISTENT: "The price drop has not lasted long enough yet.",
    Reason.COOLDOWN: "A paper order on this contract was placed recently.",
    Reason.TEAM_CAP_REACHED: "The per-team spending cap (including fees) is used up.",
    Reason.GAME_CAP_REACHED: "The per-game cap is used up.",
    Reason.PORTFOLIO_CAP_REACHED: "The portfolio exposure cap is used up.",
    Reason.DAILY_LIMIT_REACHED: "Today's spending limit is used up.",
    Reason.INSUFFICIENT_CASH: "Not enough settled cash above the reserve.",
    Reason.ALL_GATES_PASSED: "Every data, model, value and risk check passed.",
    Reason.DUPLICATE_ALERT: "This exact signal was already acted on.",
    Reason.FILL_RECHECK_FAILED: "Conditions changed before the simulated fill, so no fill.",
    Reason.NO_FILL: "No contracts were available within the price limit.",
    Reason.PARTIAL_FILL: "Only part of the requested size was available.",
}


def explain(reasons) -> list[str]:
    return [f"{Reason(r).value}: {TEXT.get(Reason(r), '')}" for r in reasons]
