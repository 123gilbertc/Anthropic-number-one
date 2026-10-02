"""Typed API contracts. The frontend's TypeScript types are generated from these
(``sports-edge openapi`` -> ``web/openapi.json`` -> ``npm run gen:api``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sports_edge.domain.records import Decision, Game, NHLState, PaperFill
from sports_edge.session import LedgerEvent, PaperOrder


class ContractIntel(BaseModel):
    contract_id: str
    selection: str
    settlement_rule: str
    probability: float | None
    probability_low: float | None
    probability_high: float | None
    reliability: str
    model_status: str
    abstention: str | None
    best_ask: str | None
    best_bid: str | None
    ask_depth: int | None
    book_valid: bool
    reference_age_seconds: float | None
    action: str | None
    reasons: list[str]
    notes: list[str]
    ev: dict[str, Any] | None
    remaining_paper_exposure: str
    position: dict[str, Any] | None
    decision_expires_at: str | None
    invalidation: str
    decision_id: str | None = None
    signal_eligible: bool = False


class GameIntel(BaseModel):
    game: Game
    state: NHLState | None
    model_favored_team: str | None
    value_side: str | None
    contracts: list[ContractIntel]
    as_of: datetime
    state_age_seconds: float | None
    event_seq: int


class SessionInfo(BaseModel):
    run_id: str
    mode: Literal["REPLAY", "LIVE"]
    data_label: str
    as_of: str
    position: int
    total: int
    running: bool
    speed: float
    finished: bool
    model: dict[str, Any] | None
    auto_paper: bool


class Health(BaseModel):
    banners: list[str]
    session: SessionInfo
    authenticated: bool
    event_seq: int
    server_time: datetime


class PreviewRequest(BaseModel):
    game_id: str
    contract_id: str


class Preview(BaseModel):
    decision: Decision
    eligible: bool
    expired: bool
    seconds_to_expiry: float
    max_quantity: int
    estimated_cost: str
    estimated_fees: str | None
    explanation: list[str]


class OrderRequest(BaseModel):
    decision_id: str
    idempotency_key: str = Field(min_length=1, max_length=100)
    quantity: int | None = Field(default=None, ge=1)


class OrderResponse(BaseModel):
    order: PaperOrder
    created: bool


class Ledger(BaseModel):
    orders: list[PaperOrder]
    events: list[LedgerEvent]
    cash: str
    open_cost: str
    limits: dict[str, str]
    fills: list[PaperFill]


class ApiError(BaseModel):
    code: str
    detail: str
