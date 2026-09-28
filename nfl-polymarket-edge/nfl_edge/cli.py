"""Command-line entry point: ``python -m nfl_edge.cli <command>`` or ``nfl-edge <command>``."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import pipeline
from .config import DEFAULT_STRATEGY, GAMMA_API, CLOB_API, REPORTS_DIR, SNAPSHOT_DIR, ensure_dirs
from .data import current_season, current_week, load_games, load_team_week_stats
from .markets.polymarket import PolymarketClient


def _games(refresh: bool = False):
    return load_games(refresh=refresh)


def _bundle(path: str | None):
    return pipeline.ModelBundle.load(Path(path) if path else pipeline.MODELS_PATH)


def _client(args) -> PolymarketClient:
    return PolymarketClient(gamma_url=GAMMA_API, clob_url=CLOB_API, fixture_dir=args.fixture)


def _strategy(args):
    cfg = DEFAULT_STRATEGY
    overrides = {k: getattr(args, k) for k in ("min_edge", "kelly_fraction", "fee_rate", "slippage_buffer",
                                                 "max_stake_fraction", "max_weekly_exposure", "min_liquidity_usd")
                 if getattr(args, k, None) is not None}
    return replace(cfg, **overrides) if overrides else cfg


def cmd_update_data(args) -> int:
    games = _games(refresh=True)
    tw = load_team_week_stats(refresh=args.all)
    s = current_season(games)
    print(f"games: {len(games)} rows through season {s} week {current_week(games, s)}; team-week stats: {len(tw)} rows")
    return 0


def cmd_fit(args) -> int:
    games = _games()
    bundle = pipeline.fit_models(games, tune_elo=not args.no_tune, quick=args.quick, l2=args.l2)
    path = bundle.save(Path(args.models) if args.models else pipeline.MODELS_PATH)
    print(f"saved {path}")
    return 0


def cmd_backtest(args) -> int:
    games = _games()
    bundle = _bundle(args.models)
    pipeline.evaluate(games, bundle, first_test=args.first_test, last_test=args.last_test)
    print(f"report: {REPORTS_DIR / 'backtest.md'}")
    return 0


def cmd_predict(args) -> int:
    games = _games()
    bundle = _bundle(args.models)
    season = args.season or current_season(games)
    week = args.week or current_week(games, season)
    table = pipeline.predict_week(games, bundle, season, week)
    print((REPORTS_DIR / f"predictions_{season}_wk{week:02d}.md").read_text())
    print(f"{len(table)} games; csv/md written to {REPORTS_DIR}")
    return 0


def cmd_simulate(args) -> int:
    games = _games()
    bundle = _bundle(args.models)
    season = args.season or current_season(games)
    pipeline.futures(games, bundle, season, n_sims=args.sims, seed=args.seed, anchor_to_market=not args.no_anchor)
    print((REPORTS_DIR / f"futures_{season}.md").read_text())
    return 0


def cmd_scan(args) -> int:
    games = _games()
    bundle = _bundle(args.models)
    pipeline.scan(_client(args), games, bundle, cfg=_strategy(args), bankroll=args.bankroll, n_sims=args.sims,
                  record_fair=not args.no_record)
    return 0


def cmd_snapshot(args) -> int:
    games = bundle = None
    if not args.prices_only:
        try:
            bundle = _bundle(args.models)
            games = _games()
        except FileNotFoundError as exc:
            print(f"{exc}; recording prices only", file=sys.stderr)
    n = pipeline.take_snapshot(_client(args), games, bundle, n_sims=args.sims)
    print(f"{n} rows appended to {pipeline.SNAPSHOT_PATH}")
    return 0


def cmd_clv(args) -> int:
    games = _games()
    bundle = None
    try:
        bundle = _bundle(args.models)
    except FileNotFoundError:
        pass
    pipeline.clv_report(games, bundle)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nfl-edge", description="NFL probability engine and Polymarket edge scanner")
    sub = p.add_subparsers(dest="command", required=True)

    def models_arg(sp):
        sp.add_argument("--models", help="path to models.json (default data/models.json)")

    sp = sub.add_parser("update-data", help="refresh nflverse games and team stats"); sp.add_argument("--all", action="store_true", help="re-download every season of team stats"); sp.set_defaults(fn=cmd_update_data)
    sp = sub.add_parser("fit", help="tune Elo, fit EPA scale, sigmas and the stackers"); models_arg(sp)
    sp.add_argument("--quick", action="store_true", help="small Elo grid, one pass"); sp.add_argument("--no-tune", action="store_true", help="keep default Elo parameters")
    sp.add_argument("--l2", type=float, default=5.0, help="stacker ridge strength"); sp.set_defaults(fn=cmd_fit)
    sp = sub.add_parser("backtest", help="walk-forward evaluation vs the closing line"); models_arg(sp)
    sp.add_argument("--first-test", type=int, default=2012); sp.add_argument("--last-test", type=int); sp.set_defaults(fn=cmd_backtest)
    sp = sub.add_parser("predict", help="fair prices for a week"); models_arg(sp)
    sp.add_argument("--season", type=int); sp.add_argument("--week", type=int); sp.set_defaults(fn=cmd_predict)
    sp = sub.add_parser("simulate", help="season simulation -> futures fair values"); models_arg(sp)
    sp.add_argument("--season", type=int); sp.add_argument("--sims", type=int, default=20000); sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--no-anchor", action="store_true", help="use raw Elo instead of market-anchored ratings"); sp.set_defaults(fn=cmd_simulate)
    for name, fn, help_ in (("scan", cmd_scan, "price Polymarket markets and find edges"), ("snapshot", cmd_snapshot, "record Polymarket prices and fair values")):
        sp = sub.add_parser(name, help=help_); models_arg(sp)
        sp.add_argument("--fixture", help="read recorded fixtures instead of the live API (offline demo)")
        sp.add_argument("--sims", type=int, default=10000 if name == "scan" else 4000)
        if name == "scan":
            sp.add_argument("--bankroll", type=float, default=1000.0)
            sp.add_argument("--no-record", action="store_true", help="do not append fair values to the snapshot store")
            for opt in ("min_edge", "kelly_fraction", "fee_rate", "slippage_buffer", "max_stake_fraction", "max_weekly_exposure", "min_liquidity_usd"):
                sp.add_argument("--" + opt.replace("_", "-"), dest=opt, type=float)
        else:
            sp.add_argument("--prices-only", action="store_true", help="skip fair values even when models exist")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("clv", help="closing-line value on recorded snapshots"); models_arg(sp); sp.set_defaults(fn=cmd_clv)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
