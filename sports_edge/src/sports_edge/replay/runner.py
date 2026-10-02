"""Deterministic replay.

Replays a recorded (or synthetic) JSONL stream through exactly the same
``Monitor`` used live, with a ``ReplayClock`` that advances to each line's
receipt time. Only information received by that time is visible.

Running the same file twice must produce the same decision digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sports_edge.adapters.kalshi import KalshiBookManager
from sports_edge.clock import ReplayClock
from sports_edge.domain.enums import ModelStatus, SettlementRule, SourceStatus
from sports_edge.domain.records import Game, MarketMapping, SportsbookQuote, stable_id
from sports_edge.forecast.models import ChainForecaster
from sports_edge.health import SourceHealth
from sports_edge.ingest.nhl_state import NormalizedGameEvent
from sports_edge.monitor import MemorySink, Monitor
from sports_edge.paper.broker import PaperBroker
from sports_edge.pricing.fees import FeeModel, KalshiQuadraticFee
from sports_edge.risk.exposure import ExposureLedger, RiskLimits
from sports_edge.triggers.engine import USABLE_STATUSES, TriggerEngine
from sports_edge.triggers.strategy import StrategyConfig, provisional_dip_strategy


def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class ReplayResult:
    monitor: Monitor
    sink: MemorySink
    data_label: str
    status: SourceStatus
    digest: str

    def summary(self) -> dict:
        s = self.sink
        actions: dict[str, int] = {}
        for d in s.decisions:
            actions[d.action.value] = actions.get(d.action.value, 0) + 1
        return {
            "data_label": self.data_label,
            "source_status": self.status.value,
            "events": len(s.raws),
            "states": len(s.states),
            "decisions_logged": len(s.decisions),
            "actions": actions,
            "alerts": len(self.monitor.alerts),
            "fills": [f.model_dump(mode="json") for f in s.fills],
            "settlements": [x.model_dump(mode="json") for x in s.settlements],
            "cash_after": str(self.monitor.engine.ledger.cash),
            "decision_digest": self.digest,
        }


def build_monitor(clock, *, forecaster: ChainForecaster | None, cfg: StrategyConfig | None = None,
                  limits: RiskLimits | None = None, fee_model: FeeModel | None = None,
                  status: SourceStatus = SourceStatus.REPLAY,
                  mechanics_demo: bool = False) -> Monitor:
    cfg = cfg or provisional_dip_strategy()
    ledger = ExposureLedger(limits or RiskLimits.example_1000_bankroll_100_cap())
    usable = set(USABLE_STATUSES)
    allowed = {ModelStatus.VALIDATED}
    label = None
    if mechanics_demo:
        # Explicit opt-in so synthetic data + synthetic-only models can exercise the
        # full path. Every decision is labelled; nothing here is evidence of value.
        usable.add(SourceStatus.SYNTHETIC_DEMO)
        allowed.add(ModelStatus.SYNTHETIC_ONLY)
        label = "MECHANICS DEMO: synthetic data and a synthetic-only model. Not evidence."
    engine = TriggerEngine(cfg, fee_model or KalshiQuadraticFee(), ledger, frozenset(usable),
                           frozenset(allowed), label)
    return Monitor(clock=clock, engine=engine, broker=PaperBroker(engine), forecaster=forecaster,
                   books=KalshiBookManager(source_status=status))


def replay_file(path: Path, *, forecaster: ChainForecaster | None, mechanics_demo: bool = False,
                cfg: StrategyConfig | None = None, limits: RiskLimits | None = None,
                use_references: bool = True) -> ReplayResult:
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    meta = lines[0]
    if meta.get("stream") != "meta":
        raise ValueError("first line must be meta")
    label = meta.get("data_label", "UNKNOWN")
    status = SourceStatus.SYNTHETIC_DEMO if label == "SYNTHETIC" else SourceStatus.REPLAY
    body = lines[1:]
    times = [x["received_time"] for x in body]
    if times != sorted(times):
        raise ValueError("replay lines must be sorted by received_time")
    game = Game.model_validate(meta["game"])
    mappings = [MarketMapping.model_validate(m) for m in meta["mappings"]]
    rule = mappings[0].settlement_rule
    clock = ReplayClock(_t(body[0]["received_time"]) or game.scheduled_start)
    mon = build_monitor(clock, forecaster=forecaster, cfg=cfg or provisional_dip_strategy(rule),
                        limits=limits, status=status, mechanics_demo=mechanics_demo)
    mon.use_references = use_references
    mon.add_game(game, mappings, meta.get("pregame_prob"),
                 {k: Decimal(v) for k, v in (meta.get("anchor_price") or {}).items()})
    for name, kind, stale in (("replay_game", "game_feed", 60), ("replay_market", "market", 30),
                              ("replay_odds", "reference", 120)):
        mon.add_source(SourceHealth(name, kind, status, timedelta(seconds=stale),
                                    note=f"{label} replay"))
    for x in body:
        rt = _t(x["received_time"])
        assert rt is not None
        clock.advance_to(rt)
        if x["stream"] == "game":
            e = x["event"]
            mon.on_game_event(NormalizedGameEvent(
                game_id=game.game_id, source="replay_game", source_status=status, type=e["type"],
                provider_event_id=e.get("provider_event_id"), seq=e.get("seq"),
                event_time=_t(e.get("event_time")), published_time=_t(e.get("published_time")),
                received_time=rt, data=e.get("data", {})))
        elif x["stream"] == "kalshi":
            mon.on_market_message(x["raw"], rt, source="replay_market")
        elif x["stream"] == "odds":
            q = x["quote"]
            mon.on_reference(SportsbookQuote(
                quote_id=stable_id("q", [q, x["received_time"]]), game_id=game.game_id,
                book=q["book"], market=q.get("market", "h2h"),
                settlement_rule=SettlementRule(q.get("settlement_rule", rule.value)),
                prices_american=q["prices_american"],
                provider_last_update=_t(q.get("provider_last_update")), received_time=rt,
                source="replay_odds"))
        mon.tick()
    sink = mon.sink
    assert isinstance(sink, MemorySink)
    digest = hashlib.sha256("\n".join(
        f"{d.decision_time.isoformat()}|{d.contract_id}|{d.action.value}|"
        f"{','.join(r.value for r in d.reasons)}|{d.ev.ev_point if d.ev else ''}"
        for d in sink.decisions).encode()).hexdigest()
    return ReplayResult(mon, sink, label, status, digest)
