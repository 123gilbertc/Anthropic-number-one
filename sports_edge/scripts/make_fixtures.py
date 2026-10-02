"""Generate the deterministic SYNTHETIC NHL replay fixture.

Everything in the output is invented for testing plumbing. It is labelled
SYNTHETIC in its meta line and must never be used as evidence of anything.

    uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

T0 = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)
GAME_ID = "SYN-2026-10-10-TOR-BOS"
HOME, AWAY = "BOS", "TOR"
C_HOME, C_AWAY = "SYN-NHLGAME-BOS", "SYN-NHLGAME-TOR"
FEED_LAG = 3  # seconds from event to our receipt (synthetic)

lines: list[dict] = []
_ladders: dict[str, dict[str, dict[str, int]]] = {}
_seq = {"kalshi": 0, "game": 0}


def at(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


def iso(t: datetime) -> str:
    return t.isoformat()


def game(sec: int, typ: str, data: dict | None = None, *, seq: int | None = None,
         eid: str | None = None, lag: float = FEED_LAG) -> None:
    if seq is None:
        _seq["game"] += 1
        seq = _seq["game"]
    lines.append({"stream": "game", "received_time": iso(at(sec + lag)), "event": {
        "type": typ, "provider_event_id": eid or f"e{seq}", "seq": seq,
        "event_time": iso(at(sec)), "published_time": iso(at(sec + 1)), "data": data or {}}})


def clock_data(sec: int) -> dict:
    period = min(3, sec // 1200 + 1) if sec < 3600 else 4
    rem = 1200 - (sec - (period - 1) * 1200) if period <= 3 else max(0, 300 - (sec - 3600))
    return {"period": period, "seconds_remaining": rem}


def _send(sec: float, typ: str, msg: dict, *, skip_seq: bool = False) -> None:
    _seq["kalshi"] += 2 if skip_seq else 1
    lines.append({"stream": "kalshi", "received_time": iso(at(sec)),
                  "raw": {"type": typ, "sid": 1, "seq": _seq["kalshi"], "msg": msg}})


def book_snapshot(sec: float, ticker: str, yes: dict[str, int], no: dict[str, int]) -> None:
    _ladders[ticker] = {"yes": dict(yes), "no": dict(no)}
    _send(sec, "orderbook_snapshot", {
        "market_ticker": ticker,
        "yes_dollars_fp": [[p, f"{q}.00"] for p, q in yes.items()],
        "no_dollars_fp": [[p, f"{q}.00"] for p, q in no.items()]})


def book_move(sec: float, ticker: str, yes: dict[str, int], no: dict[str, int],
              skip_first_seq: bool = False) -> None:
    first = True
    for side, target in (("yes", yes), ("no", no)):
        cur = _ladders[ticker][side]
        for p in sorted(set(cur) | set(target)):
            d = target.get(p, 0) - cur.get(p, 0)
            if d:
                _send(sec, "orderbook_delta", {"market_ticker": ticker, "price_dollars": p,
                                               "delta_fp": f"{d}.00", "side": side},
                      skip_seq=skip_first_seq and first)
                first = False
        _ladders[ticker][side] = dict(target)


def ladder(best_bid_yes: float, best_ask_yes: float, size: int = 120) -> tuple[dict, dict]:
    """YES bids below the ask; NO bids such that ask_yes = 1 - best_no_bid."""
    yes = {f"{best_bid_yes - 0.01 * i:.4f}": size + 40 * i for i in range(3)}
    no = {f"{1 - best_ask_yes - 0.01 * i:.4f}": size + 40 * i for i in range(3)}
    return yes, no


def price(sec: float, home_ask: float, spread: float = 0.02) -> None:
    hy, hn = ladder(home_ask - spread, home_ask)
    away_ask = round(1 - home_ask + spread + 0.01, 2)
    ay, an = ladder(away_ask - spread, away_ask)
    book_move(sec, C_HOME, hy, hn)
    book_move(sec, C_AWAY, ay, an)


def odds(sec: float, home: int, away: int, last_update_sec: float, book: str = "pinnacle") -> None:
    lines.append({"stream": "odds", "received_time": iso(at(sec)), "quote": {
        "book": book, "market": "h2h", "prices_american": {HOME: home, AWAY: away},
        "provider_last_update": iso(at(last_update_sec))}})


def build() -> list[dict]:
    meta = {
        "stream": "meta",
        "data_label": "SYNTHETIC",
        "description": "Invented NHL game for plumbing tests. Not real data.",
        "game": {"game_id": GAME_ID, "sport": "NHL", "season": "2026-27", "home_team": HOME,
                 "away_team": AWAY, "scheduled_start": iso(T0), "status": "SCHEDULED",
                 "source": "synthetic_fixture"},
        "mappings": [
            {"mapping_id": "map-bos", "game_id": GAME_ID, "venue": "SYNTHETIC",
             "contract_id": C_HOME, "selection_team": HOME,
             "settlement_rule": "NHL_INCLUDING_OT_SO", "rules_text_hash": None,
             "tick_size": "0.01", "verified_by": "synthetic fixture"},
            {"mapping_id": "map-tor", "game_id": GAME_ID, "venue": "SYNTHETIC",
             "contract_id": C_AWAY, "selection_team": AWAY,
             "settlement_rule": "NHL_INCLUDING_OT_SO", "rules_text_hash": None,
             "tick_size": "0.01", "verified_by": "synthetic fixture"},
        ],
        "pregame_prob": {HOME: 0.60, AWAY: 0.40},
        "anchor_price": {C_HOME: "0.60"},
    }
    # pregame books and reference
    hy, hn = ladder(0.58, 0.60)
    book_snapshot(-300, C_HOME, hy, hn)
    ay, an = ladder(0.40, 0.42)
    book_snapshot(-300, C_AWAY, ay, an)
    odds(-120, -150, 135, -125)

    game(0, "SNAPSHOT", {**clock_data(0), "home_score": 0, "away_score": 0, "home_skaters": 5,
                         "away_skaters": 5, "home_goalie": "Swayman", "away_goalie": "Woll",
                         "home_net_empty": False, "away_net_empty": False})
    goals = {420: AWAY, 900: AWAY, 1500: HOME, 3000: HOME}
    pens = {1100: (HOME, 1220)}
    home_ask = {0: 0.60, 425: 0.36, 905: 0.22, 1505: 0.40, 3005: 0.60}
    dup_done = False
    for sec in range(0, 3600, 30):
        if sec:
            game(sec, "CLOCK", clock_data(sec))
        for g_sec, team in goals.items():
            if sec < g_sec <= sec + 30:
                game(g_sec, "GOAL", {**clock_data(g_sec), "team": team})
                if g_sec == 420 and not dup_done:
                    # the same event delivered twice: must be ignored
                    lines.append(json.loads(json.dumps(lines[-1])))
                    dup_done = True
        for p_sec, (team, end) in pens.items():
            if sec < p_sec <= sec + 30:
                game(p_sec, "PENALTY", {**clock_data(p_sec), "team": team})
                game(end, "PENALTY_END", {**clock_data(end), "team": team})
        for a_sec, a in home_ask.items():
            if a_sec and sec < a_sec <= sec + 30:
                price(a_sec, a)
        # a small persistent drift so the dip persists between goal events
        if 930 <= sec < 1500 and sec % 60 == 0:
            price(sec + 5, 0.23 if sec % 120 else 0.22)

    # reference quotes: one received AFTER the first goal but published BEFORE it
    odds(440, -150, 135, 400)
    odds(480, 105, -120, 460)
    odds(930, 160, -185, 915)
    odds(960, 165, -190, 950)

    # goalie pulled for an unknown replacement, then identified
    game(2000, "GOALIE_CHANGE", {**clock_data(2000), "team": AWAY, "goalie": None})
    game(2090, "GOALIE_CHANGE", {**clock_data(2090), "team": AWAY, "goalie": "Stolarz"})

    # market data gap then resync snapshot
    hy, hn = ladder(0.47, 0.49)
    book_move(2300, C_HOME, hy, hn, skip_first_seq=True)
    book_snapshot(2310, C_HOME, hy, hn)

    # OT winner
    game(3600, "CLOCK", {"period": 4, "seconds_remaining": 300})
    game(3720, "GOAL", {"period": 4, "seconds_remaining": 180, "team": HOME})
    game(3721, "GAME_END", {"period": 4, "seconds_remaining": 180, "decided_in": "OT",
                            "regulation_home_score": 2, "regulation_away_score": 2})
    # transport-level liveness (stands in for WebSocket ping/pong) every 5 seconds
    for sec in range(-300, 3725, 5):
        lines.append({"stream": "kalshi", "received_time": iso(at(sec)),
                      "raw": {"type": "heartbeat"}})
    body = sorted(lines, key=lambda x: x["received_time"])
    # provider sequence numbers follow delivery order (duplicates keep theirs)
    n = 0
    seen_ids: dict[str, int] = {}
    for x in body:
        if x["stream"] != "game":
            continue
        e = x["event"]
        if e["provider_event_id"] in seen_ids:
            e["seq"] = seen_ids[e["provider_event_id"]]
            continue
        n += 1
        e["seq"] = n
        seen_ids[e["provider_event_id"]] = n
    return [meta] + body


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"
    out.write_text("\n".join(json.dumps(x, sort_keys=True) for x in build()) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
