#!/usr/bin/env sh
# Weekly routine: refresh data, refit, re-run the honest backtest, publish this week's fair prices
# and futures values, then scan Polymarket. Safe to run any day; each step is idempotent.
set -eu
python -m nfl_edge.cli update-data
python -m nfl_edge.cli fit
python -m nfl_edge.cli backtest
python -m nfl_edge.cli predict
python -m nfl_edge.cli simulate
python -m nfl_edge.cli scan --bankroll "${BANKROLL:-1000}"
