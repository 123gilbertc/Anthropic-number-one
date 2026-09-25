"""Command line entry point:  python -m tennis_edge <command> ...

  download   fetch Sackmann ATP/WTA match files
  ratings    show the current top players by Elo
  predict    win probability for one match
  price      evaluate a Polymarket price you're looking at
  backtest   walk-forward test vs closing odds (tennis-data.co.uk files)
  scan       pull live Polymarket tennis markets and list value bets
"""
from __future__ import annotations

import argparse
import csv
import os
import json
from datetime import date, datetime, timedelta, timezone

from .backtest import BacktestConfig, run, tune_w_model
from .betting import shrink, size_bet
from .data import download_tml, load_matches
from .elo import Elo, EloParams

DEFAULT_DATA = ["data/*.csv", "data/*.xlsx"]
JOURNAL = "bets_journal.csv"


def _model(args) -> Elo:
    matches = load_matches(args.data or DEFAULT_DATA)
    if not matches:
        raise SystemExit("No match data found. Run `python -m tennis_edge download` first, "
                         "or pass --data path/to/*.csv")
    elo = Elo(EloParams(surface_weight=args.surface_weight)).fit(matches)
    print(f"[model] trained on {len(matches):,} matches up to {matches[-1].date}")
    stale = (date.today() - matches[-1].date).days
    if stale > 14:
        print(f"[model] WARNING: newest result is {stale} days old. Ratings miss recent form, injuries "
              f"and breakout players. Refresh ./data before betting real money.")
    return elo


def cmd_download(args):
    years = range(args.start, args.end + 1)
    print(f"Downloading ATP {args.start}-{args.end} (TML-Database, Sackmann format) ...")
    download_tml(years, args.out)
    print("\nFor WTA, current-season results and closing odds, download the yearly .xlsx files\n"
          "(ATP and WTA 'w' folders) from http://www.tennis-data.co.uk/alldata.php into ./data/")


def cmd_ratings(args):
    elo = _model(args)
    for i, (name, r, n) in enumerate(elo.top(args.n, args.surface), 1):
        print(f"{i:>3}. {name:<28} {r:>7.1f}  ({n} matches)")


def cmd_predict(args):
    elo = _model(args)
    for p in (args.a, args.b):
        if not elo.known(p):
            print(f"  warning: no history for {p!r} (rated as a 1500 newcomer)")
    pa = elo.prob(args.a, args.b, args.surface, args.best_of, on=date.today())
    print(f"{args.a} {pa:.1%}  vs  {args.b} {1-pa:.1%}   ({args.surface}, best of {args.best_of})")
    print(f"Fair prices: {args.a} {pa:.3f}, {args.b} {1-pa:.3f}")


def _evaluate(elo, a, b, price_a, price_b, surface, best_of, args, fee_rate=None):
    """Return list of (player, p_model, p_final, price, Bet|None)."""
    rate = args.fee_rate if fee_rate is None else fee_rate
    pa = elo.prob(a, b, surface, best_of, on=date.today())
    mid_a = price_a / (price_a + price_b) if price_a and price_b else price_a
    pf = shrink(pa, mid_a, args.w_model) if mid_a else pa
    rows = []
    for name, pm, pfinal, price in ((a, pa, pf, price_a), (b, 1 - pa, 1 - pf, price_b)):
        fee = rate * price * (1 - price) if price else 0.0
        bet = size_bet(name, pfinal, price, args.bankroll, kelly_fraction=args.kelly,
                       max_bet_pct=args.max_bet, min_edge=args.min_edge, fee=fee) if price else None
        rows.append((name, pm, pfinal, price, bet))
    return rows


def cmd_price(args):
    elo = _model(args)
    if args.fee_rate is None:
        args.fee_rate = 0.05
    pb = args.price_b if args.price_b is not None else 1 - args.price_a
    for name, pm, pf, price, bet in _evaluate(elo, args.a, args.b, args.price_a, pb,
                                              args.surface, args.best_of, args):
        tag = f"BET ${bet.stake:.2f}  edge {bet.edge:+.3f}" if bet else "pass"
        print(f"{name:<26} model {pm:.1%}  blended {pf:.1%}  price {price:.3f}  -> {tag}")


def cmd_backtest(args):
    matches = load_matches(args.data or DEFAULT_DATA)
    with_odds = sum(1 for m in matches if m.odds_w)
    print(f"Loaded {len(matches):,} matches ({with_odds:,} with odds)")
    if not with_odds:
        raise SystemExit("Backtest needs odds: use tennis-data.co.uk files (they include Pinnacle closing odds).")
    cfg = BacktestConfig(start=date.fromisoformat(args.start), w_model=args.w_model,
                         min_edge=args.min_edge, kelly_fraction=args.kelly,
                         max_bet_pct=args.max_bet, min_matches=args.min_matches)
    if args.tune:
        print("\nBlend weight vs log loss (pick the minimum):")
        for w, ll in tune_w_model(matches, cfg):
            print(f"  w_model={w:.1f}  log loss={ll:.4f}")
    print()
    print(run(matches, cfg, EloParams(surface_weight=args.surface_weight)).summary())


def cmd_scan(args):
    from .polymarket import fetch_events, markets_from_events

    elo = _model(args)
    if args.events_json:
        events = []
        for path in args.events_json:
            with open(path) as fh:
                events += json.load(fh)
        markets = markets_from_events(events)
    else:
        import requests
        s = requests.Session()
        markets = markets_from_events(fetch_events(args.tags, session=s), None if args.no_books else s)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=args.hours)
    markets = [m for m in markets if not m.doubles and m.start_dt and now < m.start_dt <= horizon]
    print(f"[polymarket] {len(markets)} singles match-winner markets starting in the next {args.hours}h\n")

    picks, rated, unrated = [], [], []
    for mk in sorted(markets, key=lambda m: m.start_dt):
        a, b = mk.outcomes
        exp = min(elo.experience(a.name), elo.experience(b.name))
        if exp < args.min_matches:
            unrated.append(mk)
            continue
        pa_price = a.best_ask or a.mid
        pb_price = b.best_ask or b.mid
        surface = args.surface or mk.surface
        best_of = args.best_of or mk.best_of
        rows = _evaluate(elo, a.name, b.name, pa_price, pb_price, surface, best_of, args,
                         fee_rate=mk.fee_rate if args.fee_rate is None else args.fee_rate)
        rated.append((mk, rows))
        for name, pm, pf, price, bet in rows:
            if bet and mk.liquidity >= args.min_liquidity:
                picks.append((bet.edge, mk, name, pm, pf, price, bet))

    if args.all or not picks:
        print("Rated matches (model% vs market ask):")
        for mk, rows in rated:
            (na, pa, _, qa, _), (nb, pb, _, qb, _) = rows
            print(f"  {mk.start_dt:%a %H:%M}Z  {na[:22]:<22} {pa:>5.0%} @ {qa or 0:.2f}   "
                  f"{nb[:22]:<22} {pb:>5.0%} @ {qb or 0:.2f}   [{mk.event_title.split(':')[0][:22]}]")
        print(f"\n{len(unrated)} matches skipped: a player has < {args.min_matches} rated matches in the data.\n")

    picks.sort(key=lambda x: -x[0])
    if not picks:
        print("No bets clear the edge threshold after fees. Passing is a position - that's normal.")
        return
    print(f"{'PLAYER':<24}{'MODEL':>7}{'BLEND':>7}{'ASK':>7}{'EDGE':>7}{'STAKE':>9}  START (UTC)  MATCH")
    for edge, mk, name, pm, pf, price, bet in picks:
        print(f"{name[:23]:<24}{pm:>7.1%}{pf:>7.1%}{price:>7.3f}{edge:>+7.3f}{bet.stake:>9.2f}  "
              f"{mk.start_dt:%a %H:%M}    {mk.event_title[:48]} [{args.surface or mk.surface}, Bo{args.best_of or mk.best_of}]")
    print("\nEDGE is after Polymarket's taker fee. Use a limit order at or below ASK.")
    if args.log:
        new = not os.path.exists(JOURNAL)
        with open(JOURNAL, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["logged_utc", "match_start_utc", "match", "player", "model_p", "blend_p",
                            "price", "edge", "stake", "url", "closing_price", "result", "pnl"])
            ts = now.isoformat(timespec="seconds")
            for edge, mk, name, pm, pf, price, bet in picks:
                w.writerow([ts, mk.start_dt.isoformat(), mk.event_title, name, f"{pm:.4f}", f"{pf:.4f}",
                            f"{price:.3f}", f"{edge:.4f}", bet.stake, mk.url, "", "", ""])
        print(f"\nLogged {len(picks)} picks to {JOURNAL} - fill in closing_price/result to track CLV.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tennis_edge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, bet=False):
        p.add_argument("--data", nargs="*", help="CSV/XLSX files, globs or folders (default ./data)")
        p.add_argument("--surface-weight", type=float, default=0.5)
        if bet:
            p.add_argument("--bankroll", type=float, default=1000.0)
            p.add_argument("--w-model", type=float, default=0.3,
                           help="weight on model vs market price (tune with backtest --tune)")
            p.add_argument("--min-edge", type=float, default=0.04)
            p.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction")
            p.add_argument("--max-bet", type=float, default=0.02, help="max fraction of bankroll per bet")
            p.add_argument("--fee-rate", type=float, default=None,
                           help="taker fee rate; fee/share = rate*p*(1-p). Default: 0.05 on Polymarket "
                                "sports markets (scan) and 0.05 for `price`")
        return p

    p = sub.add_parser("download")
    p.add_argument("--start", type=int, default=2015)
    p.add_argument("--end", type=int, default=date.today().year)
    p.add_argument("--out", default="data")
    p.set_defaults(fn=cmd_download)

    p = common(sub.add_parser("ratings"))
    p.add_argument("-n", type=int, default=25)
    p.add_argument("--surface", choices=["hard", "clay", "grass"])
    p.set_defaults(fn=cmd_ratings)

    p = common(sub.add_parser("predict"))
    p.add_argument("a"); p.add_argument("b")
    p.add_argument("--surface", default="hard", choices=["hard", "clay", "grass"])
    p.add_argument("--best-of", type=int, default=3, choices=[3, 5])
    p.set_defaults(fn=cmd_predict)

    p = common(sub.add_parser("price"), bet=True)
    p.add_argument("a"); p.add_argument("b")
    p.add_argument("price_a", type=float, help="Polymarket ask for player A, e.g. 0.62")
    p.add_argument("price_b", type=float, nargs="?", help="ask for player B (default 1 - price_a)")
    p.add_argument("--surface", default="hard", choices=["hard", "clay", "grass"])
    p.add_argument("--best-of", type=int, default=3, choices=[3, 5])
    p.set_defaults(fn=cmd_price)

    p = common(sub.add_parser("backtest"), bet=True)
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--min-matches", type=int, default=30)
    p.add_argument("--tune", action="store_true", help="grid-search the model/market blend weight")
    p.set_defaults(fn=cmd_backtest)

    p = common(sub.add_parser("scan"), bet=True)
    p.add_argument("--tags", nargs="+", default=["tennis"])
    p.add_argument("--surface", choices=["hard", "clay", "grass"], help="override guessed surface")
    p.add_argument("--best-of", type=int, choices=[3, 5], help="override guessed format")
    p.add_argument("--min-matches", type=int, default=30)
    p.add_argument("--min-liquidity", type=float, default=1000.0)
    p.add_argument("--hours", type=float, default=24, help="only matches starting within this many hours")
    p.add_argument("--events-json", nargs="+", help="read saved Gamma /events JSON instead of calling the API")
    p.add_argument("--no-books", action="store_true", help="use Gamma top-of-book, skip CLOB order books")
    p.add_argument("--all", action="store_true", help="also print every market found")
    p.add_argument("--log", action="store_true", help=f"append picks to {JOURNAL}")
    p.set_defaults(fn=cmd_scan)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
