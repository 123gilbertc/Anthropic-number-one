"""Coverage registry: what each competition actually has, per data dimension.

Status vocabulary per cell:
  AVAILABLE      a configured source has delivered it (runtime evidence)
  FIXTURE_ONLY   only synthetic / recorded fixtures exercise it
  NOT_CONFIGURED a source exists in code but has no credentials / verified endpoint
  BLOCKED        a known blocker (licensing, missing source, unverified rules)
  UNKNOWN        not established
  NOT_MODELLED   the data might exist, but no model uses it yet

Each cell also carries the provider-fact label (VERIFIED / SNIPPET / MEASURED / UNKNOWN)
of the claim it rests on. Unknown coverage is shown as UNKNOWN, never as "0 games".
Nothing here promises every tennis match worldwide: competitions not listed are not
covered.
"""

from __future__ import annotations

from copy import deepcopy

DIMENSIONS = ("schedule", "live_score", "play_level", "history", "market", "licensing",
              "model")

_C = lambda status, label, note: {"status": status, "label": label, "note": note}  # noqa: E731

REGISTRY: list[dict] = [
    {"competition": "NHL", "sport": "NHL", "name": "NHL regular season & playoffs",
     "cells": {
         "schedule": _C("NOT_CONFIGURED", "SNIPPET", "Sportradar NHL / SportsDataIO: needs a "
                        "key and a verified endpoint version"),
         "live_score": _C("BLOCKED", "SNIPPET", "Sportradar push or SportsDataIO real-time: "
                          "commercial quote required (cost UNKNOWN)"),
         "play_level": _C("BLOCKED", "SNIPPET", "same feed as live score"),
         "history": _C("NOT_CONFIGURED", "SNIPPET", "SportsDataIO Discovery Lab (delayed / "
                       "historical only)"),
         "market": _C("NOT_CONFIGURED", "SNIPPET", "Kalshi KXNHLGAME series; OT/shootout rule "
                      "UNKNOWN until each market's rules text is read"),
         "licensing": _C("UNKNOWN", "UNKNOWN", "subscriber display, redistribution, model "
                         "training and LLM use not cleared"),
         "model": _C("FIXTURE_ONLY", "MEASURED", "calibrated state model trained on SYNTHETIC "
                     "games only"),
     }},
    {"competition": "NFL", "sport": "NFL", "name": "NFL regular season & playoffs",
     "cells": {
         "schedule": _C("NOT_CONFIGURED", "SNIPPET", "Sportradar NFL / SportsDataIO"),
         "live_score": _C("BLOCKED", "SNIPPET", "Sportradar push: commercial quote required"),
         "play_level": _C("BLOCKED", "SNIPPET", "same feed as live score"),
         "history": _C("NOT_CONFIGURED", "UNKNOWN", "licensed play-by-play history needed"),
         "market": _C("UNKNOWN", "UNKNOWN", "Kalshi NFL game series ticker and tie rule UNKNOWN"),
         "licensing": _C("UNKNOWN", "UNKNOWN", "not cleared"),
         "model": _C("FIXTURE_ONLY", "MEASURED", "win + tie state models trained on SYNTHETIC "
                     "simulated games only"),
     }},
    {"competition": "MLB", "sport": "MLB", "name": "MLB regular season & postseason",
     "cells": {
         "schedule": _C("NOT_CONFIGURED", "SNIPPET", "Sportradar MLB / SportsDataIO"),
         "live_score": _C("BLOCKED", "SNIPPET", "commercial quote required; statsapi.mlb.com "
                          "is not a commercial source (terms UNKNOWN)"),
         "play_level": _C("BLOCKED", "SNIPPET", "same feed as live score"),
         "history": _C("NOT_CONFIGURED", "UNKNOWN", "licensed plate-appearance history needed"),
         "market": _C("NOT_CONFIGURED", "SNIPPET", "Kalshi KXMLBGAME series; rules per market"),
         "licensing": _C("UNKNOWN", "UNKNOWN", "not cleared"),
         "model": _C("FIXTURE_ONLY", "MEASURED", "base-out Markov model with SYNTHETIC team "
                     "rates"),
     }},
    {"competition": "ATP-WTA-SINGLES", "sport": "TENNIS",
     "name": "ATP / WTA tour-level singles (main draw)",
     "cells": {
         "schedule": _C("NOT_CONFIGURED", "SNIPPET", "Sportradar tennis: which tours, rounds "
                        "and qualifying draws are covered is UNKNOWN until checked"),
         "live_score": _C("BLOCKED", "SNIPPET", "point-by-point push: commercial quote required"),
         "play_level": _C("BLOCKED", "SNIPPET", "point-level data needed for the Markov model"),
         "history": _C("NOT_CONFIGURED", "UNKNOWN", "serve/return point history by surface"),
         "market": _C("UNKNOWN", "UNKNOWN", "Kalshi tennis series and retirement rule UNKNOWN"),
         "licensing": _C("UNKNOWN", "UNKNOWN", "not cleared"),
         "model": _C("FIXTURE_ONLY", "MEASURED", "exact Markov model rules-tested; strengths "
                     "SYNTHETIC"),
     }},
    {"competition": "ATP-WTA-DOUBLES", "sport": "TENNIS", "name": "Tour-level doubles",
     "cells": {
         "schedule": _C("NOT_CONFIGURED", "UNKNOWN", "coverage UNKNOWN"),
         "live_score": _C("BLOCKED", "UNKNOWN", "coverage UNKNOWN"),
         "play_level": _C("BLOCKED", "UNKNOWN", "coverage UNKNOWN"),
         "history": _C("UNKNOWN", "UNKNOWN", "team serve data UNKNOWN"),
         "market": _C("UNKNOWN", "UNKNOWN", "UNKNOWN"),
         "licensing": _C("UNKNOWN", "UNKNOWN", "not cleared"),
         "model": _C("BLOCKED", "MEASURED", "scoring rules implemented and tested (no-ad, match "
                     "tiebreak); forecasts disabled until a doubles-specific model is tested"),
     }},
    {"competition": "TENNIS-CHALLENGER-ITF", "sport": "TENNIS",
     "name": "Challenger / ITF / qualifying",
     "cells": {d: _C("UNKNOWN", "UNKNOWN", "not evaluated; not covered") for d in DIMENSIONS}},
]


def coverage(overlays: dict[str, dict[str, dict]] | None = None) -> list[dict]:
    """Static registry plus runtime evidence (e.g. a discovery source that succeeded)."""
    out = deepcopy(REGISTRY)
    for row in out:
        for dim, cell in (overlays or {}).get(row["competition"], {}).items():
            row["cells"][dim] = cell
    return out
