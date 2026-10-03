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

# Paper workflow ledger. ``mode`` + ``data_label`` + ``run_id`` keep replay/demo
# records isolated from live paper records.
paper_orders = _record_table(
    "paper_orders", "order_id",
    Column("run_id", String(40), nullable=False, index=True),
    Column("mode", String(8), nullable=False, index=True),
    Column("data_label", String(32), nullable=False),
    Column("status", String(16), nullable=False),
)
ledger_events = _record_table(
    "ledger_events", "event_key",
    Column("run_id", String(40), nullable=False, index=True),
    Column("mode", String(8), nullable=False, index=True),
    Column("data_label", String(32), nullable=False),
    Column("kind", String(24), nullable=False),
)

# Customer accounts. Mutable documents (preferences, watchlist, subscription) live in
# user_docs; every change is also written to audit_log. Billing events are stored by
# provider event id so each is processed at most once.
from sqlalchemy import Boolean, PrimaryKeyConstraint  # noqa: E402

users = Table(
    "users", metadata,
    Column("user_id", String(40), primary_key=True),
    Column("email", String(260), nullable=False, unique=True),
    Column("password_hash", String(200), nullable=False),
    Column("role", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
)
user_docs = Table(
    "user_docs", metadata,
    Column("user_id", String(40), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("doc", J, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    PrimaryKeyConstraint("user_id", "kind"),
)
billing_events = Table(
    "billing_events", metadata,
    Column("event_id", String(120), primary_key=True),
    Column("kind", String(80), nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("processed", Boolean, nullable=False, default=True),
    Column("received_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
audit_log = Table(
    "audit_log", metadata,
    Column("pk", BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
           autoincrement=True),
    Column("t", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("user_id", String(40), index=True),
    Column("action", String(60), nullable=False),
    Column("detail", J, nullable=False),
)
