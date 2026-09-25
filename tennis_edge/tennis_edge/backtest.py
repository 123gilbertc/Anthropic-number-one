"""Walk-forward backtest against closing bookmaker odds.

This is the reality check. For every match after `start` we:
  1. predict with ratings built ONLY from earlier matches (no look-ahead),
  2. compare to the de-vigged closing line,
  3. place a simulated bet if our (shrunk) probability beats the price by
     `min_edge`, sized with fractional Kelly,
  4. then update ratings with the result.

If the model can't beat Pinnacle closing odds here, it won't beat Polymarket
either - Polymarket tennis prices generally track the sharp books.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .betting import implied_from_decimal, kelly_binary, shrink
from .data import Match
from .elo import Elo, EloParams, log_loss


@dataclass
class BacktestConfig:
    start: date = date(2020, 1, 1)
    end: Optional[date] = None
    w_model: float = 0.3          # weight on our model vs the market
    min_edge: float = 0.03        # required p - price
    kelly_fraction: float = 0.25
    max_bet_pct: float = 0.02
    min_matches: int = 30         # both players need this much history
    bankroll: float = 1000.0
    max_odds: float = 5.0         # skip long shots (price < 0.20)


@dataclass
class BacktestResult:
    n_pred: int = 0
    model_ll: float = 0.0
    market_ll: float = 0.0
    blend_ll: float = 0.0
    model_brier: float = 0.0
    market_brier: float = 0.0
    model_acc: int = 0
    market_acc: int = 0
    bets: int = 0
    wins: int = 0
    staked: float = 0.0
    profit: float = 0.0
    bankroll: float = 0.0
    peak: float = 0.0
    max_drawdown: float = 0.0
    flat_profit: float = 0.0      # 1 unit per bet, independent of Kelly
    buckets: dict = field(default_factory=dict)

    def summary(self) -> str:
        n = max(self.n_pred, 1)
        b = max(self.bets, 1)
        lines = [
            f"Predictions scored         : {self.n_pred}",
            f"Log loss   model / market / blend : {self.model_ll/n:.4f} / {self.market_ll/n:.4f} / {self.blend_ll/n:.4f}  (lower = better)",
            f"Brier      model / market   : {self.model_brier/n:.4f} / {self.market_brier/n:.4f}",
            f"Accuracy   model / market   : {self.model_acc/n:.1%} / {self.market_acc/n:.1%}",
            "",
            f"Bets placed                : {self.bets}  (win rate {self.wins/b:.1%})",
            f"Flat-stake ROI (1u/bet)    : {self.flat_profit/b:+.2%}   ({self.flat_profit:+.1f}u)",
            f"Kelly: staked ${self.staked:,.0f}, profit ${self.profit:+,.2f}, ROI {self.profit/max(self.staked,1):+.2%}",
            f"Final bankroll             : ${self.bankroll:,.2f}   max drawdown {self.max_drawdown:.1%}",
            "",
            "Flat ROI by edge bucket:",
        ]
        order = ["0-3%", "3-5%", "5-8%", "8-12%", "12%+"]
        for k in sorted(self.buckets, key=order.index):
            cnt, pr = self.buckets[k]
            lines.append(f"  edge {k:<10} bets={cnt:<6} ROI={pr/max(cnt,1):+.2%}")
        verdict = ("MODEL BEATS MARKET on log loss - edge may be real, paper-trade to confirm"
                   if self.model_ll < self.market_ll else
                   "Market is sharper than the model (normal). Only small blended edges are plausible.")
        lines += ["", "Verdict: " + verdict]
        return "\n".join(lines)


def _bucket(edge: float) -> str:
    for lo, hi in ((0.0, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 0.12)):
        if lo <= edge < hi:
            return f"{int(lo*100)}-{int(hi*100)}%"
    return "12%+"


def run(matches: list[Match], cfg: BacktestConfig = BacktestConfig(),
        params: Optional[EloParams] = None) -> BacktestResult:
    elo = Elo(params)
    r = BacktestResult(bankroll=cfg.bankroll, peak=cfg.bankroll)
    for m in matches:
        if cfg.end and m.date > cfg.end:
            break
        scorable = (m.date >= cfg.start and m.completed and m.odds_w and m.odds_l
                    and min(elo.n[m.wkey], elo.n[m.lkey]) >= cfg.min_matches)
        if scorable:
            p_w = elo.prob_keys(m.wkey, m.lkey, m.surface, m.best_of, on=m.date)
            mk_w, _ = implied_from_decimal(m.odds_w, m.odds_l)
            bl_w = shrink(p_w, mk_w, cfg.w_model)
            r.n_pred += 1
            r.model_ll += log_loss(p_w, 1)
            r.market_ll += log_loss(mk_w, 1)
            r.blend_ll += log_loss(bl_w, 1)
            r.model_brier += (1 - p_w) ** 2
            r.market_brier += (1 - mk_w) ** 2
            r.model_acc += p_w > 0.5
            r.market_acc += mk_w > 0.5

            # evaluate both sides; outcome known only for scoring
            for p, odds, won in ((bl_w, m.odds_w, True), (1 - bl_w, m.odds_l, False)):
                if odds > cfg.max_odds:
                    continue
                price = 1 / odds  # what you actually pay, vig included
                edge = p - price
                if edge < cfg.min_edge:
                    continue
                f = min(kelly_binary(p, price) * cfg.kelly_fraction, cfg.max_bet_pct)
                stake = r.bankroll * f
                pnl = stake * (odds - 1) if won else -stake
                flat = (odds - 1) if won else -1.0
                r.bets += 1
                r.wins += won
                r.staked += stake
                r.profit += pnl
                r.flat_profit += flat
                r.bankroll += pnl
                r.peak = max(r.peak, r.bankroll)
                r.max_drawdown = max(r.max_drawdown, 1 - r.bankroll / r.peak)
                b = r.buckets.setdefault(_bucket(edge), [0, 0.0])
                b[0] += 1
                b[1] += flat
        elo.update(m)
    return r


def tune_w_model(matches: list[Match], cfg: BacktestConfig,
                 grid=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)) -> list[tuple[float, float]]:
    """Log loss of the blended probability for each model weight."""
    out = []
    for w in grid:
        c = BacktestConfig(**{**cfg.__dict__, "w_model": w})
        res = run(matches, c)
        out.append((w, res.blend_ll / max(res.n_pred, 1)))
    return out
