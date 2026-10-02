"""SQL sink: persists canonical records via SQLAlchemy Core (PostgreSQL in practice)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, insert, select

from sports_edge.domain.records import (
    Decision,
    Game,
    MarketMapping,
    ModelVersion,
    NHLState,
    PaperFill,
    Prediction,
    RawEvent,
    Record,
    Settlement,
    SportsbookQuote,
)
from sports_edge.storage import tables as t


def make_engine(url: str) -> Engine:
    return create_engine(url, future=True, pool_pre_ping=True)


def _row(rec: Record, **cols) -> dict:
    return {**cols, "schema_version": rec.schema_version, "record": rec.model_dump(mode="json")}


class SqlSink:
    """Implements the Monitor ``Sink`` protocol. Append-only."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _put(self, table, row: dict) -> None:
        with self.engine.begin() as c:
            c.execute(insert(table).values(**row))

    def raw(self, r: RawEvent) -> None:
        if not r.retain_payload:
            r = r.model_copy(update={"payload": {}})
        self._put(t.raw_events, _row(r, raw_id=r.raw_id, source=r.source, kind=r.kind,
                                     event_time=r.event_time, published_time=r.published_time,
                                     received_time=r.received_time))

    def state(self, s: NHLState) -> None:
        self._put(t.game_states, _row(s, snapshot_id=s.snapshot_id, game_id=s.game_id,
                                      received_time=s.as_of_received_time))

    def quote(self, q: SportsbookQuote) -> None:
        self._put(t.sportsbook_quotes, _row(q, quote_id=q.quote_id, game_id=q.game_id,
                                            book=q.book,
                                            provider_last_update=q.provider_last_update,
                                            received_time=q.received_time))

    def prediction(self, p: Prediction) -> None:
        self._put(t.predictions, _row(p, prediction_id=p.prediction_id, game_id=p.game_id,
                                      model_version=p.model_version, created_time=p.created_time))

    def decision(self, d: Decision) -> None:
        self._put(t.decisions, _row(d, decision_id=d.decision_id, game_id=d.game_id,
                                    action=d.action.value, strategy_version=d.strategy_version,
                                    decision_time=d.decision_time))

    def fill(self, f: PaperFill) -> None:
        self._put(t.paper_fills, _row(f, fill_id=f.fill_id, game_id=f.game_id,
                                      decision_id=f.decision_id, fill_time=f.fill_time))

    def settlement(self, s: Settlement) -> None:
        self._put(t.settlements, _row(s, settlement_id=s.settlement_id, game_id=s.game_id,
                                      outcome=s.outcome.value))

    def game(self, g: Game) -> None:
        self._put(t.games, _row(g, game_id=g.game_id, sport=g.sport.value,
                                scheduled_start=g.scheduled_start))

    def mapping(self, m: MarketMapping) -> None:
        self._put(t.market_mappings, _row(m, mapping_id=m.mapping_id, game_id=m.game_id,
                                          contract_id=m.contract_id))

    def model_version(self, v: ModelVersion) -> None:
        self._put(t.model_versions, _row(v, model_version=v.model_version, status=v.status.value))

    # ---------------------------------------------------------------- reads

    def decisions_for(self, game_id: str) -> list[Decision]:
        with self.engine.connect() as c:
            rows = c.execute(select(t.decisions.c.record).where(t.decisions.c.game_id == game_id)
                             .order_by(t.decisions.c.pk)).all()
        return [Decision.model_validate(r[0]) for r in rows]
