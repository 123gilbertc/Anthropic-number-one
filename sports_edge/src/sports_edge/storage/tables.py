"""PostgreSQL schema (SQLAlchemy Core). Alembic migrations live in ``migrations/``.

Design: each canonical record is stored as an append-only row with its ID,
the key columns used for querying, and the full record as JSONB. Rows are
never updated in place: corrections are new rows, so the database always
shows what the system knew at the time.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
J = JSON().with_variant(JSONB(), "postgresql")


def _record_table(name: str, id_col: str, *cols: Column) -> Table:
    return Table(
        name, metadata,
        Column("pk", BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
               autoincrement=True),
        Column(id_col, String(80), nullable=False, index=True),
        *cols,
        Column("schema_version", Integer, nullable=False),
        Column("record", J, nullable=False),
        Column("inserted_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


raw_events = _record_table(
    "raw_events", "raw_id",
    Column("source", String(64), nullable=False, index=True),
    Column("kind", String(64), nullable=False),
    Column("event_time", DateTime(timezone=True)),
    Column("published_time", DateTime(timezone=True)),
    Column("received_time", DateTime(timezone=True), nullable=False, index=True),
)
game_states = _record_table(
    "game_states", "snapshot_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("received_time", DateTime(timezone=True), nullable=False),
)
sportsbook_quotes = _record_table(
    "sportsbook_quotes", "quote_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("book", String(64), nullable=False),
    Column("provider_last_update", DateTime(timezone=True)),
    Column("received_time", DateTime(timezone=True), nullable=False),
)
predictions = _record_table(
    "predictions", "prediction_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("model_version", String(80), nullable=False),
    Column("created_time", DateTime(timezone=True), nullable=False),
)
decisions = _record_table(
    "decisions", "decision_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("action", String(32), nullable=False, index=True),
    Column("strategy_version", String(80), nullable=False),
    Column("decision_time", DateTime(timezone=True), nullable=False, index=True),
)
paper_fills = _record_table(
    "paper_fills", "fill_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("decision_id", String(80), nullable=False),
    Column("fill_time", DateTime(timezone=True), nullable=False),
)
settlements = _record_table(
    "settlements", "settlement_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("outcome", String(16), nullable=False),
)
llm_reviews = _record_table(
    "llm_reviews", "review_id",
    Column("decision_id", String(80), nullable=False, index=True),
    Column("provider", String(32), nullable=False),
    Column("status", String(24), nullable=False),
)
model_versions = _record_table(
    "model_versions", "model_version",
    Column("status", String(24), nullable=False),
)
market_mappings = _record_table(
    "market_mappings", "mapping_id",
    Column("game_id", String(80), nullable=False, index=True),
    Column("contract_id", String(120), nullable=False),
)
games = _record_table(
    "games", "game_id",
    Column("sport", String(8), nullable=False),
    Column("scheduled_start", DateTime(timezone=True), nullable=False),
)
