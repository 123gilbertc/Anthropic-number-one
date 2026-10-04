"""Machine-readable source registry mirrored from DATA_SOURCES.md for the dashboard.

Status vocabulary:
  VERIFIED  read in official documentation on ``checked``
  SNIPPET   official-site search result only (page fetch blocked); re-check
  MEASURED  observed from our own recorded data
  UNKNOWN   not established
  BLOCKED   cannot be used for live decisions right now
"""

from __future__ import annotations

CHECKED = "2026-10-02"

SOURCES = [
    {
        "name": "Kalshi market data (REST + WebSocket)",
        "role": "prediction-market prices (first venue)",
        "doc_status": "SNIPPET",
        "entitlement": "UNKNOWN (no API key configured)",
        "live_use": "NOT_CONNECTED",
        "notes": "WebSocket needs signed API key even for public channels. NHL game market "
                 "OT/shootout settlement UNKNOWN: read each market's rules before mapping.",
    },
    {
        "name": "Polymarket (international CLOB)",
        "role": "second venue (Phase 5)",
        "doc_status": "VERIFIED (CLOB host) / SNIPPET (WebSocket, fees)",
        "entitlement": "US persons blocked on international venue",
        "live_use": "NOT_CONNECTED",
        "notes": "Polymarket US is a separate CFTC-regulated exchange with its own API, fees "
                 "and eligibility. Never mix the two.",
    },
    {
        "name": "The Odds API v4",
        "role": "sportsbook reference quotes",
        "doc_status": "SNIPPET",
        "entitlement": "UNKNOWN (no API key configured)",
        "live_use": "NOT_CONNECTED",
        "notes": "Pinnacle under regions=eu with stated delay; Circa not listed (UNKNOWN). "
                 "Polled aggregate: never instantaneous.",
    },
    {
        "name": "SportsDataIO Discovery Lab",
        "role": "historical research",
        "doc_status": "SNIPPET",
        "entitlement": "UNKNOWN",
        "live_use": "BLOCKED",
        "notes": "Delayed/final-only: may not power live decisions. Free Trial data is "
                 "scrambled: never use it for research.",
    },
    {
        "name": "Live NHL game state (SportsDataIO real-time / Sportradar push)",
        "role": "live game feed",
        "doc_status": "SNIPPET / pricing UNKNOWN",
        "entitlement": "NONE",
        "live_use": "BLOCKED",
        "notes": "No qualified live game-state source within budget has been verified. Live "
                 "decisions are BLOCKED; recording, replay and offline research continue.",
    },
    {
        "name": "NHL public API (api-web.nhle.com)",
        "role": "candidate free game feed",
        "doc_status": "UNKNOWN (no official docs or terms found)",
        "entitlement": "UNKNOWN",
        "live_use": "BLOCKED",
        "notes": "Not used until permitted use is established and documented.",
    },
]
