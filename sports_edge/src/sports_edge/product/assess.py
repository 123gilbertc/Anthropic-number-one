"""Customer-facing assessments built from the backend's own state, models and books.

Answers three questions per game, in this order:
1. Who is most likely to win from here?        (model probability, with band and status)
2. Is the available price attractive after costs and uncertainty?   (decision panel)
3. Why, and what could change it?               (evidence: explain.py)

"Most likely winner" and "attractive price" are different concepts. The value side
may be NONE. Nothing here is a recommendation to buy; there is no real-money action.

Customer states (from the trigger engine's actions):
  PAPER ENTRY ELIGIBLE  PAPER_ENTRY / PAPER_ADD: every gate passed, a paper entry may be previewed
  VALUE CANDIDATE       REVIEW: value gates passed, but something needs a human look
  WATCH                 CANDIDATE (a dip is forming) or no current trigger
  HOLD                  HOLD: cooldown / duplicate
  NO ADD                NO_ADD / STOP_BUYING: price, size or risk limits say no
  DATA UNAVAILABLE      DATA_BLOCKED or no usable forecast
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sports_edge.contracts.settlement import MappingError, validate_mapping
from sports_edge.domain.enums import Action, ModelStatus, Reason, SettlementRule
from sports_edge.domain.explain import TEXT
from sports_edge.domain.records import Prediction
from sports_edge.pricing.ev import expected_value
from sports_edge.pricing.fills import max_quantity_within_budget, walk_asks
from sports_edge.product.explain import INVALIDATING_EVENTS, NOT_CONNECTED, sensitivity
from sports_edge.product.formats import price_formats, probability_formats
from sports_edge.sports.base import adapter_for
from sports_edge.triggers.alignment import check_book, check_state

STATE_OF_ACTION = {
    Action.PAPER_ENTRY: "PAPER ENTRY ELIGIBLE", Action.PAPER_ADD: "PAPER ENTRY ELIGIBLE",
    Action.REVIEW: "VALUE CANDIDATE", Action.CANDIDATE: "WATCH", Action.WATCH: "WATCH",
    Action.HOLD: "HOLD", Action.NO_ADD: "NO ADD", Action.STOP_BUYING: "NO ADD",
    Action.DATA_BLOCKED: "DATA UNAVAILABLE",
}
FORECAST_GAPS = {Reason.MODEL_NOT_VALIDATED, Reason.PREDICTION_STALE,
                 Reason.CRITICAL_FEATURE_MISSING}

RULE_TEXT = {
    SettlementRule.NHL_INCLUDING_OT_SO: "Pays $1 if the team wins, including overtime and "
                                        "shootout.",
    SettlementRule.NHL_REGULATION_ONLY: "Pays $1 only if the team wins in regulation.",
    SettlementRule.MLB_FULL_GAME_INCL_EXTRAS: "Pays $1 if the team wins, including extra "
                                              "innings.",
    SettlementRule.MLB_LISTED_PITCHERS: "Pays $1 if the team wins; void if a listed starting "
                                        "pitcher does not start.",
    SettlementRule.NFL_INCL_OT_TIE_VOID: "Pays $1 if the team wins (overtime included); a "
                                         "tie refunds the contract cost.",
    SettlementRule.NFL_INCL_OT_TIE_LOSES: "Pays $1 if the team wins (overtime included); a "
                                          "tie pays nothing.",
    SettlementRule.TENNIS_MATCH_RETIREMENT_ADVANCER_WINS: "Pays $1 if the player wins the "
                                                          "match; a retirement counts for the "
                                                          "player who advances.",
    SettlementRule.TENNIS_MATCH_RETIREMENT_VOID: "Pays $1 if the player wins the match; a "
                                                 "retirement voids the contract.",
}


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


def _reasons(codes) -> list[dict]:
    return [{"code": getattr(c, "value", c), "text": TEXT.get(Reason(getattr(c, "value", c)),
                                                              str(c))} for c in codes]


def customer_state(action: Action | None, reasons: tuple, has_prediction: bool) -> str:
    if action is None:
        return "DATA UNAVAILABLE"
    if action == Action.WATCH and (set(reasons) & FORECAST_GAPS or not has_prediction):
        return "DATA UNAVAILABLE"
    return STATE_OF_ACTION[action]


def board_label(contracts: list[dict], any_forecast: bool, has_market: bool) -> str:
    """VALUE CANDIDATE / WATCH (a price dip is forming) / NO CURRENT EDGE / UNAVAILABLE."""
    states = [c["state"] for c in contracts]
    if not has_market or not any_forecast:
        return "UNAVAILABLE"
    if any(s in ("PAPER ENTRY ELIGIBLE", "VALUE CANDIDATE") for s in states):
        return "VALUE CANDIDATE"
    if all(s == "DATA UNAVAILABLE" for s in states):
        return "UNAVAILABLE"
    if any(c["action"] == Action.CANDIDATE.value or
           any(r["code"] == Reason.DIP_DETECTED.value for r in c["reasons"]) for c in contracts):
        return "WATCH"
    return "NO CURRENT EDGE"


def break_even_price(p_low: float, fee_model, margin_cents: Decimal) -> Decimal | None:
    """Highest ask (1 contract) at which the *cautious* estimate still clears the margin."""
    best = None
    for c in range(1, 100):
        px = Decimal(c) / 100
        fee = fee_model.entry_fee(1, px)
        if Decimal(str(p_low)) - px - fee - margin_cents / 100 > 0:
            best = px
    return best


def ev_text(ev: dict, stake: Decimal) -> str:
    q = ev["quantity"]
    return (f"If the estimate is right, a ${stake:.0f} paper entry ({q} contracts) would average "
            f"{_money(ev['ev_point'])} per entry over many situations like this one; with the "
            f"cautious estimate it averages {_money(ev['ev_conservative'])}. This is an "
            f"average implied by the estimate, not a promised profit on this game.")


def _money(x) -> str:
    v = float(x)
    return f"{'+' if v >= 0 else '−'}${abs(v):.2f}"


class Assessor:
    """Reads a Monitor (+ LineHistory) and produces customer views. No side effects."""

    def __init__(self, monitor, history, stake: Decimal | None = None) -> None:
        self.mon = monitor
        self.hist = history
        self.stake = stake or monitor.engine.cfg.max_order_dollars

    # ---------------------------------------------------------------- per contract

    def contract_view(self, rt, m, now: datetime) -> dict:
        mon, eng = self.mon, self.mon.engine
        # current evaluation (pure read): a stored decision can be stale once a time-based
        # gate (post-event settling, cooldown, dip persistence) has lapsed
        st0 = rt.reducer.state
        d = None
        if not (st0 is not None and st0.is_final) and (rt.reducer.state is not None or
                                                       rt.mappings):
            d = mon.preview(rt.game.game_id, m.contract_id, register=False)
        d = d or rt.last_decision.get(m.contract_id)
        ctx = mon.context(rt, m)
        pred: Prediction | None = ctx.prediction
        book = ctx.book
        has_pred = pred is not None and pred.model_status in eng.allowed_model_statuses
        action = d.action if d else None
        reasons = d.reasons if d else ()
        state = customer_state(action, reasons, pred is not None)
        entry = ev = None
        if book is not None and book.valid and book.asks:
            qty = max_quantity_within_budget(book.asks, self.stake, eng.fee_model,
                                             depth_haircut=eng.cfg.depth_haircut)
            if qty:
                fill = walk_asks(book.asks, qty, eng.fee_model, depth_haircut=eng.cfg.depth_haircut)
                entry = {"stake": str(self.stake), "quantity": fill.filled,
                         "average_price": str((fill.cost / fill.filled).quantize(Decimal("0.0001"))),
                         "cost": str(fill.cost), "entry_fees": str(fill.fees),
                         "all_in": str(fill.cost + fill.fees),
                         "depth_haircut": str(eng.cfg.depth_haircut),
                         "note": "Walks the recorded ask ladder for the stated paper amount, "
                                 "assuming only part of displayed size is reachable."}
                if pred is not None:
                    e = expected_value(fill, pred.probability, pred.probability_low,
                                       eng.fee_model, pred.probability_void)
                    ev = e.model_dump(mode="json")
                    ev["plain_english"] = ev_text(ev, self.stake)
        readiness = self._readiness(rt, m, ctx, now)
        be = None
        if pred is not None:
            be = break_even_price(pred.probability_low, eng.fee_model,
                                  eng.cfg.min_conservative_ev_cents_per_contract)
        name = rt.game.names.get(m.selection_team, m.selection_team)
        return {
            "contract_id": m.contract_id, "participant": m.selection_team, "name": name,
            "venue": m.venue.value, "settlement_rule": m.settlement_rule.value,
            "settlement_text": RULE_TEXT.get(m.settlement_rule, m.settlement_rule.value),
            "model": None if pred is None else {
                "probability": pred.probability, "low": pred.probability_low,
                "high": pred.probability_high, "void_probability": pred.probability_void,
                "status": pred.model_status.value, "model_version": pred.model_version,
                "as_of": _iso(pred.created_time), "valid_until": _iso(pred.valid_until),
                "fair_odds": probability_formats(pred.probability)},
            "abstention": rt.abstentions.get(m.contract_id),
            "market": {"source": f"{m.venue.value} order book", "valid": bool(book and book.valid),
                       "bid": None if not book or book.best_bid is None else str(book.best_bid),
                       "ask": None if not book or book.best_ask is None else str(book.best_ask),
                       "ask_size": book.asks[0].quantity if book and book.asks else None,
                       "bid_size": book.bids[0].quantity if book and book.bids else None,
                       "as_of": _iso(book.received_time) if book else None,
                       "ask_formats": price_formats(book.best_ask) if book else None},
            "entry": entry, "ev": ev,
            "break_even_ask": None if be is None else str(be),
            "state": state, "action": action.value if action else None,
            "reasons": _reasons(reasons), "notes": list(d.notes) if d else [],
            "readiness": readiness,
            "ready": all(r["ok"] for r in readiness),
            "decision_id": d.decision_id if d else None,
            "decision_time": _iso(d.decision_time) if d else None,
            "expires_at": _iso(d.expires_at) if d else None,
            "eligible": bool(d and d.action in (Action.PAPER_ENTRY, Action.PAPER_ADD)
                             and now <= d.expires_at),
            "has_usable_forecast": has_pred,
            "line": self.hist.summary(m.contract_id, now) if self.hist else None,
        }

    def _readiness(self, rt, m, ctx, now: datetime) -> list[dict]:
        eng = self.mon.engine
        cfg = eng.cfg
        out = []
        st = ctx.state
        if ctx.is_pregame:
            out.append({"check": "Game state", "ok": True, "detail": "pregame"})
        else:
            rs = check_state(st, now, cfg, ctx.game_last_seen)
            age = None if st is None else (now - st.as_of_received_time).total_seconds()
            out.append({"check": "Game state current and reconciled", "ok": not rs,
                        "detail": ", ".join(r.value for r in rs) or f"updated {age:.0f}s ago"})
        rb = check_book(ctx.book, now, cfg, ctx.market_last_seen)
        out.append({"check": "Order book valid and current", "ok": not rb,
                    "detail": ", ".join(r.value for r in rb) or "valid"})
        try:
            validate_mapping(m, rt.game, cfg.rule_for(rt.game.sport))
            out.append({"check": "Contract rules match the forecast", "ok": True,
                        "detail": m.settlement_rule.value})
        except MappingError as e:
            out.append({"check": "Contract rules match the forecast", "ok": False,
                        "detail": str(e)})
        p = ctx.prediction
        ok = p is not None and p.model_status in eng.allowed_model_statuses
        out.append({"check": "Model allowed to support entries", "ok": ok,
                    "detail": "no forecast" if p is None else p.model_status.value +
                    ("" if ok else " (not allowed)")})
        ev_t = st.last_material_event_time if st is not None else None
        settling = ev_t is not None and now - ev_t < cfg.post_event_settle
        out.append({"check": "Market has settled after the last event", "ok": not settling,
                    "detail": f"last event {(now - ev_t).total_seconds():.0f}s ago"
                    if ev_t else "no material event yet"})
        room, _ = eng.ledger.remaining(rt.game.game_id, m.selection_team, now.date())
        out.append({"check": "Paper risk room left", "ok": room > 0, "detail": f"${room:.2f}"})
        return out

    # ---------------------------------------------------------------- per game

    def game(self, game_id: str, now: datetime, with_evidence: bool = True) -> dict:
        mon = self.mon
        rt = mon.games[game_id]
        g = rt.game
        st = rt.reducer.state
        contracts = [self.contract_view(rt, m, now) for m in rt.mappings]
        probs = {c["participant"]: c["model"] for c in contracts if c["model"]}
        likely = None
        if probs:
            fav = max(probs, key=lambda k: probs[k]["probability"])
            likely = {"participant": fav, "name": g.names.get(fav, fav), **probs[fav],
                      "available": True}
        elif rt.mappings:
            reason = next((c["abstention"] for c in contracts if c["abstention"]),
                          "No forecast for this game yet")
            likely = {"available": False, "reason": reason}
        else:
            likely = {"available": False, "reason": "No market is mapped to this game, so "
                                                    "no forecast is published for it."}
        value = [c for c in contracts if c["state"] in ("PAPER ENTRY ELIGIBLE",
                                                        "VALUE CANDIDATE")]
        value_side = value[0] if value else None
        states = [c["state"] for c in contracts]
        label = board_label(contracts, bool(probs), bool(rt.mappings))
        if st is not None and st.is_final:
            label = "FINAL"
        sb = adapter_for(g.sport).scoreboard(st, g)
        status = "FINAL" if st is not None and st.is_final else \
            "LIVE" if st is not None else ("UPCOMING" if now < g.scheduled_start else "STARTED?")
        if status == "STARTED?":
            status = "AWAITING DATA"  # scheduled start passed but no game state received
        out = {
            "event": {"game_id": g.game_id, "sport": g.sport.value, "competition": g.competition,
                      "participants": [g.away_team, g.home_team],
                      "names": {k: g.names.get(k, k) for k in (g.away_team, g.home_team)},
                      "participant_kind": g.participant_kind,
                      "home": g.home_team, "away": g.away_team,
                      "scheduled_start": _iso(g.scheduled_start), "round": g.round,
                      "venue_name": g.venue_name, "details": g.details, "status": status},
            "scoreboard": sb,
            "likely_winner": likely,
            "value_side": None if value_side is None else {
                "participant": value_side["participant"], "name": value_side["name"],
                "contract_id": value_side["contract_id"], "state": value_side["state"]},
            "assessment": label,
            "contracts": contracts,
            "freshness": self._freshness(rt, now),
            "as_of": now.isoformat(),
        }
        if with_evidence:
            out["evidence"] = self.evidence(rt, contracts, now)
        return out

    def _freshness(self, rt, now: datetime) -> dict:
        st = rt.reducer.state
        mk = self.mon._last_seen("market")
        gm = self.mon._last_seen("game_feed")
        return {
            "game_state_age_s": None if st is None else
            round((now - st.as_of_received_time).total_seconds(), 1),
            "game_feed_last_seen_s": None if gm is None else round((now - gm).total_seconds(), 1),
            "market_feed_last_seen_s": None if mk is None else round((now - mk).total_seconds(), 1),
            "note": "Connection liveness (heartbeats) shows the feed is connected; it does not "
                    "mean a score or quote changed.",
            "pending_reconciliation": list(st.pending_reconciliation) if st else [],
        }

    # ---------------------------------------------------------------- evidence

    def evidence(self, rt, contracts: list[dict], now: datetime) -> dict:
        g = rt.game
        st = rt.reducer.state
        target = next((c for c in contracts if c["state"] in ("PAPER ENTRY ELIGIBLE",
                                                              "VALUE CANDIDATE")), None)
        if target is None:
            withp = [c for c in contracts if c["model"]]
            target = max(withp, key=lambda c: c["model"]["probability"]) if withp else None
        f = self.mon.forecaster_for(g.sport)
        drivers: list[dict] = []
        if target is not None and f is not None:
            m = next(x for x in rt.mappings if x.contract_id == target["contract_id"])
            ctx = self.mon.context(rt, m)
            if ctx.prediction is not None:
                drivers = sensitivity(f, g, st, m.selection_team,
                                      rt.pregame_prob.get(m.selection_team),
                                      self.mon.engine.cfg.rule_for(g.sport), now,
                                      ctx.prediction)
        supporting = [d for d in drivers if d.get("available") and d["delta_pp"] > 0.5]
        contrary = [d for d in drivers if d.get("available") and d["delta_pp"] < -0.5]
        contrary_other = []
        if target is not None:
            t = target
            if t["ev"] is not None:
                fees = float(t["ev"]["expected_fees"])
                if fees:
                    contrary_other.append({"kind": "CALCULATION",
                                           "label": f"Fees take ${fees:.2f} from a "
                                                    f"${self.stake:.0f} entry"})
                if t["model"] and t["market"]["ask"] is not None and \
                        t["model"]["low"] <= float(t["market"]["ask"]):
                    contrary_other.append({
                        "kind": "MODEL",
                        "label": f"The cautious estimate ({t['model']['low'] * 100:.0f}%) is "
                                 f"not above the ask ({float(t['market']['ask']) * 100:.0f}¢)"})
            if t["model"] and t["model"]["status"] != ModelStatus.VALIDATED.value:
                contrary_other.append({"kind": "MODEL",
                                       "label": f"Model status {t['model']['status']}: no "
                                                f"out-of-sample evidence for this sport yet"})
        changed = self._changes(rt, target, now)
        missing = self._missing(rt, target)
        invalid = [f"A new event: {INVALIDATING_EVENTS[g.sport]}.",
                   f"The order book has a sequence gap or no update for "
                   f"{self.mon.engine.cfg.max_book_age.total_seconds():.0f}s.",
                   f"Game data older than {self.mon.engine.cfg.max_state_age.total_seconds():.0f}s."]
        if target is not None:
            if target["expires_at"]:
                invalid.append(f"This decision expires at {target['expires_at'][11:19]}Z.")
            if target["break_even_ask"]:
                invalid.append(f"The ask rises above {float(target['break_even_ask']) * 100:.0f}¢"
                               " (cautious estimate no longer clears costs and the margin).")
        return {
            "subject": None if target is None else {"contract_id": target["contract_id"],
                                                    "participant": target["participant"],
                                                    "name": target["name"]},
            "supporting": supporting, "contrary": contrary + contrary_other,
            "all_drivers": drivers, "changed": changed, "missing": missing,
            "invalidation": invalid,
            "ai_review": {"status": "NOT_CONFIGURED",
                          "note": "Optional AI reviews run in shadow mode with zero decision "
                                  "weight; none is configured for this session."},
            "kinds": {"OBSERVATION": "recorded from a provider feed",
                      "MODEL": "calculated by the probability model",
                      "CALCULATION": "arithmetic on recorded prices and fees",
                      "AI": "interpretation by an optional language model (shadow only)"},
        }

    def _changes(self, rt, target, now: datetime) -> dict:
        if target is None or self.hist is None:
            return {"available": False, "reason": "no forecast to compare"}
        cs = self.hist.contracts.get(target["contract_id"])
        preds = cs.preds if cs else []
        if len(preds) < 2:
            return {"available": False, "reason": "no previous valid estimate recorded"}
        cur = preds[-1]
        prev = next((p for p in reversed(preds[:-1]) if p.snapshot_id != cur.snapshot_id), None)
        if prev is None:
            return {"available": False, "reason": "no previous valid estimate recorded"}
        events = [a for a in self.hist.annotations.get(rt.game.game_id, [])
                  if prev.t <= a.t <= now and a.kind != "FORECAST_REVISION"]
        book = [o for o in cs.book if o.valid and o.ask is not None]
        ask_then = next((o.ask for o in reversed(book) if o.t <= prev.t), None)
        ask_now = book[-1].ask if book else None
        return {"available": True, "previous": {"t": prev.t.isoformat(), "p": prev.p},
                "current": {"t": cur.t.isoformat(), "p": cur.p},
                "delta_pp": round((cur.p - prev.p) * 100, 2),
                "events": [{"t": a.t.isoformat(), "label": a.label, "kind": "OBSERVATION"}
                           for a in events[-6:]],
                "ask_then": None if ask_then is None else float(ask_then),
                "ask_now": None if ask_now is None else float(ask_now)}

    def _missing(self, rt, target) -> list[dict]:
        g = rt.game
        st = rt.reducer.state
        out = []
        for m in rt.mappings:
            a = rt.abstentions.get(m.contract_id)
            if a:
                out.append({"label": f"Forecast unavailable for {g.names.get(m.selection_team, m.selection_team)}: {a}",
                            "severity": "BLOCKING"})
        if target is not None and st is not None:
            feats = adapter_for(g.sport).features(st, g, target["participant"],
                                                   rt.pregame_prob.get(target["participant"]))
            for k, v in feats.items():
                if v is None:
                    out.append({"label": f"Input unknown: {k}", "severity": "REDUCED"})
        if st is not None:
            for flag in st.pending_reconciliation:
                out.append({"label": f"Feed reconciliation pending: {flag}",
                            "severity": "BLOCKING"})
        if not rt.mappings:
            out.append({"label": "No market mapped to this game", "severity": "BLOCKING"})
        for x in NOT_CONNECTED.get(g.sport, []):
            out.append({"label": x, "severity": "NOT_MODELLED"})
        if target and target["model"] and target["model"]["status"] == "SYNTHETIC_ONLY":
            out.append({"label": "Model trained or parameterised on SYNTHETIC data only",
                        "severity": "EVIDENCE"})
        return out

    # ---------------------------------------------------------------- board

    def board_row(self, game_id: str, now: datetime) -> dict:
        v = self.game(game_id, now, with_evidence=False)
        rt = self.mon.games[game_id]
        lw = v["likely_winner"]
        fav_contract = None
        if lw.get("available"):
            fav_contract = next((c for c in v["contracts"]
                                 if c["participant"] == lw["participant"]), None)
        sel = None
        if v["value_side"]:
            sel = next(c for c in v["contracts"]
                       if c["contract_id"] == v["value_side"]["contract_id"])
        sel = sel or fav_contract or (v["contracts"][0] if v["contracts"] else None)
        edge = None
        if sel and sel["ev"] and sel["model"] and sel["model"]["status"] == "VALIDATED":
            edge = float(sel["ev"]["ev_conservative"])
        statuses = sorted({c["model"]["status"] for c in v["contracts"] if c["model"]})
        return {
            "game_id": game_id, "event": v["event"], "scoreboard": v["scoreboard"],
            "likely_winner": lw, "value_side": v["value_side"], "assessment": v["assessment"],
            "selected_market": None if sel is None else {
                "contract_id": sel["contract_id"], "participant": sel["participant"],
                "name": sel["name"], "ask": sel["market"]["ask"], "bid": sel["market"]["bid"],
                "source": sel["market"]["source"], "valid": sel["market"]["valid"],
                "state": sel["state"]},
            "sparkline": [] if sel is None or self.hist is None
            else self.hist.sparkline(sel["contract_id"]),
            "freshness": v["freshness"],
            "model_status": statuses[0] if len(statuses) == 1 else
            ("MIXED" if statuses else "NONE"),
            "validated_net_edge": edge,
            "probability": lw.get("probability") if lw.get("available") else None,
            "has_market": bool(rt.mappings),
            "forecast_ready": any(c["model"] for c in v["contracts"]),
        }
