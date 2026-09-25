# Walk-forward evaluation 2012-2025

Generated 2026-09-25T01:09:58Z. Every prediction for season S uses only games before S; Elo/EPA ratings update sequentially and never see the game they predict.

## Probability quality (lower log-loss / Brier is better)

| index | model | logloss | brier | acc | ece | n |
|---|---|---|---|---|---|---|
| 0 | closing line (multiplicative devig) | 0.6097 | 0.2103 | 0.6635 | 0.0158 | 3829 |
| 1 | closing line (Shin devig) | 0.6095 | 0.2102 | 0.6635 | 0.0144 | 3829 |
| 2 | Elo | 0.6290 | 0.2188 | 0.6510 | 0.0206 | 3829 |
| 3 | EPA ratings | 0.6442 | 0.2257 | 0.6306 | 0.0541 | 3829 |
| 4 | Elo+EPA stack (no market) | 0.6287 | 0.2186 | 0.6460 | 0.0203 | 3829 |
| 5 | ensemble (market+Elo+EPA) | 0.6097 | 0.2103 | 0.6645 | 0.0172 | 3829 |

## Does the ensemble beat the closing line?

Paired bootstrap, ensemble minus Shin-devigged closing line, log-loss: diff +0.00021, 95% CI [-0.00090, +0.00135], P(ensemble better) = 0.36.

Model-only stack minus closing line: diff +0.01919, 95% CI [+0.01343, +0.02485].

Read this honestly: a CI that spans zero means the model does not beat the market at the close. Edge must come from Polymarket prices that diverge from this fair value, not from out-modelling the sharps.

## Ensemble calibration

| bin | bin_lo | bin_hi | p_mean | y_mean | n | gap |
|---|---|---|---|---|---|---|
| 0 | 0.000 | 0.100 | 0.089 | 0.400 | 5 | 0.311 |
| 1 | 0.100 | 0.200 | 0.161 | 0.165 | 91 | 0.004 |
| 2 | 0.200 | 0.300 | 0.254 | 0.220 | 273 | -0.034 |
| 3 | 0.300 | 0.400 | 0.352 | 0.376 | 488 | 0.024 |
| 4 | 0.400 | 0.500 | 0.447 | 0.428 | 550 | -0.019 |
| 5 | 0.500 | 0.600 | 0.554 | 0.545 | 657 | -0.009 |
| 6 | 0.600 | 0.700 | 0.649 | 0.627 | 746 | -0.022 |
| 7 | 0.700 | 0.800 | 0.748 | 0.752 | 672 | 0.004 |
| 8 | 0.800 | 0.900 | 0.841 | 0.858 | 299 | 0.017 |
| 9 | 0.900 | 1.000 | 0.918 | 0.958 | 48 | 0.040 |

## Stacker weights by test season (logit space)

| season | market_shin_logit | elo_prob_logit | epa_prob_logit | intercept |
|---|---|---|---|---|
| 2012 | 0.788 | 0.230 | 0.034 | 0.003 |
| 2013 | 0.813 | 0.210 | 0.031 | -0.002 |
| 2014 | 0.835 | 0.218 | 0.004 | -0.004 |
| 2015 | 0.838 | 0.220 | 0.007 | -0.006 |
| 2016 | 0.813 | 0.243 | 0.001 | -0.011 |
| 2017 | 0.807 | 0.296 | -0.042 | -0.019 |
| 2018 | 0.832 | 0.288 | -0.056 | -0.024 |
| 2019 | 0.840 | 0.266 | -0.050 | -0.017 |
| 2020 | 0.854 | 0.248 | -0.052 | -0.026 |
| 2021 | 0.882 | 0.246 | -0.076 | -0.042 |
| 2022 | 0.909 | 0.224 | -0.102 | -0.054 |
| 2023 | 0.925 | 0.225 | -0.120 | -0.056 |
| 2024 | 0.940 | 0.204 | -0.127 | -0.054 |
| 2025 | 0.943 | 0.213 | -0.125 | -0.056 |

## Betting simulations at vigged closing moneylines

Flat stakes 1% of starting bankroll; Kelly path is quarter-Kelly capped at 3% per bet and 15% per week. ROI CI is a bootstrap over bets.

| strategy | bets | hit rate | flat ROI | ROI CI lo | ROI CI hi | Kelly final bankroll | Kelly max DD |
|---|---|---|---|---|---|---|---|
| ensemble @ min_edge 2% | 599 | 0.436 | -0.001 | -0.103 | 0.098 | 1.014 | 0.228 |
| ensemble @ min_edge 5% | 177 | 0.386 | 0.014 | -0.179 | 0.220 | 1.077 | 0.170 |
| Elo alone @ min_edge 5% (cautionary) | 2628 | 0.365 | -0.049 | -0.103 | 0.006 | 0.092 | 0.936 |
| every closing line (pays the vig) | 3828 | 0.664 | -0.030 | -0.052 | -0.008 | 0.996 | 0.004 |

## Model parameters

Elo: EloParams(k=20.0, hfa=45.0, mov=True, season_regress=0.5, rest_bonus=25.0, qb_change_penalty=40.0, playoff_mult=1.0, mean=1500.0)
EPA: {'halflife': 5.0, 'prior_games': 6.0, 'carryover': 0.5, 'iters': 5, 'scale': 8.304387993875613}
margin sigma 13.20, total sigma 13.44, ridge l2 5.0
