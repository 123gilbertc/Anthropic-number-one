"""Build the per-sport forecasters for a session.

* NHL: calibrated state models (``forecast.train``).
* NFL: calibrated win + tie state models (``sports.nfl_sim``).
* Tennis: exact point-to-match Markov model + shrunk serve/return strengths.
* MLB: base-out Markov model + team plate-appearance rates.

With synthetic inputs every model is ``SYNTHETIC_ONLY``. Real models need licensed
history; until then the strength books are absent and the models abstain.
"""

from __future__ import annotations

from sports_edge.domain.enums import Sport


def synthetic_forecasters(strength: dict, *, nhl_games: int = 300, nfl_games: int = 400,
                          nhl=None) -> dict:
    from sports_edge.forecast.train import train_synthetic
    from sports_edge.sports.mlb import MLBForecaster, MLBStrengthBook
    from sports_edge.sports.nfl_sim import train_synthetic_nfl
    from sports_edge.sports.nhl import NHLForecaster
    from sports_edge.sports.tennis import TennisForecaster, TennisStrengthBook

    out: dict = {}
    chain = nhl if nhl is not None else train_synthetic(n_games=nhl_games).forecaster
    out[Sport.NHL] = NHLForecaster(chain)
    out[Sport.NFL] = train_synthetic_nfl(nfl_games)
    if "TENNIS" in strength:
        out[Sport.TENNIS] = TennisForecaster(TennisStrengthBook.from_meta(strength["TENNIS"]))
    if "MLB" in strength:
        out[Sport.MLB] = MLBForecaster(MLBStrengthBook.from_meta(strength["MLB"]))
    return out


def describe(forecasters: dict) -> list[dict]:
    """Model cards for the UI: version, status, inputs, what it does not model."""
    cards = []
    for sport, f in forecasters.items():
        version = getattr(f, "version", None)
        status = getattr(f, "status", None) or (version.status if version else None)
        cards.append({
            "sport": sport.value,
            "model_version": f.model_version,
            "status": getattr(status, "value", status),
            "trained_on": getattr(version, "trained_on", None) if version else
            f"strength data label: {getattr(getattr(f, 'strength', None), 'label', 'UNKNOWN')}",
            "kind": {Sport.NHL: "calibrated logistic state model (+ reduced fallback)",
                     Sport.NFL: "calibrated logistic win + tie state models",
                     Sport.TENNIS: "exact point-game-set-match Markov model",
                     Sport.MLB: "base-out Markov run model"}[sport],
        })
    return cards
