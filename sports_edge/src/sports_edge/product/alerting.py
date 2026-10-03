"""Customer alerts: watchlist-scoped, deduplicated, expiring, quiet-hours aware.

An alert tells a customer *what changed and why it may be worth reviewing*: the exact
market, the price when observed, the evidence age and an expiry. It never announces a
winner, never says "buy", never claims an order was placed, and never escalates after
losses. Pausing alerts (or quiet hours) only stops delivery: ingestion, monitoring and
settlement continue, and suppressed alerts stay visible in the inbox as suppressed.

Kinds:
  value_candidate  a system-level paper signal (all gates passed) on a game in scope
  price_move       the best ask moved by at least ``threshold_cents`` since the baseline
  game_start       the first game state arrives
  final            the game reached a final result

Delivery channel: the in-app inbox. Email and push are NOT_CONFIGURED.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

KINDS = ("value_candidate", "price_move", "game_start", "final")


def in_quiet_hours(now: datetime, tz: str, quiet: dict | None) -> bool:
    if not quiet:
        return False
    try:
        local = now.astimezone(ZoneInfo(tz)).time()
        a, b = time.fromisoformat(quiet["start"]), time.fromisoformat(quiet["end"])
    except (KeyError, ValueError):
        return False
    return (a <= local < b) if a <= b else (local >= a or local < b)


def validate_rule(rule: dict) -> dict:
    kind = rule.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    scope = rule.get("scope") or {}
    if not isinstance(scope, dict) or set(scope) - {"sport", "game_id", "participant",
                                                    "watchlist_only"}:
        raise ValueError("scope fields: sport, game_id, participant, watchlist_only")
    out = {"rule_id": rule.get("rule_id") or f"r_{uuid.uuid4().hex[:10]}", "kind": kind,
           "scope": scope, "enabled": bool(rule.get("enabled", True))}
    if kind == "price_move":
        th = rule.get("threshold_cents")
        if not isinstance(th, int | float) or not 2 <= th <= 50:
            raise ValueError("price_move needs threshold_cents between 2 and 50")
        out["threshold_cents"] = float(th)
    return out


@dataclass
class AlertCenter:
    """Evaluates every subscriber's rules against monitor events for one session."""

    session: object  # AppSession
    accounts: object  # Accounts
    inbox: dict[str, list[dict]] = field(default_factory=dict)  # user -> alerts
    baselines: dict[tuple, Decimal] = field(default_factory=dict)
    seen: set = field(default_factory=set)
    subscribers: set[str] = field(default_factory=set)

    def attach(self) -> None:
        mon = self.session.monitor  # type: ignore[attr-defined]
        mon.listeners.append(self.on_event)
        mon.observers.append(self.on_observation)

    # ---------------------------------------------------------------- inputs

    def on_event(self, kind: str, payload: dict) -> None:
        if kind == "signal":
            self._fire("value_candidate", payload["game_id"], payload["contract_id"],
                       {"decision_id": payload["decision_id"],
                        "expires_at": payload["expires_at"]})
        elif kind == "state":
            rt = self.session.monitor.games.get(payload["game_id"])  # type: ignore[attr-defined]
            if rt is not None and payload.get("applied"):
                self._fire("game_start", rt.game.game_id, None, {})
                st = rt.reducer.state
                if st is not None and st.is_final:
                    self._fire("final", rt.game.game_id, None, {})

    def on_observation(self, kind: str, payload: dict) -> None:
        if kind != "book":
            return
        snap = payload["snapshot"]
        if not snap.valid or snap.best_ask is None:
            return
        cid = payload["contract_id"]
        gid = next((g for g, rt in self.session.monitor.games.items()  # type: ignore[attr-defined]
                    if any(m.contract_id == cid for m in rt.mappings)), None)
        if gid is None:
            return
        for uid in list(self.subscribers):
            for rule in self._rules(uid):
                if rule["kind"] != "price_move" or not self._in_scope(uid, rule, gid, cid):
                    continue
                key = (uid, rule["rule_id"], cid)
                base = self.baselines.get(key)
                if base is None:
                    self.baselines[key] = snap.best_ask
                    continue
                move = (snap.best_ask - base) * 100
                if abs(move) >= Decimal(str(rule["threshold_cents"])):
                    self.baselines[key] = snap.best_ask
                    self._deliver(uid, rule, gid, cid, {"from": str(base),
                                                        "to": str(snap.best_ask),
                                                        "move_cents": float(move)})

    # ---------------------------------------------------------------- rules

    def _rules(self, uid: str) -> list[dict]:
        doc = self.accounts.store.get_doc(uid, "alert_rules") or {}  # type: ignore[attr-defined]
        return [r for r in doc.get("rules", []) if r.get("enabled")]

    def _in_scope(self, uid: str, rule: dict, gid: str, cid: str | None) -> bool:
        rt = self.session.monitor.games[gid]  # type: ignore[attr-defined]
        g = rt.game
        sc = rule["scope"]
        prefs = self.accounts.preferences(uid)  # type: ignore[attr-defined]
        if g.sport.value not in prefs["sports"]:
            return False
        if sc.get("sport") and sc["sport"] != g.sport.value:
            return False
        if sc.get("game_id") and sc["game_id"] != gid:
            return False
        if sc.get("participant") and sc["participant"] not in (g.home_team, g.away_team):
            return False
        if sc.get("watchlist_only"):
            wl = self.accounts.watchlist(uid)  # type: ignore[attr-defined]
            ids = {w["id"] for w in wl} | set(prefs["favorites"])
            if not ({gid, g.home_team, g.away_team} & ids):
                return False
        return True

    def _fire(self, kind: str, gid: str, cid: str | None, extra: dict) -> None:
        for uid in list(self.subscribers):
            for rule in self._rules(uid):
                if rule["kind"] == kind and self._in_scope(uid, rule, gid, cid):
                    self._deliver(uid, rule, gid, cid, extra)

    # ---------------------------------------------------------------- delivery

    def _deliver(self, uid: str, rule: dict, gid: str, cid: str | None, extra: dict) -> None:
        s = self.session
        now = s.clock.now()  # type: ignore[attr-defined]
        dk = (uid, rule["rule_id"], rule["kind"], gid, cid, extra.get("decision_id"),
              extra.get("to"))
        if rule["kind"] in ("game_start", "final"):
            dk = (uid, rule["rule_id"], rule["kind"], gid)
        if dk in self.seen:
            return
        self.seen.add(dk)
        rt = s.monitor.games[gid]  # type: ignore[attr-defined]
        g = rt.game
        name = lambda p: g.names.get(p, p)  # noqa: E731
        market = None
        evidence_age = None
        if cid:
            m = next(x for x in rt.mappings if x.contract_id == cid)
            book = s.monitor.books.books.get(cid)  # type: ignore[attr-defined]
            snap = book.snapshot() if book is not None and book.last_received else None
            market = {"contract_id": cid, "participant": m.selection_team,
                      "name": name(m.selection_team), "settlement_rule": m.settlement_rule.value,
                      "ask": str(snap.best_ask) if snap and snap.best_ask else None,
                      "observed_at": snap.received_time.isoformat() if snap else None}
            if snap is not None:
                evidence_age = round((now - snap.received_time).total_seconds(), 1)
        title, why, ttl = self._copy(rule, g, market, extra, name)
        prefs = self.accounts.preferences(uid)  # type: ignore[attr-defined]
        suppressed = None
        if prefs.get("alerts_paused"):
            suppressed = "alerts paused (monitoring and settlement continue)"
        elif in_quiet_hours(now, prefs.get("timezone", "UTC"), prefs.get("quiet_hours")):
            suppressed = "quiet hours"
        expires = extra.get("expires_at") or (now + ttl).isoformat()
        self.inbox.setdefault(uid, []).append({
            "alert_id": f"al_{uuid.uuid4().hex[:12]}", "rule_id": rule["rule_id"],
            "kind": rule["kind"], "game_id": gid, "event": f"{name(g.away_team)} at "
            f"{name(g.home_team)}" if g.participant_kind == "TEAM" else
            f"{name(g.home_team)} vs {name(g.away_team)}",
            "sport": g.sport.value, "title": title, "reason_to_review": why,
            "market": market, "evidence_age_s": evidence_age, "created_at": now.isoformat(),
            "expires_at": expires, "delivered": suppressed is None,
            "suppressed_reason": suppressed, "channel": "inbox",
            "data_label": s.data_label,  # type: ignore[attr-defined]
            "disclaimer": "An alert is a prompt to review, not a prediction, a recommendation "
                          "or an order. No paper or real order has been placed."})

    def _copy(self, rule, g, market, extra, name) -> tuple[str, str, timedelta]:
        k = rule["kind"]
        if k == "value_candidate":
            return (f"Paper signal to review: {market['name']}",
                    "Every data, model and cost check passed for this contract at the price "
                    "shown. Re-check the assessment: it expires quickly and the price may "
                    "have moved.", timedelta(minutes=1))
        if k == "price_move":
            d = extra["move_cents"]
            return (f"{market['name']} ask moved {d:+.0f}¢",
                    f"The best ask moved from {float(extra['from']) * 100:.0f}¢ to "
                    f"{float(extra['to']) * 100:.0f}¢. Check what happened in the game and "
                    "whether the estimate moved too.", timedelta(minutes=15))
        if k == "game_start":
            return ("Game data started arriving", "The first live game state was received; "
                    "estimates now update with the game.", timedelta(minutes=30))
        return ("Final result received", "The game is final; paper positions settle by the "
                "contract's rules.", timedelta(hours=6))

    def for_user(self, uid: str, now: datetime) -> list[dict]:
        out = []
        for a in reversed(self.inbox.get(uid, [])):
            exp = datetime.fromisoformat(a["expires_at"])
            out.append(a | {"expired": now > exp})
        return out
