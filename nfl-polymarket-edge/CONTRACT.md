# Module contract

Every module below is implemented against this contract so the pieces compose.
Foundation (already written, tested, DO NOT EDIT): `nfl_edge/config.py`, `nfl_edge/odds.py`,
`nfl_edge/teams.py`, `nfl_edge/data/nflverse.py`. If you find a bug there, report it; do not patch it.

Allowed dependencies: numpy, pandas, scipy, requests, stdlib. No sklearn/statsmodels/torch.
Python 3.11. Tests use pytest; **tests must not hit the network** (Polymarket is unreachable here).
nflverse data is cached under `data/cache/` (`games.csv`, `stats_team_week_<1999..2026>.csv`).

## Data facts (from `nfl_edge.data.load_games()`)
* One row per game 1999-2026, sorted by season, week, game_date. `game_type` in REG/WC/DIV/CON/SB.
* `spread_line` = expected (home - away) margin: +3.0 means home favored by 3. `total_line` = O/U.
* `result` / `margin` = home_score - away_score. `home_win` = 1/0/0.5(tie)/NaN(unplayed). `played` bool.
* `home_moneyline`/`away_moneyline` American odds, complete from 2007. `market_prob` = devigged
  home moneyline prob when present else normal-model spread prob. This is the **market baseline**.
* `home_team_c`/`away_team_c` are franchise-canonical codes (use these, not home_team/away_team).
* `home_rest`/`away_rest` days since last game (7 = normal, >=10 after a bye, first week ~ 7+).
* `location` = "Home" or "Neutral" (Super Bowl, London/Germany games). `div_game` 0/1.
* `home_qb_name`/`away_qb_name` listed starters; `qb_change_home/away` 1 if changed from previous game.
* 2026 season is live: weeks 1-2 played, week 3 games on 2026-09-24..28, closing lines posted for weeks 3-4.
* Weekly team stats (`load_team_week_stats`): season, week, team, season_type, game_id, opponent_team,
  attempts, completions, passing_epa, carries, rushing_epa, sacks_suffered, passing_cpoe, ... plus
  `team_c`/`opponent_c` canonical codes. Offensive EPA of team X in game G is the defensive EPA
  allowed by X's opponent in G (join on game_id).

## Look-ahead rule (non-negotiable)
Any prediction for game G may use only games whose `game_date` < G's game_date (or same date but
earlier week). Season-level fits use only seasons strictly before the test season. Rating updates
happen only after a game is played. Tests must include a leakage test: shifting a game's outcome
must not change predictions for games before it.

---
## nfl_edge/models/elo.py
```python
@dataclass
class EloParams:
    k: float = 20.0                # base update
    hfa: float = 55.0              # home-field Elo points (0 when location == "Neutral")
    mov: bool = True               # 538-style margin multiplier: ln(|margin|+1) * 2.2/(0.001*elo_diff_winner + 2.2)
    season_regress: float = 0.33   # fraction of (rating - mean) removed at season start
    rest_bonus: float = 25.0       # Elo points for the rested side when rest >= 10 and the other < 10
    qb_change_penalty: float = 0.0 # Elo points subtracted when qb_change flag = 1
    playoff_mult: float = 1.0      # multiplies elo_diff in playoff games (538 used 1.2)
    mean: float = 1500.0

class EloModel:
    def __init__(self, params: EloParams | None = None): ...
    def run(self, games: pd.DataFrame) -> pd.DataFrame
        # Input: output of load_games() (played + unplayed). Sequential, NO look-ahead.
        # Returns a copy with: elo_home_pre, elo_away_pre, elo_diff (adjusted, home-away incl hfa/rest/qb),
        # elo_prob (P home win), elo_spread (= elo_diff / ELO_POINTS_PER_SPREAD_POINT). Unplayed games get
        # predictions from the latest ratings and do not update ratings.
    def ratings(self) -> dict[str, float]      # current post-run ratings (canonical codes)
    def predict(self, home: str, away: str, neutral: bool = False, home_rest: int = 7, away_rest: int = 7,
                playoff: bool = False) -> float  # P(home win) from current ratings

def fit_elo(games: pd.DataFrame, train_seasons: range | list[int], grid: dict | None = None,
            metric: str = "logloss") -> tuple[EloParams, pd.DataFrame]
    # Coordinate-descent / grid search over k, hfa, season_regress, rest_bonus (and mov on/off) minimizing
    # walk-forward log-loss on played games in train_seasons (warm-up seasons before train_seasons are
    # allowed for rating state only). Returns best params and a tidy results table of every combo tried.
def save_params(params: EloParams, path) / load_params(path) -> EloParams   # JSON
```
Measured quality (2015-2024 played games with moneylines): Elo log-loss ≈ 0.633-0.636, accuracy ≈ 64-65%; the closing-line baseline on the same rows is 0.613 / 66.1%. No Elo variant reaches the market; that is expected.

## nfl_edge/models/epa.py
```python
def team_game_epa(team_week: pd.DataFrame) -> pd.DataFrame
    # One row per (game_id, team_c): off_plays, off_epa (total), off_epa_pp, def_epa_pp (opponent's offensive
    # epa per play in that game), pass_epa_pp, rush_epa_pp, cpoe. Regular + post season.
def epa_ratings(games: pd.DataFrame, team_game: pd.DataFrame, halflife: float = 5.0, prior_games: float = 6.0,
                carryover: float = 0.5, iters: int = 5) -> pd.DataFrame
    # For every game row in `games` compute PRE-GAME ratings for home and away using only prior games:
    # exponentially-weighted (by games, halflife) offensive and defensive EPA/play, opponent-adjusted
    # iteratively (`iters`), shrunk toward the league mean with `prior_games` pseudo-games, and carried
    # into a new season with weight `carryover`. Returns games with columns:
    # home_off_epa, home_def_epa, away_off_epa, away_def_epa, epa_diff (home net - away net), epa_n_home, epa_n_away.
def epa_prob(epa_diff, scale: float) -> np.ndarray   # logistic map; `fit_epa_scale(games)` returns scale by MLE.
```
Efficiency: must run over all 7.5k games in < 30s. Vectorize per team where possible.

## nfl_edge/models/market.py
```python
def market_probs(games: pd.DataFrame, devig: str = "shin") -> pd.Series   # P(home) devigged by `devig` when ML present, else spread model
def fit_spread_sigma(games: pd.DataFrame) -> float                          # MLE sigma of margin | spread_line on played games
def spread_calibration(games: pd.DataFrame, bins=...) -> pd.DataFrame       # bucketed spread -> empirical home win %
def line_to_prob_table(sigma) -> pd.DataFrame                                # -14..+14 by 0.5 -> P(home)
```

## nfl_edge/models/calibration.py
```python
def log_loss(y, p) -> float; def brier(y, p) -> float; def accuracy(y, p) -> float   # y may contain 0.5 ties (counted half)
def calibration_table(y, p, bins: int = 10) -> pd.DataFrame  # bin, p_mean, y_mean, n, gap
def ece(y, p, bins: int = 10) -> float
def paired_bootstrap(y, p_a, p_b, n: int = 2000, seed: int = 0) -> dict   # logloss diff a-b, 95% CI, p(a better)
def summarize(y, probs: dict[str, np.ndarray]) -> pd.DataFrame  # model -> logloss, brier, acc, ece, n
```

## nfl_edge/models/ensemble.py
```python
class LogisticStacker:
    # L2-regularised logistic regression on logit-transformed component probabilities (scipy.optimize).
    def __init__(self, features: list[str], l2: float = 1.0, use_intercept: bool = True)
    def fit(self, df: pd.DataFrame, y_col: str = "home_win") -> "LogisticStacker"
    def predict_proba(self, df: pd.DataFrame) -> np.ndarray
    coef_: dict[str, float]; intercept_: float
def add_logits(df, cols) -> df   # adds f"{col}_logit" with clipping to [0.01, 0.99]
def walk_forward(df: pd.DataFrame, features: list[str], first_test: int, last_test: int | None = None,
                 min_train_seasons: int = 5, l2: float = 1.0) -> pd.DataFrame
    # For each test season S in [first_test, last_test]: fit on played games with season < S, predict season S.
    # Returns df restricted to test seasons with `fair_prob` and per-season coefficients in attrs["coefs"].
def fit_final(df, features, l2) -> LogisticStacker   # fit on all played games (for live use)
def save_model / load_model (JSON)
```
Note: with `market_prob` among the features the stacker should put most weight on the market; report
whether the ensemble beats the market baseline with `calibration.paired_bootstrap`. Honest result expected:
tiny or no improvement at the closing line. That is fine; document it.

## nfl_edge/markets/polymarket.py
```python
@dataclass
class OrderBook: bids: list[tuple[float, float]]; asks: list[tuple[float, float]]  # (price, size) sorted best-first
    best_bid, best_ask, mid, spread properties; depth_usd(side, max_price_impact) -> float
@dataclass
class PolyMarket:
    market_id: str; condition_id: str; question: str; slug: str; event_slug: str; event_title: str
    outcomes: list[str]; token_ids: list[str]; prices: list[float]; end_date: str | None
    liquidity: float; volume: float; active: bool; closed: bool
    kind: str  # "moneyline" | "spread" | "total" | "futures" | "other"
    home: str | None; away: str | None; team: str | None; line: float | None
    yes_index: int   # which outcome/token the "fair_prob" refers to
    book: OrderBook | None = None; raw: dict
class PolymarketClient:
    def __init__(self, gamma_url=config.GAMMA_API, clob_url=config.CLOB_API, session=None,
                 fixture_dir: str | Path | None = None, timeout: float = 15.0)
        # fixture_dir: read JSON from files instead of HTTP (tests + offline dev)
    def list_nfl_events(self, active=True, closed=False, limit=100, tag_slug="nfl") -> list[dict]   # paginate offset
    def markets_from_events(self, events) -> list[PolyMarket]
    def orderbook(self, token_id) -> OrderBook          # GET {clob}/book?token_id=
    def enrich_with_books(self, markets) -> list[PolyMarket]
    def price_history(self, token_id, interval="max", fidelity=60) -> pd.DataFrame  # {clob}/prices-history?market=
def parse_market(market: dict, event: dict) -> PolyMarket | None
    # Gamma fields may be JSON-encoded strings: outcomes, outcomePrices, clobTokenIds. Handle both str and list.
    # Classify kind via sportsMarketType/question text ("vs", "spread", "O/U", "Super Bowl", "win the AFC", "make the playoffs",
    # "win the NFC East", "win <n>+ games"). Extract teams with nfl_edge.teams.teams_in_text.
def match_game_markets(markets, games: pd.DataFrame) -> pd.DataFrame
    # join moneyline/spread/total markets to nflverse game rows by canonical team pair within +-3 days of game_date.
    # columns: market_id, game_id, home_team_c, away_team_c, kind, yes_team (code whose win = YES), price_yes, ...
def fee_for_market(market: PolyMarket, default: float = 0.0) -> float
def snapshot(markets, path: Path, ts: str) -> int    # append JSONL rows (ts, market_id, token_id, best_bid, best_ask, mid, liquidity)
def load_snapshots(path) -> pd.DataFrame
```
Provide realistic fixtures under `tests/fixtures/polymarket/` (events.json, book_<token>.json) shaped like the
real Gamma/CLOB responses (event: title, slug, startDate, endDate, tags, markets[]; market: id, question,
conditionId, slug, outcomes, outcomePrices, clobTokenIds, endDate, liquidity, volume, active, closed,
sportsMarketType, line, groupItemTitle). Include a moneyline game market, a spread, a total, a Super Bowl
futures market, and a division winner.

## nfl_edge/strategy/kelly.py
```python
def kelly_fraction(p: float, decimal_odds: float) -> float             # full Kelly, clipped at 0
def kelly_share(p: float, price: float, fee: float = 0.0) -> float      # $1-payout share bought at `price`; fee on winnings
def fractional_kelly(p, price, fraction, cap, fee=0.0) -> float
def expected_value(p, price, fee=0.0) -> float                          # per $1 staked
def size_positions(opps: list["Opportunity"], bankroll: float, cfg: StrategyConfig) -> list["Opportunity"]
    # greedy by edge; enforce max_stake_fraction, max_game_exposure (same game_id), max_weekly_exposure.
def growth_rate(p, price, f) -> float  # expected log growth, for tests
```

## nfl_edge/strategy/edge.py
```python
@dataclass
class Opportunity:
    market_id, question, kind, side ("YES"|"NO"), token_id, fair_prob (of the side), price (best ask of side),
    effective_price (price + cfg.slippage_buffer, additive in price space, capped at 0.999), edge, ev, kelly, stake_fraction, stake_usd,
    liquidity_usd, game_id | None, teams: list[str], reason: str
def find_edges(markets: list[PolyMarket], fair_yes: dict[str, float], cfg=DEFAULT_STRATEGY, bankroll=1000.0) -> list[Opportunity]
    # Evaluate YES at ask and NO at (1 - best_bid) [= NO ask]; respect min_prob/max_prob, min_liquidity, min_edge.
    # Sorted by edge desc. Sizing via kelly.size_positions.
def find_arbitrage(markets) -> list[dict]   # YES ask + NO ask < 1 (per market) ; sum of asks over a full mutually-exclusive event < 1
def consistency_checks(markets, sim: pd.DataFrame | None) -> list[dict]  # P(SB) <= P(conf) <= P(playoffs) per team, division sums ~1
def opportunities_table(opps) -> pd.DataFrame
```

## nfl_edge/sim/season.py
```python
@dataclass
class SimConfig: n_sims: int = 20000; seed: int = 0; hfa_elo: float = 55.0; playoff_mult: float = 1.0
def simulate_season(games: pd.DataFrame, season: int, ratings: dict[str, float], cfg: SimConfig) -> pd.DataFrame
    # Uses played results for the season as fixed, simulates remaining REG games with Elo probs from `ratings`
    # (hfa; neutral when location=="Neutral"), builds standings, applies NFL tiebreakers (head-to-head, division
    # record, common games approx, conference record, points differential, coin flip), seeds 7 per conference,
    # simulates bracket (1 seed bye, reseeding, home field to better seed, neutral SB).
    # Returns per-team: mean_wins, p_playoffs, p_division, p_top_seed, p_conference, p_super_bowl, win_dist (dict).
def win_total_probs(result_wins_matrix, team) -> dict[int, float]   # P(wins >= n)
```
Vectorize with numpy (sims x games); must finish 20k sims in < 60s.

## nfl_edge/backtest/engine.py
```python
@dataclass
class BacktestConfig: start_season=2010; end_season=None; min_edge=0.02; kelly_fraction=0.25; max_stake=0.03;
                      flat_stake=0.01; devig="shin"; bet_types=("moneyline",)
@dataclass
class BacktestResult: bets: pd.DataFrame; summary: dict; by_season: pd.DataFrame
def backtest_vs_closing(pred: pd.DataFrame, prob_col: str, cfg: BacktestConfig) -> BacktestResult
    # Bet home or away moneyline at the *vigged* closing price when (p * decimal_odds - 1) > min_edge.
    # Tracks flat and fractional-Kelly bankroll paths, ROI, hit rate, max drawdown, per-season table,
    # bootstrap 95% CI for ROI, and "model CLV" = fair_prob - devigged market prob at the bets taken.
def backtest_snapshots(snapshots: pd.DataFrame, fair_probs: pd.DataFrame, results: pd.DataFrame, cfg) -> BacktestResult
    # Same on recorded Polymarket prices: entry at ask at snapshot time, settle on game result, CLV vs the last
    # snapshot before kickoff.
def max_drawdown(equity) -> float; def bootstrap_roi(profits, stakes, n=2000, seed=0) -> tuple[lo, hi]
def report_markdown(result) -> str
```
