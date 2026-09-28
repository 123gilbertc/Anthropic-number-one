"""nfl_edge: NFL win-probability engine and Polymarket edge scanner.

Design principle: the sharp closing line is the strongest single predictor of an
NFL game. Every model here is scored against it, and the strategy layer only
bets when a calibrated fair probability differs from a Polymarket price by more
than fees, slippage and a safety margin. There is no "psychic"; there is
calibration, discipline, and closing-line value.
"""

__version__ = "0.1.0"
