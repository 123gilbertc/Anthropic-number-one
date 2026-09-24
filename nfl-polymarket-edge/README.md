# nfl_edge: NFL probability engine + Polymarket edge scanner

A disciplined system for finding **profitable NFL positions on Polymarket**. It does not predict
games "psychically". It does the four things that actually make money in this market, and it
measures itself honestly against the strongest public predictor there is: the sharp closing line.

1. **Fair probabilities** from an ensemble of a tuned Elo model (margin of victory, home field, rest,
   quarterback changes), opponent-adjusted EPA team ratings, and the de-vigged sportsbook line,
   stacked with a walk-forward logistic model.
2. **Edge detection** against live Polymarket order books after fees and slippage, plus free-money
   checks: intra-market arbitrage (YES + NO < $1) and cross-market consistency (a team's Super Bowl
   price can never exceed its conference or playoff price).
3. **Futures pricing** from a Monte Carlo season simulator with real NFL playoff rules and
   tiebreakers, which is where crowd prices are usually sloppiest (Super Bowl, division, playoffs,
   win totals).
4. **Bankroll discipline**: quarter-Kelly sizing, per-position and per-week exposure caps, and
   closing-line-value (CLV) tracking, the metric professionals use to know whether a process is
   real before the profit-and-loss statement can tell them.

## What to expect (read this first)

| Metric, 2015-2024 regular + post season | Closing line | Target for this system |
|---|---|---|
| Straight-up accuracy | 66.2% | 66-68% |
| Log-loss (lower is better) | 0.613 | ≤ 0.613 |
| Sportsbook vig on moneylines | ~3.0% | n/a |
| ROI betting every favorite at the close | -3.0% | n/a |

No public model beats the NFL closing line by a meaningful margin, and this one does not claim to.
The profit thesis is different: **Polymarket is not the closing line.** Its NFL books are thinner,
priced by fewer participants, slower to move on news, and structurally prone to the
favorite-longshot bias. Edge comes from buying Polymarket prices that lag a calibrated fair value,
sized so that variance cannot ruin you, and from recording every price so the edge can be measured
rather than assumed. Realistic outcomes for a well-run process are low single-digit percent ROI on
turnover with sizable variance. Anyone promising more is selling something.

## Layout

```
nfl_edge/
  config.py              paths, endpoints, StrategyConfig (Kelly fraction, caps, thresholds)
  odds.py                odds conversions, vig removal (multiplicative / additive / power / Shin), spread<->prob, Elo<->prob
  teams.py               team aliases -> nflverse codes, franchise continuity, divisions
  data/nflverse.py       cached loaders: games 1999-present (closing lines), weekly team EPA stats, injuries, depth charts
  models/elo.py          Elo with MOV multiplier, HFA, rest, QB change; walk-forward tuning
  models/epa.py          opponent-adjusted, exponentially-weighted, shrunk EPA/play ratings (strictly pre-game)
  models/market.py       de-vigged market probabilities, spread sigma fit, calibration tables
  models/calibration.py  log-loss, Brier, ECE, calibration tables, paired bootstrap
  models/ensemble.py     L2 logistic stacker on component logits; strict walk-forward by season
  sim/season.py          Monte Carlo season + playoffs with NFL tiebreakers -> futures fair values
  markets/polymarket.py  Gamma/CLOB client, market parsing, game matching, order books, snapshots
  strategy/kelly.py      Kelly for $1-payout shares with fees, fractional sizing, exposure caps
  strategy/edge.py       opportunity finder, arbitrage and consistency checks
  backtest/engine.py     walk-forward betting simulation vs closing lines and vs recorded snapshots, CLV
  pipeline.py            end-to-end orchestration used by the CLI
  cli.py                 update-data | fit | backtest | predict | simulate | scan | snapshot | clv
tests/                   offline test suite (network is blocked in tests)
scripts/                 snapshot loop and weekly routine
data/cache/              nflverse downloads (gitignored)
data/reports/            generated reports (gitignored)
data/snapshots/          recorded Polymarket prices (gitignored)
```

## Quick start

```bash
cd nfl-polymarket-edge
pip install -r requirements.txt && pip install -e .
python -m nfl_edge.cli update-data      # pull nflverse games + weekly team stats
python -m nfl_edge.cli fit              # tune Elo, fit EPA scale, fit the stacker -> data/models.json
python -m nfl_edge.cli backtest         # walk-forward evaluation + betting sim vs closing lines
python -m nfl_edge.cli predict          # fair probabilities for the upcoming week
python -m nfl_edge.cli simulate         # season simulation -> futures fair values
python -m nfl_edge.cli scan --bankroll 1000      # live Polymarket scan (needs network access to polymarket.com)
python -m nfl_edge.cli scan --fixture tests/fixtures/polymarket   # offline demo on recorded fixtures
python -m nfl_edge.cli snapshot         # record current Polymarket prices (run every 15 min)
python -m nfl_edge.cli clv              # evaluate recorded snapshots vs closing prices and results
pytest                                  # offline test suite
```

Docker: `docker compose up -d nfl-edge-snapshotter` starts the price recorder next to the existing n8n
service. `scripts/weekly.sh` runs the full weekly routine.

## Methodology

### Fair probability
* **Elo** (`models/elo.py`): FiveThirtyEight-style with a margin-of-victory multiplier and
  autocorrelation damping, home-field advantage (zero at neutral sites), a bye-week rest bonus, a
  penalty when the listed starting quarterback changes, and regression toward the mean between
  seasons. Parameters are tuned by coordinate descent on walk-forward log-loss, never on the
  seasons they are evaluated on.
* **EPA ratings** (`models/epa.py`): offensive and defensive expected points added per play from
  nflverse weekly team stats, opponent-adjusted iteratively, exponentially weighted by recency,
  shrunk toward the league mean with a pseudo-game prior, and carried across seasons with decay.
  Every rating used for a game is computed from games strictly before it.
* **Market** (`models/market.py`): moneylines de-vigged with Shin's method (which handles the
  favorite-longshot bias better than proportional scaling); before 2007 the closing spread is
  mapped through a normal margin model with a fitted sigma (~13.4 points).
* **Stacker** (`models/ensemble.py`): L2-regularised logistic regression on the logits of the
  components, refit each season on all prior seasons. The market gets most of the weight, as it
  should. The report shows whether the ensemble beats the market baseline with a paired bootstrap
  confidence interval; a null result is reported as a null result.

### Edge and sizing
For a share paying $1 bought at price *q* with fee *f* on winnings, the breakeven probability is
`q / (q + (1-q)(1-f))`. The scanner adds a slippage buffer to the best ask, computes
`edge = fair_prob - breakeven`, and only surfaces positions with `edge >= min_edge` (default 3%)
and enough depth at the touch. Stakes are fractional Kelly (default 0.25) capped per position (3%
of bankroll), per game (5%) and per week (15%). The same logic runs on the NO side using 1 minus
the best bid.

### Season simulation
`sim/season.py` fixes played results, draws the remaining schedule from Elo probabilities, applies
the NFL tiebreaker ladder, seeds seven teams per conference, and plays the bracket with reseeding
and a neutral Super Bowl. Output is per-team probability of playoffs, division, top seed,
conference, Super Bowl, and the full win-total distribution, which price the corresponding
Polymarket futures.

### Backtesting without fooling yourself
* Walk-forward only: the model for season *S* never sees any game from season *S* or later.
* Betting simulation is priced at the **vigged** closing moneyline, so the vig is paid.
* Bootstrap confidence intervals on ROI; per-season tables so one lucky year cannot hide.
* Model CLV (fair minus de-vigged close) is reported on every bet taken.
* Once the snapshot loop has recorded live Polymarket prices, `clv` and `backtest_snapshots` measure
  real entry prices against real closes and results. That is the number that decides whether to
  keep going.

## Polymarket access
The scanner talks to `gamma-api.polymarket.com` and `clob.polymarket.com`. If those hosts are
blocked in your environment, allow them or run the scanner from a machine that can reach them; the
rest of the system works offline from the nflverse cache.

## Disclaimer
This is research software. Prediction-market and sports-betting positions can lose their entire
stake. Check the legality of prediction markets in your jurisdiction. Nothing here is financial advice.
