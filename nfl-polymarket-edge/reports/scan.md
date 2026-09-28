# Polymarket NFL scan 2026-09-28T01:11:43Z

36 markets loaded, 33 priced. Bankroll $1,000; min edge 3%, Kelly fraction 0.25, per-position cap 3%, weekly cap 15%, slippage buffer 0.01, fee 0.00%.

## Opportunities (after fees, slippage, depth and exposure caps)

| question | side | kind | fair_prob | price | effective_price | edge | ev | stake_fraction | stake_usd | liquidity_usd | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Will the Minnesota Vikings win the NFC North? | YES | futures | 0.671 | 0.170 | 0.180 | 0.491 | 2.731 | 0.030 | 30.000 | 78302.918 | no book: last price used as bid and ask |
| Will the Washington Commanders make the playoffs? | NO | futures | 0.851 | 0.620 | 0.630 | 0.221 | 0.350 | 0.030 | 30.000 | 27000.000 | no book: last price used as bid and ask |
| Will the Green Bay Packers win the NFC North? | NO | futures | 0.947 | 0.730 | 0.740 | 0.207 | 0.279 | 0.020 | 20.000 | 84422.513 | no book: last price used as bid and ask \| capped by max_game_exposure |
| Will the Detroit Lions win the NFC North? | NO | futures | 0.768 | 0.580 | 0.590 | 0.178 | 0.302 | 0.000 | 0.000 | 84105.808 | no book: last price used as bid and ask \| no stake: max_game_exposure exhausted |
| Will the Chicago Bears make the playoffs? | NO | futures | 0.766 | 0.590 | 0.600 | 0.166 | 0.276 | 0.030 | 30.000 | 31000.000 | no book: last price used as bid and ask |
| Will the Kansas City Chiefs win the AFC Championship? | NO | futures | 0.827 | 0.730 | 0.740 | 0.087 | 0.118 | 0.030 | 30.000 | 89772.303 | no book: last price used as bid and ask |
| Will the Eagles win 12+ regular season games? | YES | futures | 0.532 | 0.440 | 0.450 | 0.082 | 0.182 | 0.010 | 10.000 | 14000.000 | no book: last price used as bid and ask \| capped by max_weekly_exposure |
| Will the Baltimore Ravens win the AFC Championship? | NO | futures | 0.900 | 0.810 | 0.820 | 0.080 | 0.098 | 0.000 | 0.000 | 76225.389 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |
| Spread: Eagles (-4.5) | NO | spread | 0.537 | 0.479 | 0.489 | 0.048 | 0.099 | 0.000 | 0.000 | 117786.181 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |
| Will the Kansas City Chiefs win Super Bowl LXI? | NO | futures | 0.912 | 0.860 | 0.870 | 0.042 | 0.049 | 0.000 | 0.000 | 6504.000 | book \| no stake: max_weekly_exposure exhausted |
| Ravens vs. Cowboys: O/U 52.5 | YES | total | 0.552 | 0.502 | 0.512 | 0.040 | 0.079 | 0.000 | 0.000 | 32904.996 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |
| Will the Chiefs win 11+ regular season games? | YES | futures | 0.657 | 0.610 | 0.620 | 0.037 | 0.059 | 0.000 | 0.000 | 12000.000 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |

## Risk-free inconsistencies

None found.

## Consistency checks (market vs logic / simulation)

| check | team | category | market_id | market_mid | sim_p | gap |
|---|---|---|---|---|---|---|
| divergence | CHI | playoffs | 552333 | 0.410 | 0.234 | -0.176 |
| divergence | DET | division | 552326 | 0.420 | 0.232 | -0.188 |
| divergence | GB | division | 552327 | 0.270 | 0.053 | -0.217 |
| divergence | MIN | division | 552328 | 0.170 | 0.671 | 0.501 |
| divergence | WAS | playoffs | 552334 | 0.380 | 0.149 | -0.231 |

## All priced markets (largest fair-vs-price gap first)

| market_id | kind | type | question | yes_outcome | game_id | fair_yes | price_yes | best_bid | best_ask | fair_minus_price | liquidity |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 552328 | futures | division | Will the Minnesota Vikings win the NFC North? | Yes |  | 0.671 | 0.170 |  |  | 0.501 | 78302.918 |
| 552334 | futures | playoffs | Will the Washington Commanders make the playoffs? | Yes |  | 0.149 | 0.380 |  |  | -0.231 | 27000.000 |
| 552327 | futures | division | Will the Green Bay Packers win the NFC North? | Yes |  | 0.053 | 0.270 |  |  | -0.217 | 84422.513 |
| 552326 | futures | division | Will the Detroit Lions win the NFC North? | Yes |  | 0.232 | 0.420 |  |  | -0.188 | 84105.808 |
| 552333 | futures | playoffs | Will the Chicago Bears make the playoffs? | Yes |  | 0.234 | 0.410 |  |  | -0.176 | 31000.000 |
| 552331 | futures | conference | Will the Kansas City Chiefs win the AFC Championship? | Yes |  | 0.173 | 0.270 |  |  | -0.097 | 89772.303 |
| 552329 | futures | division | Will the Chicago Bears win the NFC North? | Yes |  | 0.043 | 0.140 |  |  | -0.097 | 37915.386 |
| 552335 | futures | win_total | Will the Eagles win 12+ regular season games? | Yes |  | 0.532 | 0.440 |  |  | 0.092 | 14000.000 |
| 552332 | futures | conference | Will the Baltimore Ravens win the AFC Championship? | Yes |  | 0.100 | 0.190 |  |  | -0.090 | 76225.389 |
| 552316 | futures | super_bowl | Will the Kansas City Chiefs win Super Bowl LXI? | Yes |  | 0.088 | 0.145 | 0.140 | 0.150 | -0.062 | 150174.513 |
| 552311 | spread | spread | Spread: Eagles (-4.5) | Bears | 2026_03_PHI_CHI | 0.463 | 0.521 |  |  | -0.058 | 117786.181 |
| 552319 | futures | super_bowl | Will the Baltimore Ravens win Super Bowl LXI? | Yes |  | 0.048 | 0.100 |  |  | -0.052 | 174253.007 |
| 552320 | futures | super_bowl | Will the Detroit Lions win Super Bowl LXI? | Yes |  | 0.033 | 0.085 |  |  | -0.052 | 173795.379 |
| 552306 | total | total | Ravens vs. Cowboys: O/U 52.5 | Over | 2026_03_BAL_DAL | 0.552 | 0.502 |  |  | 0.050 | 32904.996 |
| 552336 | futures | win_total | Will the Chiefs win 11+ regular season games? | Yes |  | 0.657 | 0.610 |  |  | 0.047 | 12000.000 |
| 552330 | futures | conference | Will the Buffalo Bills win the AFC Championship? | Yes |  | 0.258 | 0.220 |  |  | 0.038 | 25696.896 |
| 552324 | futures | super_bowl | Will the Green Bay Packers win Super Bowl LXI? | Yes |  | 0.009 | 0.045 |  |  | -0.036 | 201499.621 |
| 552305 | spread | spread | Spread: Ravens (-3.5) | Cowboys | 2026_03_BAL_DAL | 0.528 | 0.493 |  |  | 0.035 | 54841.660 |
| 552307 | moneyline | moneyline | Seahawks vs. Commanders | Commanders | 2026_03_SEA_WAS | 0.215 | 0.240 | 0.240 | 0.250 | -0.035 | 270016.885 |
| 552317 | futures | super_bowl | Will the Philadelphia Eagles win Super Bowl LXI? | Yes |  | 0.087 | 0.120 |  |  | -0.033 | 113160.741 |
| 552301 | spread | spread | Spread: Chiefs (-10.5) | Dolphins | 2026_03_KC_MIA | 0.522 | 0.497 | 0.480 | 0.490 | 0.032 | 116784.434 |
| 552322 | futures | super_bowl | Will the Washington Commanders win Super Bowl LXI? | Yes |  | 0.003 | 0.035 |  |  | -0.032 | 87222.448 |
| 552308 | spread | spread | Spread: Seahawks (-7.5) | Commanders | 2026_03_SEA_WAS | 0.467 | 0.497 |  |  | -0.030 | 112507.035 |
| 552318 | futures | super_bowl | Will the Buffalo Bills win Super Bowl LXI? | Yes |  | 0.139 | 0.110 |  |  | 0.029 | 214365.165 |
| 552310 | moneyline | moneyline | Eagles vs. Bears | Bears | 2026_03_PHI_CHI | 0.315 | 0.330 | 0.330 | 0.340 | -0.025 | 282686.835 |
| 552304 | moneyline | moneyline | Ravens vs. Cowboys | Cowboys | 2026_03_BAL_DAL | 0.405 | 0.370 | 0.370 | 0.380 | 0.025 | 131619.984 |
| 552325 | futures | super_bowl | Will the Chicago Bears win Super Bowl LXI? | Yes |  | 0.007 | 0.030 |  |  | -0.023 | 128475.645 |
| 552302 | total | total | Chiefs vs. Dolphins: O/U 45.5 | Over | 2026_03_KC_MIA | 0.489 | 0.513 | 0.500 | 0.510 | -0.021 | 70070.660 |
| 552300 | moneyline | moneyline | Chiefs vs. Dolphins | Dolphins | 2026_03_KC_MIA | 0.168 | 0.140 | 0.140 | 0.150 | 0.018 | 280282.641 |
| 552321 | futures | super_bowl | Will the San Francisco 49ers win Super Bowl LXI? | Yes |  | 0.084 | 0.070 |  |  | 0.014 | 87393.219 |
| 552312 | total | total | Eagles vs. Bears: O/U 41.5 | Over | 2026_03_PHI_CHI | 0.489 | 0.478 |  |  | 0.011 | 70671.709 |
| 552309 | total | total | Seahawks vs. Commanders: O/U 40.5 | Over | 2026_03_SEA_WAS | 0.495 | 0.491 |  |  | 0.004 | 67504.221 |
| 552323 | futures | super_bowl | Will the Los Angeles Rams win Super Bowl LXI? | Yes |  | 0.046 | 0.050 |  |  | -0.004 | 103609.458 |
