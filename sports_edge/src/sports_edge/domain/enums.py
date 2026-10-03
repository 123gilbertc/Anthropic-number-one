from __future__ import annotations

from enum import StrEnum


class Sport(StrEnum):
    NHL = "NHL"
    MLB = "MLB"
    NFL = "NFL"
    TENNIS = "TENNIS"


class Venue(StrEnum):
    KALSHI = "KALSHI"
    POLYMARKET_INTL = "POLYMARKET_INTL"
    POLYMARKET_US = "POLYMARKET_US"
    SYNTHETIC = "SYNTHETIC"


class SettlementRule(StrEnum):
    """What exactly has to happen for a "team wins" contract to pay out.

    These are different products. A regulation-only NHL contract on the home
    team loses if the game goes to overtime, even if the home team then wins.
    A model that forecasts "wins the game" cannot price it.
    """

    NHL_INCLUDING_OT_SO = "NHL_INCLUDING_OT_SO"
    NHL_REGULATION_ONLY = "NHL_REGULATION_ONLY"  # three-way: home / away / tie-after-60
    MLB_FULL_GAME_INCL_EXTRAS = "MLB_FULL_GAME_INCL_EXTRAS"
    MLB_LISTED_PITCHERS = "MLB_LISTED_PITCHERS"  # void if either listed starter does not start
    # NFL regular-season games can end tied. Which of these a venue uses is UNKNOWN until
    # its rules text is read; a mapping must name one explicitly.
    NFL_INCL_OT_TIE_VOID = "NFL_INCL_OT_TIE_VOID"  # tie refunds the stake
    NFL_INCL_OT_TIE_LOSES = "NFL_INCL_OT_TIE_LOSES"  # tie: "team wins" pays nothing
    # Tennis match winner. Retirement handling differs by venue/book (UNKNOWN until read).
    TENNIS_MATCH_RETIREMENT_ADVANCER_WINS = "TENNIS_MATCH_RETIREMENT_ADVANCER_WINS"
    TENNIS_MATCH_RETIREMENT_VOID = "TENNIS_MATCH_RETIREMENT_VOID"


class SourceStatus(StrEnum):
    """Shown prominently in the dashboard. Never upgrade a status without evidence."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    REPLAY = "REPLAY"
    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"
    NOT_CONNECTED = "NOT_CONNECTED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class ModelStatus(StrEnum):
    UNTRAINED = "UNTRAINED"
    SYNTHETIC_ONLY = "SYNTHETIC_ONLY"  # fitted only on synthetic data: never alert-eligible
    UNVALIDATED = "UNVALIDATED"  # trained on real data, not yet passed holdout gates
    VALIDATED = "VALIDATED"


class Action(StrEnum):
    WATCH = "WATCH"
    CANDIDATE = "CANDIDATE"
    REVIEW = "REVIEW"
    PAPER_ENTRY = "PAPER_ENTRY"
    PAPER_ADD = "PAPER_ADD"
    HOLD = "HOLD"
    NO_ADD = "NO_ADD"
    STOP_BUYING = "STOP_BUYING"
    DATA_BLOCKED = "DATA_BLOCKED"


class Outcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    VOID = "VOID"  # stake refunded
    PENDING = "PENDING"


class Reason(StrEnum):
    """Machine-readable reason codes. Every decision carries at least one."""

    # data / mapping gates
    MAPPING_INVALID = "MAPPING_INVALID"
    SETTLEMENT_MISMATCH = "SETTLEMENT_MISMATCH"
    GAME_STATE_STALE = "GAME_STATE_STALE"
    GAME_STATE_INCOHERENT = "GAME_STATE_INCOHERENT"
    GAME_FEED_NOT_CONNECTED = "GAME_FEED_NOT_CONNECTED"
    MARKET_INACTIVE = "MARKET_INACTIVE"
    BOOK_INVALID = "BOOK_INVALID"
    BOOK_STALE = "BOOK_STALE"
    MARKET_SETTLING = "MARKET_SETTLING"  # too soon after a material event: quotes may be stale
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    DEPTH_INSUFFICIENT = "DEPTH_INSUFFICIENT"
    REFERENCE_PRE_EVENT = "REFERENCE_PRE_EVENT"
    REFERENCE_STALE = "REFERENCE_STALE"
    REFERENCE_DISAGREEMENT = "REFERENCE_DISAGREEMENT"
    TIMESTAMP_INCONSISTENT = "TIMESTAMP_INCONSISTENT"  # provider time after our receipt, etc.
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    CRITICAL_FEATURE_MISSING = "CRITICAL_FEATURE_MISSING"
    # model gates
    MODEL_NOT_VALIDATED = "MODEL_NOT_VALIDATED"
    PREDICTION_STALE = "PREDICTION_STALE"
    # value gates
    EV_BELOW_MARGIN = "EV_BELOW_MARGIN"
    CONSERVATIVE_EV_NEGATIVE = "CONSERVATIVE_EV_NEGATIVE"
    DIP_DETECTED = "DIP_DETECTED"
    DIP_NOT_PERSISTENT = "DIP_NOT_PERSISTENT"
    COOLDOWN = "COOLDOWN"
    # risk gates
    TEAM_CAP_REACHED = "TEAM_CAP_REACHED"
    GAME_CAP_REACHED = "GAME_CAP_REACHED"
    PORTFOLIO_CAP_REACHED = "PORTFOLIO_CAP_REACHED"
    DAILY_LIMIT_REACHED = "DAILY_LIMIT_REACHED"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    # outcomes
    ALL_GATES_PASSED = "ALL_GATES_PASSED"
    DUPLICATE_ALERT = "DUPLICATE_ALERT"
    FILL_RECHECK_FAILED = "FILL_RECHECK_FAILED"
    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
