"""Record a demo replay from the REAL backend for the static "recorded replay" page.

Every number in the output is produced by the backend (AppSession / Monitor /
engine / broker). The page that plays it back computes nothing: it only shows
these recorded responses.

Recorded:
* honest session (no model): every changed frame + the preview response at each frame;
* mechanics session (synthetic-only model): the same, plus one "branch" per
  eligible (frame, contract): a fresh replay to that frame, a real paper order,
  then the rest of the game, recording the order's lifecycle, ledger events,
  evaluation and how the contracts' decisions change afterwards.

    uv run python scripts/record_demo.py <out.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sports_edge.domain.explain import TEXT
from sports_edge.forecast.train import train_synthetic
from sports_edge.session import AppSession

FX = Path(__file__).resolve().parents[1] / "fixtures" / "nhl_synthetic_dip.jsonl"


def r(x, n=4):
    return None if x is None else round(float(x), n)


def contract_row(c: dict) -> dict:
    ev = c.get("ev") or {}
    return {"id": c["contract_id"], "sel": c["selection"], "rule": c["settlement_rule"],
            "p": r(c["probability"]), "lo": r(c["probability_low"]),
            "hi": r(c["probability_high"]), "rel": c["reliability"], "ms": c["model_status"],
            "abst": c["abstention"], "ask": c["best_ask"], "bid": c["best_bid"],
            "depth": c["ask_depth"], "valid": c["book_valid"], "act": c["action"],
            "why": c["reasons"], "room": c["remaining_paper_exposure"],
            "evc": ev.get("ev_conservative"), "evq": ev.get("quantity"),
            "fees": ev.get("expected_fees"), "elig": c["signal_eligible"],
            "did": c["decision_id"], "exp": c["decision_expires_at"]}


def frame(s: AppSession) -> dict:
    gid = s.stream.game.game_id
    g = s.monitor.game_view(gid)
    st = g["state"]
    now = s.clock.now()
    rs = s.monitor.games[gid].reducer.state
    return {
        "t": now.isoformat(), "pos": s.stream.position,
        "st": None if st is None else {
            "per": st["period"], "secs": st["seconds_remaining_in_period"],
            "hs": st["home_score"], "as": st["away_score"], "hsk": st["home_skaters"],
            "ask": st["away_skaters"], "hg": st["home_goalie"], "ag": st["away_goalie"],
            "fin": st["is_final"], "dec": st["final_decided_in"],
            "pend": st["pending_reconciliation"]},
        "age": None if rs is None else round((now - rs.as_of_received_time).total_seconds()),
        "fav": g["model_favored_team"], "val": g["value_side"],
        "c": [contract_row(c) for c in g["contracts"]],
    }


def preview(s: AppSession, cid: str) -> dict:
    gid = s.stream.game.game_id
    with s.lock:
        d = s.monitor.preview(gid, cid)
    eligible = d.action.value in ("PAPER_ENTRY", "PAPER_ADD")
    return {"elig": eligible, "did": d.decision_id, "act": d.action.value,
            "why": [x.value for x in d.reasons], "maxq": d.planned_quantity if eligible else 0,
            "cost": str(d.planned_cost), "fees": str(d.ev.expected_fees) if d.ev else None,
            "exp": d.expires_at.isoformat()}


def run_baseline(fc, mechanics: bool):
    s = AppSession.replay(FX, fc, mechanics, None)
    cids = [m.contract_id for m in s.stream.mappings]
    frames, prev = [], None
    for i in range(len(s.stream.body) + 1):
        if i:
            s.step(1)
        f = frame(s)
        f["pv"] = {cid: preview(s, cid) for cid in cids}
        key = json.dumps({k: v for k, v in f.items() if k not in ("t", "pos", "age")},
                         sort_keys=True)
        if key != prev or i == len(s.stream.body):
            f["i"] = i
            frames.append(f)
            prev = key
    decisions = [{"t": d.decision_time.isoformat(), "cid": d.contract_id,
                  "act": d.action.value, "why": [x.value for x in d.reasons],
                  "model": d.model_version} for d in s.monitor.sink.decisions]
    return s, frames, decisions


def run_branch(fc, k: int, cid: str) -> dict | None:
    s = AppSession.replay(FX, fc, True, None)
    cids = [m.contract_id for m in s.stream.mappings]
    for _ in range(k):
        s.step(1)
        for c in cids:
            preview(s, c)  # same call pattern as the baseline recording
    pv = preview(s, cid)
    if not pv["elig"]:
        return None
    o, created = s.place_order(pv["did"], f"demo-{k}-{cid}", None, expected_contract_id=cid)
    assert created
    oid = o.order_id
    status_log = [{"i": k, "status": o.status, "t": s.clock.now().isoformat()}]
    deltas, prev = [], None
    for i in range(k + 1, len(s.stream.body) + 1):
        s.step(1)
        for c in cids:
            preview(s, c)
        cur = s.orders[oid]
        if cur.status != status_log[-1]["status"] or (cur.outcome and
                                                      "outcome" not in status_log[-1]):
            e = {"i": i, "status": cur.status, "t": s.clock.now().isoformat(),
                 "why": cur.reasons}
            if cur.fill:
                e["fill"] = {"q": cur.fill.filled_quantity, "cost": str(cur.fill.cost),
                             "fees": str(cur.fill.fees)}
            if cur.outcome:
                e["outcome"] = cur.outcome.value
                e["pnl"] = str(cur.settled_pnl)
            status_log.append(e)
        g = s.monitor.game_view(s.stream.game.game_id)
        slim = [{"act": c["action"], "why": c["reasons"], "room": c["remaining_paper_exposure"],
                 "elig": False} for c in g["contracts"]]  # one order per demo run
        if json.dumps(slim) != prev:
            deltas.append({"i": i, "c": slim})
            prev = json.dumps(slim)
    led = s.monitor.engine.ledger
    return {"k": k, "cid": cid, "order_id": oid, "requested": o.requested_quantity,
            "status": status_log, "deltas": deltas,
            "ledger": [e.model_dump(mode="json") for e in s.ledger],
            "evaluation": s.evaluation(), "cash": str(led.cash)}


def main(out: Path) -> None:
    fc = train_synthetic(n_games=300).forecaster
    model = fc.version.model_dump(mode="json")
    sh, honest, honest_dec = run_baseline(None, False)
    sm, mech, mech_dec = run_baseline(fc, True)
    branches = {}
    for f in mech:
        for cid, pv in f["pv"].items():
            if pv["elig"]:
                b = run_branch(fc, f["i"], cid)
                if b:
                    branches[f"{f['i']}:{cid}"] = b
    game = sm.stream.game.model_dump(mode="json")
    data = {
        "recorded_note": "Every value was produced by the sports_edge backend (synthetic "
                         "fixture, SYNTHETIC_ONLY model). The page computes nothing.",
        "game": game, "total_steps": len(sm.stream.body),
        "reason_text": {k.value: v for k, v in TEXT.items()},
        "banners": {
            "honest": ["SYNTHETIC DEMO", "LIVE GAME FEED: NOT CONNECTED (BLOCKED)",
                       "NO MODEL LOADED", "PAPER ONLY: no real-money execution exists"],
            "mechanics": ["SYNTHETIC DEMO", "LIVE GAME FEED: NOT CONNECTED (BLOCKED)",
                          f"UNVALIDATED MODEL ({model['status']})",
                          "MECHANICS DEMO: signals are not evidence of value",
                          "PAPER ONLY: no real-money execution exists"]},
        "model": {k: model[k] for k in ("model_version", "status", "trained_on")},
        "limits": {k: str(v) for k, v in sm.monitor.engine.ledger.limits.__dict__.items()},
        "honest": {"frames": honest, "decisions": honest_dec,
                   "evaluation": sh.evaluation()},
        "mechanics": {"frames": mech, "decisions": mech_dec,
                      "evaluation": sm.evaluation(), "branches": branches},
    }
    out.write_text(json.dumps(data, separators=(",", ":"), default=str))
    print(f"wrote {out}: {out.stat().st_size / 1e6:.1f} MB, honest frames {len(honest)}, "
          f"mechanics frames {len(mech)}, order branches {len(branches)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
