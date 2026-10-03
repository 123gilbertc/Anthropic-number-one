"""Command line entry point: ``uv run sports-edge <command>``.

    demo       replay the synthetic fixture honestly (no model) and in mechanics mode
    replay     replay a JSONL file through the live code path
    train      train + evaluate on synthetic data (SYNTHETIC_ONLY artifacts)
    paper      run the matched paper-strategy comparison on a replay file
    serve      start the API (and dashboard if built)
    discover   list Kalshi NHL/MLB game markets and their rules text (public REST)
    record     record Kalshi order-book WebSocket messages to JSONL (needs API key)
    live       refuses: live decisions are BLOCKED until a qualified game feed exists
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sports_edge.config import ROOT, settings


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_demo(_a) -> None:
    from sports_edge.forecast.train import train_synthetic
    from sports_edge.replay.runner import replay_file

    fx = ROOT / "fixtures" / "nhl_synthetic_dip.jsonl"
    print("== 1. Honest mode: synthetic feed, no trained model ==")
    honest = replay_file(fx, forecaster=None).summary()
    _print({k: honest[k] for k in ("data_label", "source_status", "actions", "alerts")})
    print("-> no value alerts: the feed is synthetic and no validated model exists.\n")
    print("== 2. Mechanics demo: synthetic feed + SYNTHETIC_ONLY model (explicit opt-in) ==")
    rep = train_synthetic(n_games=300)
    mech = replay_file(fx, forecaster=rep.forecaster, mechanics_demo=True).summary()
    _print({k: mech[k] for k in ("actions", "alerts", "cash_after")})
    for f in mech["fills"]:
        print(f"  paper fill {f['fill_time']} {f['contract_id']} qty={f['filled_quantity']} "
              f"cost=${f['cost']} fees=${f['fees']}")
    print("-> this shows the plumbing works. It is NOT evidence of any edge.")


def cmd_replay(a) -> None:
    from sports_edge.forecast.models import ChainForecaster, load_artifact
    from sports_edge.replay.runner import replay_file
    from sports_edge.storage.repository import SqlSink, make_engine

    fc = None
    if a.model:
        from sports_edge.domain.enums import SettlementRule
        fc = ChainForecaster([load_artifact(Path(m), SettlementRule(a.rule)) for m in a.model])
    sink = SqlSink(make_engine(settings().database_url)) if a.persist else None
    r = replay_file(Path(a.file), forecaster=fc, extra_sink=sink,
                    mechanics_demo=a.mechanics_demo)
    _print(r.summary())


def cmd_train(a) -> None:
    from sports_edge.forecast.models import save_artifact
    from sports_edge.forecast.train import train_synthetic

    s = settings()
    if a.compare:
        from sports_edge.domain.enums import ModelStatus
        from sports_edge.forecast.synthetic import simulate_season
        from sports_edge.forecast.train import compare_models
        df = simulate_season(a.games, seed=a.seed)
        rep = compare_models(df, f"SYNTHETIC simulate_season(n={a.games}, seed={a.seed})",
                             ModelStatus.SYNTHETIC_ONLY, s.runs_dir / "experiments.jsonl")
        _print({k: {"brier": v["brier"]["value"], "brier_ci": [v["brier"]["low"],
                                                              v["brier"]["high"]],
                    "log_loss": v["log_loss"]["value"]} for k, v in rep["models"].items()})
        return
    rep = train_synthetic(n_games=a.games, seed=a.seed, kind=a.kind,
                          log_path=s.runs_dir / "experiments.jsonl")
    saved = [save_artifact(f, s.artifacts_dir) for f in rep.forecaster.forecasters]
    _print({"artifacts": [v.version.artifact_path for v in saved],
            "status": saved[0].version.status, "test_metrics": rep.test_metrics})


def cmd_thresholds(a) -> None:
    from sports_edge.domain.enums import ModelStatus
    from sports_edge.evaluation.splits import ExperimentLog, chronological_split
    from sports_edge.features.nhl import FEATURE_NAMES
    from sports_edge.forecast.synthetic import simulate_season
    from sports_edge.forecast.train import train_nhl
    from sports_edge.triggers import thresholds

    s = settings()
    df = simulate_season(a.games, seed=11)
    rep = train_nhl(df, "SYNTHETIC thresholds", ModelStatus.SYNTHETIC_ONLY, n_bootstrap=5)
    model = rep.forecaster.forecasters[0].model
    _, lo, _ = model.predict_matrix(df[list(FEATURE_NAMES)].to_numpy(float))
    df = df.assign(p_low=lo, ask=(df["market_p"] + 0.01).clip(0.02, 0.98))
    sp = chronological_split(df)
    ft = thresholds.estimate(sp.validation, "SYNTHETIC_ONLY")
    path = thresholds.save(ft, s.runs_dir)
    log = ExperimentLog(s.runs_dir / "experiments.jsonl")
    test_result = thresholds.evaluate_frozen(ft, sp.test)
    log.log("THRESHOLDS_FROZEN", config=str(path), test=test_result)
    _print({"frozen": str(path), "thresholds": ft.__dict__, "test_once": test_result,
            "note": "Synthetic data: plumbing check only, not evidence."})


def cmd_paper(a) -> None:
    from sports_edge.paper.compare import compare_strategies

    _print(compare_strategies(Path(a.file), mechanics_demo=a.mechanics_demo))


def cmd_serve(a) -> None:
    import uvicorn

    uvicorn.run("sports_edge.api.app:app_factory", factory=True, host=a.host, port=a.port)


def cmd_openapi(a) -> None:
    from sports_edge.api.app import create_app

    spec = create_app(train_games=60).openapi()
    Path(a.out).write_text(json.dumps(spec, indent=1))
    print(f"wrote {a.out}")


def cmd_discover(a) -> None:
    from sports_edge.adapters.kalshi_rest import list_game_markets

    _print(asyncio.run(list_game_markets(settings().kalshi_rest_base, a.series)))


def cmd_record(a) -> None:
    from sports_edge.adapters.kalshi_record import record

    s = settings()
    from sports_edge.connections import materialize_kalshi_key
    if materialize_kalshi_key(s.runs_dir):
        s = settings()
    if not s.kalshi_key_id or not s.kalshi_private_key_path:
        sys.exit("BLOCKED: KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH are required for the "
                 "Kalshi WebSocket (it needs a signed handshake even for public data).")
    out = Path(a.out or s.runs_dir / f"kalshi_{datetime.now(UTC):%Y%m%dT%H%M%S}.jsonl")
    asyncio.run(record(s, a.tickers, out))


def cmd_live(_a) -> None:
    sys.exit("BLOCKED: no live NHL game-state source has passed the coverage/freshness/"
             "licensing test (see DATA_SOURCES.md). Live paper decisions are disabled. "
             "Use `record` to capture market data and `replay` for research.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sports-edge")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    r = sub.add_parser("replay")
    r.add_argument("file")
    r.add_argument("--model", action="append", help="model metadata .json (repeatable: full, reduced)")
    r.add_argument("--rule", default="NHL_INCLUDING_OT_SO")
    r.add_argument("--persist", action="store_true", help="also write to DATABASE_URL")
    r.add_argument("--mechanics-demo", action="store_true")
    r.set_defaults(fn=cmd_replay)
    t = sub.add_parser("train")
    t.add_argument("--synthetic", action="store_true", required=True,
                   help="required until licensed historical data exists")
    t.add_argument("--games", type=int, default=600)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--kind", choices=["logistic", "gbm"], default="logistic")
    t.add_argument("--compare", action="store_true",
                   help="compare logistic, GBM and the market-implied baseline")
    t.set_defaults(fn=cmd_train)
    th = sub.add_parser("thresholds", help="estimate value-gate margin on validation, freeze")
    th.add_argument("--synthetic", action="store_true", required=True)
    th.add_argument("--games", type=int, default=600)
    th.set_defaults(fn=cmd_thresholds)
    pp = sub.add_parser("paper")
    pp.add_argument("file")
    pp.add_argument("--mechanics-demo", action="store_true")
    pp.set_defaults(fn=cmd_paper)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    oa = sub.add_parser("openapi")
    oa.add_argument("--out", default=str(ROOT / "web" / "openapi.json"))
    oa.set_defaults(fn=cmd_openapi)
    d = sub.add_parser("discover")
    d.add_argument("--series", default="KXNHLGAME")
    d.set_defaults(fn=cmd_discover)
    rc = sub.add_parser("record")
    rc.add_argument("tickers", nargs="+")
    rc.add_argument("--out")
    rc.set_defaults(fn=cmd_record)
    sub.add_parser("live").set_defaults(fn=cmd_live)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
