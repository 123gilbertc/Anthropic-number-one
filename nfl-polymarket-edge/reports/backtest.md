# Walk-forward evaluation 2012-2025

Generated 2026-09-25T01:15:44Z. Every prediction for season S uses only games before S; Elo/EPA ratings update sequentially and never see the game they predict.

## Probability quality (lower log-loss / Brier is better)

| index | model | logloss | brier | acc | ece | n |
|---|---|---|---|---|---|---|
| 0 | closing line (multiplicative devig) | 0.6097 | 0.2103 | 0.6635 | 0.0158 | 3829 |
| 1 | closing line (Shin devig) | 0.6095 | 0.2102 | 0.6635 | 0.0144 | 3829 |
| 2 | Elo | 0.6287 | 0.2186 | 0.6510 | 0.0183 | 3829 |
| 3 | EPA ratings | 0.6377 | 0.2228 | 0.6340 | 0.0112 | 3829 |
| 4 | Elo+EPA stack (no market) | 0.6283 | 0.2185 | 0.6457 | 0.0195 | 3829 |
| 5 | ensemble (market+Elo+EPA) | 0.6097 | 0.2103 | 0.6638 | 0.0173 | 3829 |

## Does the ensemble beat the closing line?

Paired bootstrap, ensemble minus Shin-devigged closing line, log-loss: diff +0.00020, 95% CI [-0.00100, +0.00139], P(ensemble better) = 0.37.

Model-only stack minus closing line: diff +0.01879, 95% CI [+0.01300, +0.02452].

Read this honestly: a CI that spans zero means the model does not beat the market at the close. Edge must come from Polymarket prices that diverge from this fair value, not from out-modelling the sharps.

## Ensemble calibration

| bin | bin_lo | bin_hi | p_mean | y_mean | n | gap |
|---|---|---|---|---|---|---|
| 0 | 0.000 | 0.100 | 0.086 | 0.250 | 4 | 0.164 |
| 1 | 0.100 | 0.200 | 0.160 | 0.176 | 91 | 0.016 |
| 2 | 0.200 | 0.300 | 0.253 | 0.215 | 270 | -0.038 |
| 3 | 0.300 | 0.400 | 0.352 | 0.379 | 492 | 0.027 |
| 4 | 0.400 | 0.500 | 0.448 | 0.429 | 549 | -0.019 |
| 5 | 0.500 | 0.600 | 0.554 | 0.540 | 658 | -0.014 |
| 6 | 0.600 | 0.700 | 0.649 | 0.631 | 742 | -0.018 |
| 7 | 0.700 | 0.800 | 0.748 | 0.750 | 674 | 0.002 |
| 8 | 0.800 | 0.900 | 0.841 | 0.856 | 302 | 0.015 |
| 9 | 0.900 | 1.000 | 0.919 | 0.957 | 47 | 0.039 |

## Stacker weights by test season (logit space)

| season | market_shin_logit | elo_prob_logit | epa_prob_logit | intercept |
|---|---|---|---|---|
| 2012 | 0.775 | 0.244 | 0.035 | -0.006 |
| 2013 | 0.798 | 0.224 | 0.033 | -0.010 |
| 2014 | 0.818 | 0.236 | 0.006 | -0.005 |
| 2015 | 0.823 | 0.229 | 0.014 | -0.008 |
| 2016 | 0.803 | 0.246 | 0.009 | -0.011 |
| 2017 | 0.797 | 0.296 | -0.032 | -0.007 |
| 2018 | 0.819 | 0.293 | -0.048 | -0.009 |
| 2019 | 0.830 | 0.264 | -0.038 | -0.003 |
| 2020 | 0.845 | 0.245 | -0.040 | -0.012 |
| 2021 | 0.872 | 0.244 | -0.063 | -0.022 |
| 2022 | 0.900 | 0.225 | -0.093 | -0.027 |
| 2023 | 0.914 | 0.227 | -0.109 | -0.024 |
| 2024 | 0.930 | 0.205 | -0.116 | -0.020 |
| 2025 | 0.935 | 0.218 | -0.120 | -0.023 |

## Betting simulations at vigged closing moneylines

Flat stakes 1% of starting bankroll; Kelly path is quarter-Kelly capped at 3% per bet and 15% per week. ROI CI is a bootstrap over bets.

| strategy | bets | hit rate | flat ROI | ROI CI lo | ROI CI hi | Kelly final bankroll | Kelly max DD |
|---|---|---|---|---|---|---|---|
| ensemble @ min_edge 2% | 673 | 0.429 | -0.024 | -0.121 | 0.066 | 0.956 | 0.222 |
| ensemble @ min_edge 5% | 214 | 0.394 | 0.022 | -0.165 | 0.206 | 1.112 | 0.207 |
| Elo alone @ min_edge 5% (cautionary) | 2622 | 0.371 | -0.044 | -0.096 | 0.009 | 0.130 | 0.916 |
| every closing line (pays the vig) | 3828 | 0.664 | -0.030 | -0.052 | -0.008 | 0.996 | 0.004 |

## Model parameters

Elo: EloParams(k=20.0, hfa=45.0, mov=True, season_regress=0.5, rest_bonus=25.0, qb_change_penalty=60.0, playoff_mult=1.2, mean=1500.0)
EPA: {'halflife': 5.0, 'prior_games': 6.0, 'carryover': 0.5, 'iters': 5, 'scale': 8.396068257288777, 'hfa': 0.27478013713655586}
margin sigma 13.20, total sigma 13.44, ridge l2 5.0
