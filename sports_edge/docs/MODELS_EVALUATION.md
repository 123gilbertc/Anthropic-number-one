# Models and evaluation

## Forecast target

The target is the probability that the **exact contract** pays out. For NHL V1 that means `NHL_INCLUDING_OT_SO`, which is "team wins the game, overtime and shootout included". That rule must still be confirmed against Kalshi's own rules text. A regulation-only contract needs a different (three-way) target and is rejected by the mapping gate.

## Features (`nhl_v1`)

| Feature | Meaning | When missing |
|---|---|---|
| `score_diff` | Selection's goals minus the opponent's | **critical**: abstain |
| `seconds_remaining_reg`, `frac_remaining` | Time left in regulation (0 in overtime) | **critical**: abstain |
| `score_diff_x_time` | Lead scaled by 1/√(time left): a lead matters more late | derived |
| `is_home`, `in_overtime` | Context | always known |
| `manpower_diff` | Skater advantage (power play) | use the reduced model |
| `own_net_empty`, `opp_net_empty` | Empty-net status | use the reduced model |
| `pregame_logit` | Pregame prior (from team strength) | use the reduced model |

**Not yet available, and therefore not used:**

- shot quality / expected goals
- goalie quality
- confirmed lineups
- rest and back-to-back schedules
- penalty time remaining

They will be added only when a licensed source provides them; we never invent them. Recency weighting and small-sample shrinkage are to be **estimated from training data**, not set by rule.

## Models

| ID pattern | Family | Inputs |
|---|---|---|
| `market_implied_baseline` | Baseline A | The market's own margin-removed probability at the same time |
| `nhl_logistic_full_*` | Baseline B: L2 logistic regression | All `nhl_v1` features |
| `nhl_logistic_reduced_*` | B, reduced | Critical features + context only |
| `nhl_gbm_*` | Challenger C: LightGBM | Same features |

- **Calibration:** Platt (sigmoid) scaling fitted on the *validation* window, which comes later in time than training. Isotonic calibration is available once validation sets are large.
- **Uncertainty band:** 10 bootstrap refits, each resampling whole games. `probability_low` / `probability_high` are the 10th / 90th percentiles. Conservative EV uses `probability_low`.
- **Reliability label** (shown only for VALIDATED models):
  - HIGH: band width < 6 pp
  - MEDIUM: < 12 pp
  - LOW: otherwise
- **Artifacts:** a pickle plus a JSON `ModelVersion` with SHA-256, training/validation ranges, features, hyperparameters and calibration method. Loading refuses a hash mismatch.

## Metrics: what they mean

- **Brier score:** average squared error between forecast and result. Lower is better; 0.25 is "always 50%".
- **Log loss:** heavily punishes confident mistakes. Lower is better; 0.693 is "always 50%".
- **Calibration table:** within each forecast bucket, how often the team actually won.
- **Winner accuracy:** how often the side we favored won. It is easily inflated by late, nearly decided states, so we **always report by game phase** (P1, P2, early P3, late P3).
- **Confidence intervals:** computed by resampling whole games (clustered bootstrap). Thousands of snapshots from one game are not thousands of independent data points.
- **Paper performance:** net ROI, total profit, turnover, drawdown, exposure and concentration. These are reported separately for all games, selected picks, dipped picks and filled picks.

## Protocol

1. **Split by whole game in time order:** train 50% → validation 20% → test 15% → final holdout 15%.
2. **Fit everything without the test set:** preprocessing, features, calibration, blending and trigger thresholds are all fit on train/validation only.
3. **Log every run:** each evaluation goes to `runs/experiments.jsonl`. Every touch of the holdout is logged with a counter, so repeated peeking is visible.
4. **Freeze before testing prospectively:** the strategy and thresholds are frozen; changes after losses create a new `strategy_version`.
5. **LLM forecasts:** these are scored separately as experimental forecasts. Historical LLM tests are exploratory, because models may know the outcomes. Only prospectively locked, timestamped forecasts count.

## Current state (honest)

- All trained models are **SYNTHETIC_ONLY**, fitted on `forecast/synthetic.py` data, so their metrics say nothing about real hockey.
- No real historical NHL dataset has been licensed or loaded.
- No model is VALIDATED, so in normal operation the engine produces **no value alerts**. That is the intended behavior.
