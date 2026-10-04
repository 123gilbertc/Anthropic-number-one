"""Canonical, typed, versioned records.

Every record is immutable (``frozen=True``). State changes create new records
with new IDs, so a decision can always point at exactly what it saw.

Timestamps (all timezone-aware UTC, ``None`` means *unknown*, never "now"):

* ``event_time``      when the thing happened in the world (provider's claim)
* ``published_time``  when the provider says it published the message
* ``received_time``   when our process received it
* ``decision_time``   when our code made a decision using it
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sports_edge.domain.enums import (
    Action,
    ModelStatus,
    Outcome,
    Reason,
    SettlementRule,
    SourceStatus,
    Sport,
    Venue,
)

SCHEMA_VERSION = 1


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = SCHEMA_VERSION


def stable_id(prefix: str, payload: Any) -> str:
    """Deterministic content hash so identical inputs produce identical IDs (replay)."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


# --------------------------------------------------------------------------- games


class Game(Record):
    game_id: str
    sport: Sport
    season: str
    home_team: str
    away_team: str
    scheduled_start: datetime
    status: Literal["SCHEDULED", "LIVE", "FINAL", "POSTPONED", "CANCELLED", "SUSPENDED"]
    doubleheader_game_number: int | None = None  # MLB: 1 or 2 when applicable
    source: str
    # Multi-sport fields. Tennis uses home_team/away_team for player 1 / player 2.
    competition: str | None = None  # canonical competition id, e.g. "NHL", "ATP-SHANGHAI"
    participant_kind: Literal["TEAM", "PLAYER"] = "TEAM"
    names: dict[str, str] = Field(default_factory=dict)  # participant id -> display name
    round: str | None = None  # tournament round / draw, e.g. "R32", "Q2"
    venue_name: str | None = None
    # Sport-specific schedule facts from the provider (tennis format and surface, NFL
    # season type, MLB park). Absent keys are UNKNOWN; models abstain on missing ones.
    details: dict[str, Any] = Field(default_factory=dict)


class MarketMapping(Record):
    """Links one tradable contract to one game and one selection.

    ``settlement_rule`` must be copied from the venue's own rules text, and
    ``rules_text_hash`` lets us detect if the venue edits those rules later.
    """

    mapping_id: str
    game_id: str
    venue: Venue
    contract_id: str  # e.g. Kalshi market ticker
    selection_team: str  # the team the YES side pays out on
    settlement_rule: SettlementRule
    rules_text_hash: str | None
    tick_size: Decimal
    min_quantity: int = 1
    verified_by: str  # human or procedure that checked the mapping
    listed_pitchers: tuple[str, str] | None = None  # MLB_LISTED_PITCHERS only


# ------------------------------------------------------------------- raw + state


class RawEvent(Record):
    raw_id: str
    source: str
    source_status: SourceStatus
    kind: str
    provider_event_id: str | None
    provider_seq: int | None
    event_time: datetime | None
    published_time: datetime | None
    received_time: datetime
    payload: dict[str, Any]
    retain_payload: bool  # False when provider terms do not permit storage


class GameStateBase(Record):
    """Fields every sport's state carries. The trigger engine reads only these.

    ``coherent()`` and ``material_key()`` are sport-specific: the engine blocks on an
    incoherent state, and the paper broker refuses a fill if the material key changed
    between decision and fill.
    """

    snapshot_id: str
    game_id: str
    source: str
    source_status: SourceStatus
    as_of_event_time: datetime | None
    as_of_received_time: datetime
    last_material_event_time: datetime | None
    last_material_event_kind: str | None
    applied_seq: int | None
    is_final: bool = False
    in_review: bool = False
    pending_reconciliation: tuple[str, ...] = ()

    def coherent(self) -> bool:
        return True

    def material_key(self) -> tuple:
        return (self.last_material_event_time, self.last_material_event_kind, self.is_final)


class NHLState(GameStateBase):
    """Immutable NHL game-state snapshot. Unknown values are ``None``."""

    period: int  # 1-3 regulation, 4 = OT, 5 = shootout
    seconds_remaining_in_period: int | None
    home_score: int
    away_score: int
    home_skaters: int | None
    away_skaters: int | None
    home_goalie: str | None
    away_goalie: str | None
    home_net_empty: bool | None
    away_net_empty: bool | None
    final_decided_in: Literal["REG", "OT", "SO"] | None = None

    def coherent(self) -> bool:
        from sports_edge.ingest.nhl_state import coherent
        return coherent(self)

    def material_key(self) -> tuple:
        return (self.home_score, self.away_score, self.period >= 4,
                self.last_material_event_time, self.last_material_event_kind,
                self.home_skaters, self.away_skaters, self.home_goalie, self.away_goalie,
                self.home_net_empty, self.away_net_empty)


class BookLevel(Record):
    price: Decimal  # dollars per contract, 0 < price < 1
    quantity: int


class OrderBookSnapshot(Record):
    """Executable view for buying the YES side of ``contract_id``.

    ``asks`` are what we can buy YES at (ascending). On Kalshi these are
    derived from resting NO bids: ask_yes = 1 - bid_no.
    """

    book_id: str
    venue: Venue
    contract_id: str
    source_status: SourceStatus
    received_time: datetime
    exchange_time: datetime | None
    seq: int | None
    valid: bool
    market_open: bool
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def spread(self) -> Decimal | None:
        if self.best_ask is None or self.best_bid is None:
            return None
        return self.best_ask - self.best_bid


class SportsbookQuote(Record):
    quote_id: str
    game_id: str
    book: str  # e.g. "pinnacle"
    market: Literal["h2h", "h2h_3_way"]
    settlement_rule: SettlementRule
    prices_american: dict[str, int]  # outcome name -> American odds, all outcomes
    provider_last_update: datetime | None  # bookmaker last_update from the aggregator
    received_time: datetime
    source: str


# ------------------------------------------------------------- features / model


class FeatureVector(Record):
    feature_id: str
    snapshot_id: str
    feature_version: str
    values: dict[str, float | None]
    missing: tuple[str, ...]


class ModelVersion(Record):
    model_version: str
    kind: str
    status: ModelStatus
    feature_version: str
    uses_market_inputs: bool
    trained_on: str | None  # data description incl. date range, or None
    train_range: tuple[str, str] | None
    validation_range: tuple[str, str] | None
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    calibration: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None


class Prediction(Record):
    prediction_id: str
    game_id: str
    snapshot_id: str
    selection_team: str
    settlement_rule: SettlementRule
    model_version: str
    model_status: ModelStatus
    feature_version: str
    probability: float
    probability_low: float  # conservative bound used for conservative EV
    probability_high: float
    reliability: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    created_time: datetime
    valid_until: datetime
    # P(the contract is voided/refunded), e.g. an NFL tie under a tie-void rule. The
    # pay-out probability is ``probability``; the loss probability is the remainder.
    probability_void: float = 0.0


# ------------------------------------------------------------ decisions & money


class EVBreakdown(Record):
    quantity: int
    entry_cost: Decimal  # dollars, from modeled fills (includes spread/slippage)
    expected_fees: Decimal
    probability: float
    probability_low: float
    ev_point: Decimal  # dollars
    ev_conservative: Decimal  # dollars
    ev_point_cents_per_contract: Decimal
    ev_point_return_pct: Decimal  # ev / (cost + fees) * 100
    edge_probability_points: float  # (p - all-in price per contract) * 100


class Decision(Record):
    decision_id: str
    dedupe_key: str
    game_id: str
    venue: Venue | None
    contract_id: str | None
    selection_team: str | None
    settlement_rule: SettlementRule | None
    snapshot_id: str | None
    book_id: str | None
    reference_quote_ids: tuple[str, ...]
    prediction_id: str | None
    feature_version: str | None
    model_version: str | None
    strategy_version: str
    action: Action
    reasons: tuple[Reason, ...]
    ev: EVBreakdown | None
    planned_quantity: int = 0  # contracts the decision intends to buy (approvals only)
    planned_cost: Decimal = Decimal("0")  # modeled executable cost for planned_quantity
    max_eligible_addition: Decimal  # dollars of paper exposure still allowed
    decision_time: datetime
    expires_at: datetime
    notes: tuple[str, ...] = ()


class PaperFill(Record):
    fill_id: str
    decision_id: str
    contract_id: str
    selection_team: str
    game_id: str
    requested_quantity: int
    filled_quantity: int
    cost: Decimal
    fees: Decimal
    fill_time: datetime
    book_id: str
    assumptions: tuple[str, ...]


class Position(Record):
    game_id: str
    contract_id: str
    selection_team: str
    contracts: int
    total_cost: Decimal  # sum of fill costs, excluding fees
    total_fees: Decimal

    @property
    def average_entry(self) -> Decimal | None:
        """Quantity-weighted: total entry spend / total contracts. Fees excluded."""
        if self.contracts == 0:
            return None
        return self.total_cost / self.contracts

    @property
    def average_entry_all_in(self) -> Decimal | None:
        if self.contracts == 0:
            return None
        return (self.total_cost + self.total_fees) / self.contracts


class Settlement(Record):
    settlement_id: str
    game_id: str
    contract_id: str
    selection_team: str
    outcome: Outcome
    settled_time: datetime
    detail: str


class LLMReviewRecord(Record):
    review_id: str
    provider: str
    model_id: str
    decision_id: str
    snapshot_id: str
    status: Literal["OK", "INVALID_SCHEMA", "INVALID_EVIDENCE", "STALE", "TIMEOUT", "ERROR"]
    output: dict[str, Any] | None
    latency_ms: int | None
    cost_usd: Decimal | None
    created_time: datetime
    decision_weight: float = 0.0  # shadow mode: always 0 until promoted by evidence
