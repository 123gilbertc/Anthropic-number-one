# Polymarket NFL scan (OFFLINE FIXTURE DEMO: prices are illustrative, not live)

36 markets loaded, 33 priced. Bankroll $1,000; min edge 3%, Kelly fraction 0.25, per-position cap 3%, weekly cap 15%, slippage buffer 0.01, fee 0.00%.

## Opportunities (after fees, slippage, depth and exposure caps)

| question | side | kind | fair_prob | price | effective_price | edge | ev | stake_fraction | stake_usd | liquidity_usd | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Will the Washington Commanders make the playoffs? | NO | futures | 0.912 | 0.620 | 0.630 | 0.282 | 0.447 | 0.030 | 30.000 | 27000.000 | no book: last price used as bid and ask |
| Will the Minnesota Vikings win the NFC North? | YES | futures | 0.429 | 0.170 | 0.180 | 0.249 | 1.381 | 0.030 | 30.000 | 78302.918 | no book: last price used as bid and ask |
| Will the Chicago Bears make the playoffs? | NO | futures | 0.795 | 0.590 | 0.600 | 0.195 | 0.326 | 0.030 | 30.000 | 31000.000 | no book: last price used as bid and ask |
| Will the Detroit Lions win the NFC North? | NO | futures | 0.693 | 0.580 | 0.590 | 0.103 | 0.175 | 0.020 | 20.000 | 84105.808 | no book: last price used as bid and ask \| capped by max_game_exposure |
| Will the Kansas City Chiefs win the AFC Championship? | NO | futures | 0.817 | 0.730 | 0.740 | 0.077 | 0.104 | 0.030 | 30.000 | 89772.303 | no book: last price used as bid and ask |
| Will the Chicago Bears win the NFC North? | NO | futures | 0.934 | 0.860 | 0.870 | 0.064 | 0.074 | 0.000 | 0.000 | 37915.386 | no book: last price used as bid and ask \| no stake: max_game_exposure exhausted |
| Will the Green Bay Packers win the NFC North? | NO | futures | 0.801 | 0.730 | 0.740 | 0.061 | 0.082 | 0.000 | 0.000 | 84422.513 | no book: last price used as bid and ask \| no stake: max_game_exposure exhausted |
| Will the Baltimore Ravens win the AFC Championship? | NO | futures | 0.881 | 0.810 | 0.820 | 0.061 | 0.074 | 0.010 | 10.000 | 76225.389 | no book: last price used as bid and ask \| capped by max_weekly_exposure |
| Will the Philadelphia Eagles win Super Bowl LXI? | NO | futures | 0.944 | 0.880 | 0.890 | 0.054 | 0.061 | 0.000 | 0.000 | 113160.741 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |
| Spread: Eagles (-4.5) | NO | spread | 0.532 | 0.479 | 0.489 | 0.043 | 0.089 | 0.000 | 0.000 | 117786.181 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |
| Will the Kansas City Chiefs win Super Bowl LXI? | NO | futures | 0.904 | 0.860 | 0.870 | 0.034 | 0.040 | 0.000 | 0.000 | 6504.000 | book \| no stake: max_weekly_exposure exhausted |
| Will the Chiefs win 11+ regular season games? | YES | futures | 0.654 | 0.610 | 0.620 | 0.034 | 0.055 | 0.000 | 0.000 | 12000.000 | no book: last price used as bid and ask \| no stake: max_weekly_exposure exhausted |

## Risk-free inconsistencies

None found.

## Consistency checks (market vs logic / simulation)

| check | team | category | market_id | market_mid | sim_p | gap |
|---|---|---|---|---|---|---|
| divergence | CHI | playoffs | 552333 | 0.410 | 0.204 | -0.205 |
| divergence | MIN | division | 552328 | 0.170 | 0.429 | 0.259 |
| divergence | WAS | playoffs | 552334 | 0.380 | 0.088 | -0.292 |

## All priced markets (largest fair-vs-price gap first)

| market_id | kind | type | question | yes_outcome | game_id | fair_yes | price_yes | best_bid | best_ask | fair_minus_price | liquidity |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 552334 | futures | playoffs | Will the Washington Commanders make the playoffs? | Yes |  | 0.088 | 0.380 |  |  | -0.292 | 27000.000 |
| 552328 | futures | division | Will the Minnesota Vikings win the NFC North? | Yes |  | 0.429 | 0.170 |  |  | 0.259 | 78302.918 |
| 552333 | futures | playoffs | Will the Chicago Bears make the playoffs? | Yes |  | 0.204 | 0.410 |  |  | -0.205 | 31000.000 |
| 552326 | futures | division | Will the Detroit Lions win the NFC North? | Yes |  | 0.307 | 0.420 |  |  | -0.113 | 84105.808 |
| 552331 | futures | conference | Will the Kansas City Chiefs win the AFC Championship? | Yes |  | 0.183 | 0.270 |  |  | -0.087 | 89772.303 |
| 552329 | futures | division | Will the Chicago Bears win the NFC North? | Yes |  | 0.066 | 0.140 |  |  | -0.074 | 37915.386 |
| 552332 | futures | conference | Will the Baltimore Ravens win the AFC Championship? | Yes |  | 0.119 | 0.190 |  |  | -0.071 | 76225.389 |
| 552327 | futures | division | Will the Green Bay Packers win the NFC North? | Yes |  | 0.199 | 0.270 |  |  | -0.071 | 84422.513 |
| 552317 | futures | super_bowl | Will the Philadelphia Eagles win Super Bowl LXI? | Yes |  | 0.056 | 0.120 |  |  | -0.064 | 113160.741 |
| 552320 | futures | super_bowl | Will the Detroit Lions win Super Bowl LXI? | Yes |  | 0.030 | 0.085 |  |  | -0.055 | 173795.379 |
| 552316 | futures | super_bowl | Will the Kansas City Chiefs win Super Bowl LXI? | Yes |  | 0.096 | 0.145 | 0.140 | 0.150 | -0.054 | 150174.513 |
| 552311 | spread | spread | Spread: Eagles (-4.5) | Bears | 2026_03_PHI_CHI | 0.468 | 0.521 |  |  | -0.053 | 117786.181 |
| 552336 | futures | win_total | Will the Chiefs win 11+ regular season games? | Yes |  | 0.654 | 0.610 |  |  | 0.044 | 12000.000 |
| 552319 | futures | super_bowl | Will the Baltimore Ravens win Super Bowl LXI? | Yes |  | 0.060 | 0.100 |  |  | -0.040 | 174253.007 |
| 552310 | moneyline | moneyline | Eagles vs. Bears | Bears | 2026_03_PHI_CHI | 0.300 | 0.330 | 0.330 | 0.340 | -0.040 | 282686.835 |
| 552322 | futures | super_bowl | Will the Washington Commanders win Super Bowl LXI? | Yes |  | 0.002 | 0.035 |  |  | -0.033 | 87222.448 |
| 552323 | futures | super_bowl | Will the Los Angeles Rams win Super Bowl LXI? | Yes |  | 0.082 | 0.050 |  |  | 0.032 | 103609.458 |
| 552325 | futures | super_bowl | Will the Chicago Bears win Super Bowl LXI? | Yes |  | 0.007 | 0.030 |  |  | -0.023 | 128475.645 |
| 552330 | futures | conference | Will the Buffalo Bills win the AFC Championship? | Yes |  | 0.197 | 0.220 |  |  | -0.023 | 25696.896 |
| 552324 | futures | super_bowl | Will the Green Bay Packers win Super Bowl LXI? | Yes |  | 0.026 | 0.045 |  |  | -0.019 | 201499.621 |
| 552301 | spread | spread | Spread: Chiefs (-10.5) | Dolphins | 2026_03_KC_MIA | 0.506 | 0.497 | 0.480 | 0.490 | 0.016 | 116784.434 |
| 552335 | futures | win_total | Will the Eagles win 12+ regular season games? | Yes |  | 0.453 | 0.440 |  |  | 0.013 | 14000.000 |
| 552307 | moneyline | moneyline | Seahawks vs. Commanders | Commanders | 2026_03_SEA_WAS | 0.239 | 0.240 | 0.240 | 0.250 | -0.011 | 270016.885 |
| 552312 | total | total | Eagles vs. Bears: O/U 41.5 | Over | 2026_03_PHI_CHI | 0.489 | 0.478 |  |  | 0.011 | 70671.709 |
| 552309 | total | total | Seahawks vs. Commanders: O/U 40.5 | Over | 2026_03_SEA_WAS | 0.482 | 0.491 |  |  | -0.009 | 67504.221 |
| 552300 | moneyline | moneyline | Chiefs vs. Dolphins | Dolphins | 2026_03_KC_MIA | 0.158 | 0.140 | 0.140 | 0.150 | 0.008 | 280282.641 |
| 552305 | spread | spread | Spread: Ravens (-3.5) | Cowboys | 2026_03_BAL_DAL | 0.501 | 0.493 |  |  | 0.008 | 54841.660 |
| 552318 | futures | super_bowl | Will the Buffalo Bills win Super Bowl LXI? | Yes |  | 0.103 | 0.110 |  |  | -0.007 | 214365.165 |
| 552304 | moneyline | moneyline | Ravens vs. Cowboys | Cowboys | 2026_03_BAL_DAL | 0.386 | 0.370 | 0.370 | 0.380 | 0.006 | 131619.984 |
| 552321 | futures | super_bowl | Will the San Francisco 49ers win Super Bowl LXI? | Yes |  | 0.075 | 0.070 |  |  | 0.005 | 87393.219 |
| 552306 | total | total | Ravens vs. Cowboys: O/U 52.5 | Over | 2026_03_BAL_DAL | 0.505 | 0.502 |  |  | 0.003 | 32904.996 |
| 552302 | total | total | Chiefs vs. Dolphins: O/U 45.5 | Over | 2026_03_KC_MIA | 0.511 | 0.513 | 0.500 | 0.510 | 0.001 | 70070.660 |
| 552308 | spread | spread | Spread: Seahawks (-7.5) | Commanders | 2026_03_SEA_WAS | 0.497 | 0.497 |  |  | 0.000 | 112507.035 |
