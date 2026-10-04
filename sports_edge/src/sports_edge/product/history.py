"""Recorded line movement: what this application actually observed, and when.

Series kept per contract (all from the monitor's observer hook, never synthesized):

* book observations: best bid, best ask, sizes, midpoint, and the *size-aware entry
  price* for the stated paper amount, computed from the depth recorded at that moment;
* model estimates (probability + band, model version, snapshot);
* sportsbook reference (margin-removed), each point flagged with whether it was
  published after the game's last material event (temporally compatible);
* annotations (goals, breaks, runs, turnovers, pitching changes, forecast revisions,
  paper fills) with the evidence that produced them;
* gaps: intervals where the book was invalid (sequence gap / reconnect).

Vocabulary is deliberately strict:
* "first observed" is the first price *this application* captured. A provider's own
  opening price is shown only if the provider documents one (none does here: UNKNOWN).
* best ask, midpoint, last trade and size-aware entry price are different numbers.
  Last trade is never inferred from book changes (no trade feed is wired: UNKNOWN).
* Rendering may downsample; the recorded series is never discarded for display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge.pricing.fees import FeeModel
from sports_edge.pricing.fills import walk_asks
from sports_edge.pricing.odds import OddsError, remove_margin


@dataclass(frozen=True)
class BookObs:
    t: datetime  # our receipt time
    exchange_time: datetime | None
    bid: Decimal | None
    ask: Decimal | None
    bid_size: int | None
    ask_size: int | None
    valid: bool
    entry_price: Decimal | None  # average price for the stated paper amount (size-aware)
    entry_qty: int  # contracts that amount would buy from the recorded depth
    entry_full: bool  # False when recorded depth could not fill the amount

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class PredObs:
    t: datetime
    p: float
    lo: float
    hi: float
    model_version: str
    model_status: str
    snapshot_id: str


@dataclass(frozen=True)
class RefObs:
    t: datetime  # our receipt
    provider_time: datetime | None
    book: str
    fair: dict[str, float]  # outcome -> margin-removed probability
    compatible: bool  # published after the last material game event
    note: str


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    game_id: str
    t: datetime  # event time when known, else receipt
    received_time: datetime
    kind: str
    label: str
    evidence: dict


@dataclass
class ContractSeries:
    contract_id: str
    game_id: str
    selection: str
    name: str = ""
    book: list[BookObs] = field(default_factory=list)
    preds: list[PredObs] = field(default_factory=list)
    gaps: list[list] = field(default_factory=list)  # [start, end|None, reason]


@dataclass
class LineHistory:
    fee_model: FeeModel
    stake: Decimal = Decimal("25")
    depth_haircut: Decimal = Decimal("1")  # display uses the recorded depth as shown
    revision_pp: float = 5.0  # forecast change worth annotating (percentage points)
    contracts: dict[str, ContractSeries] = field(default_factory=dict)
    refs: dict[str, list[RefObs]] = field(default_factory=dict)  # game_id -> quotes
    annotations: dict[str, list[Annotation]] = field(default_factory=dict)
    last_material: dict[str, datetime] = field(default_factory=dict)  # game_id -> time

    def register(self, game_id: str, contract_id: str, selection: str,
                 name: str | None = None) -> None:
        self.contracts.setdefault(contract_id, ContractSeries(contract_id, game_id, selection,
                                                              name or selection))

    # ---------------------------------------------------------------- observer

    def observe(self, kind: str, payload: dict) -> None:
        if kind == "book":
            self._book(payload)
        elif kind == "prediction":
            self._prediction(payload)
        elif kind == "annotation":
            self._annotate(payload)
        elif kind == "reference":
            self._reference(payload)

    def _book(self, p: dict) -> None:
        cs = self.contracts.get(p["contract_id"])
        if cs is None:
            return
        snap = p["snapshot"]
        t = p["received_time"]
        entry_price, qty, full = None, 0, False
        if snap.valid and snap.asks:
            # whole contracts the stake buys at the recorded depth, fees included
            from sports_edge.pricing.fills import max_quantity_within_budget
            qty = max_quantity_within_budget(snap.asks, self.stake, self.fee_model,
                                             depth_haircut=self.depth_haircut)
            if qty:
                est = walk_asks(snap.asks, qty, self.fee_model, depth_haircut=self.depth_haircut)
                entry_price = (est.cost / est.filled).quantize(Decimal("0.0001"))
                depth_cost = sum(lv.price * lv.quantity for lv in snap.asks)
                full = depth_cost >= self.stake * Decimal("0.9")
        obs = BookObs(t, p.get("exchange_time"), snap.best_bid, snap.best_ask,
                      snap.bids[0].quantity if snap.bids else None,
                      snap.asks[0].quantity if snap.asks else None, snap.valid,
                      entry_price, qty, full)
        prev = cs.book[-1] if cs.book else None
        if prev is not None and (prev.bid, prev.ask, prev.bid_size, prev.ask_size,
                                 prev.valid) == (obs.bid, obs.ask, obs.bid_size,
                                                 obs.ask_size, obs.valid):
            return  # unchanged top of book: nothing new to record for display
        cs.book.append(obs)
        open_gap = cs.gaps and cs.gaps[-1][1] is None
        if not snap.valid and not open_gap:
            cs.gaps.append([t, None, "BOOK_INVALID (sequence gap or reconnect)"])
        elif snap.valid and open_gap:
            cs.gaps[-1][1] = t

    def _prediction(self, p: dict) -> None:
        cs = self.contracts.get(p["contract_id"])
        if cs is None:
            return
        pr = p["prediction"]
        prev = cs.preds[-1] if cs.preds else None
        cs.preds.append(PredObs(pr.created_time, pr.probability, pr.probability_low,
                                pr.probability_high, pr.model_version, pr.model_status.value,
                                pr.snapshot_id))
        if prev is not None and abs(pr.probability - prev.p) * 100 >= self.revision_pp:
            self._annotate({"game_id": cs.game_id, "kind": "FORECAST_REVISION",
                            "label": f"Estimate for {cs.name or cs.selection} "
                                     f"{prev.p * 100:.0f}% → {pr.probability * 100:.0f}%",
                            "event_time": pr.created_time, "received_time": pr.created_time,
                            "snapshot_id": pr.snapshot_id, "source": pr.model_version,
                            "provider_event_id": None})

    def _annotate(self, p: dict) -> None:
        gid = p["game_id"]
        lst = self.annotations.setdefault(gid, [])
        t = p.get("event_time") or p["received_time"]
        if p["kind"] not in ("FORECAST_REVISION", "PAPER_FILL", "POINT", "PLAY"):
            self.last_material[gid] = t
        lst.append(Annotation(
            annotation_id=f"an_{gid}_{len(lst) + 1}", game_id=gid, t=t,
            received_time=p["received_time"], kind=p["kind"], label=p["label"],
            evidence={k: (v.isoformat() if isinstance(v, datetime) else v)
                      for k, v in p.items() if k not in ("label",)}))

    def _reference(self, p: dict) -> None:
        q = p["quote"]
        gid = q.game_id
        try:
            fair = remove_margin(q.prices_american, set(q.prices_american))
        except OddsError:
            return
        mat = p.get("last_material_event_time")
        compatible = q.provider_last_update is not None and (
            mat is None or q.provider_last_update >= mat)
        note = "published after the last material event" if compatible else (
            "provider time unknown" if q.provider_last_update is None else
            "published BEFORE the last material event: not comparable")
        self.refs.setdefault(gid, []).append(RefObs(q.received_time, q.provider_last_update,
                                                    q.book, fair, compatible, note))

    def paper_fill(self, game_id: str, contract_id: str, t: datetime, qty: int,
                   cost: Decimal, order_id: str) -> None:
        self._annotate({"game_id": game_id, "kind": "PAPER_FILL",
                        "label": f"Paper fill: {qty} × {contract_id} for ${cost:.2f}",
                        "event_time": t, "received_time": t, "order_id": order_id,
                        "contract_id": contract_id, "provider_event_id": None})

    # ---------------------------------------------------------------- queries

    def window(self, game_id: str, now: datetime, window: str, start: datetime | None
               ) -> tuple[datetime | None, datetime]:
        if window == "all" or window == "pregame_to_live":
            return None, now
        mins = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "2h": 120}.get(window, 60)
        return now - timedelta(minutes=mins), now

    def series(self, contract_id: str, since: datetime | None, until: datetime,
               max_points: int = 600) -> dict:
        cs = self.contracts.get(contract_id)
        if cs is None:
            return {"contract_id": contract_id, "points": [], "total_points": 0}
        pts = [o for o in cs.book if (since is None or o.t >= since) and o.t <= until]
        total = len(pts)
        # the last observation before the window carries the price into it
        before = [o for o in cs.book if since is not None and o.t < since]
        if before:
            pts = [before[-1]] + pts
        shown = _downsample(pts, max_points)
        preds = [x for x in cs.preds if (since is None or x.t >= since) and x.t <= until]
        return {
            "contract_id": contract_id,
            "selection": cs.selection,
            "points": [{"t": o.t.isoformat(), "bid": _f(o.bid), "ask": _f(o.ask),
                        "mid": _f(o.mid), "entry": _f(o.entry_price), "entry_qty": o.entry_qty,
                        "entry_full": o.entry_full, "valid": o.valid,
                        "bid_size": o.bid_size, "ask_size": o.ask_size} for o in shown],
            "total_points": total,
            "downsampled": len(shown) < len(pts),
            "model": [{"t": x.t.isoformat(), "p": x.p, "lo": x.lo, "hi": x.hi,
                       "model_version": x.model_version, "status": x.model_status}
                      for x in _downsample(preds, max_points)],
            "gaps": [{"start": g[0].isoformat(), "end": g[1].isoformat() if g[1] else None,
                      "reason": g[2]} for g in cs.gaps
                     if (since is None or (g[1] or until) >= since)],
        }

    def summary(self, contract_id: str, now: datetime) -> dict:
        """First observed / current / range / changes, with insufficient-history flags."""
        cs = self.contracts.get(contract_id)
        if cs is None or not cs.book:
            return {"first_observed": None, "provider_open": None,
                    "provider_open_note": "UNKNOWN: no provider-documented opening price",
                    "current": None, "range": None, "changes": {}, "observations": 0}
        valid = [o for o in cs.book if o.valid and o.ask is not None]
        first = valid[0] if valid else None
        cur = valid[-1] if valid else None
        changes = {}
        for label, mins in (("5m", 5), ("15m", 15), ("60m", 60)):
            start = now - timedelta(minutes=mins)
            if first is None or first.t > start:
                changes[label] = {"status": "INSUFFICIENT_HISTORY"}
                continue
            ref = [o for o in valid if o.t <= start][-1]
            changes[label] = {"status": "OK", "ask_change_cents": float((cur.ask - ref.ask) * 100),
                              "from": _f(ref.ask), "to": _f(cur.ask)}
        if first is not None and cur is not None:
            changes["since_first_observed"] = {
                "status": "OK", "ask_change_cents": float((cur.ask - first.ask) * 100),
                "from": _f(first.ask), "to": _f(cur.ask)}
        asks = [o.ask for o in valid]
        return {
            "first_observed": None if first is None else {"t": first.t.isoformat(),
                                                          "ask": _f(first.ask),
                                                          "bid": _f(first.bid)},
            "provider_open": None,
            "provider_open_note": "UNKNOWN: no provider-documented opening price is wired; "
                                  "'first observed' is this application's first capture.",
            "current": None if cur is None else {"t": cur.t.isoformat(), "ask": _f(cur.ask),
                                                 "bid": _f(cur.bid), "mid": _f(cur.mid),
                                                 "entry": _f(cur.entry_price),
                                                 "entry_qty": cur.entry_qty},
            "range": None if not asks else {"ask_min": _f(min(asks)), "ask_max": _f(max(asks))},
            "changes": changes,
            "observations": len(cs.book),
            "gaps": len(cs.gaps),
            "last_trade": None,
            "last_trade_note": "UNKNOWN: no trade feed is wired; never inferred from the book.",
        }

    def sparkline(self, contract_id: str, n: int = 40) -> list[float]:
        cs = self.contracts.get(contract_id)
        if cs is None:
            return []
        asks = [float(o.ask) for o in cs.book if o.valid and o.ask is not None]
        if len(asks) <= n:
            return asks
        step = len(asks) / n
        return [asks[int(i * step)] for i in range(n - 1)] + [asks[-1]]

    def references(self, game_id: str, since: datetime | None, until: datetime) -> list[dict]:
        return [{"t": r.t.isoformat(), "provider_time": r.provider_time.isoformat()
                 if r.provider_time else None, "book": r.book, "fair": r.fair,
                 "compatible": r.compatible, "note": r.note}
                for r in self.refs.get(game_id, [])
                if (since is None or r.t >= since) and r.t <= until]

    def annotations_for(self, game_id: str, since: datetime | None, until: datetime,
                        ) -> list[dict]:
        return [{"id": a.annotation_id, "t": a.t.isoformat(),
                 "received_time": a.received_time.isoformat(), "kind": a.kind,
                 "label": a.label, "evidence": a.evidence}
                for a in self.annotations.get(game_id, [])
                if (since is None or a.t >= since) and a.t <= until]


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def _downsample(items: list, max_points: int) -> list:
    """Keep first/last and min/max per bucket so spikes survive (render limit only)."""
    if len(items) <= max_points:
        return items
    out = []
    bucket = len(items) / (max_points / 2)
    i = 0.0
    while int(i) < len(items):
        chunk = items[int(i):int(i + bucket)] or items[int(i):int(i) + 1]
        key = (lambda o: float(o.ask)) if hasattr(chunk[0], "ask") and chunk[0].ask is not None \
            else (lambda o: getattr(o, "p", 0.0))
        try:
            lo = min(chunk, key=key)
            hi = max(chunk, key=key)
        except TypeError:
            lo = hi = chunk[0]
        for o in sorted({id(lo): lo, id(hi): hi}.values(), key=lambda o: o.t):
            out.append(o)
        i += bucket
    if out[-1] is not items[-1]:
        out.append(items[-1])
    return out
