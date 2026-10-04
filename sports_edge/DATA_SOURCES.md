# Data sources

Checked on **2026-10-02**. Full per-item evidence with source URLs is in
[`docs/provider_research_2026-10-02.md`](docs/provider_research_2026-10-02.md).

## Status labels

| Label | Meaning |
|---|---|
| **VERIFIED** | Read in the official documentation on the checked date. |
| **SNIPPET** | From an official-domain search result. The build environment's network policy blocked the page itself, so re-check exact numbers and field names. |
| **MEASURED** | Observed in our own recorded data. Nothing is MEASURED yet, because no live provider has been reachable from the build environment. |
| **UNKNOWN** | Not established. |

Public documentation is **not** proof that your account has the entitlement. Nothing here has been purchased.

## Summary decision

| Need | Candidate | Coverage / freshness | Cost | Status for V1 |
|---|---|---|---|---|
| Prediction-market prices (venue 1) | **Kalshi** REST + WebSocket | Order-book WebSocket with snapshot + delta and `seq` (SNIPPET) | Market data free with an account; the WebSocket needs a signed API key (SNIPPET) | **NOT CONNECTED.** Needs `KALSHI_KEY_ID` + private key. Recorder and parser are implemented. |
| Prediction-market prices (venue 2) | Polymarket international CLOB **or** Polymarket US | Separate products, APIs, fees and eligibility (SNIPPET). The international venue blocks US persons. | Free market data | Phase 5. Decide which product after eligibility is confirmed. **Never mix them.** |
| Sportsbook reference | **The Odds API v4** | h2h refreshes every 60 s pre-match / 40 s in-play (SNIPPET). Pinnacle via `regions=eu`, "may incur a delay" (SNIPPET). Circa not listed (UNKNOWN). | $0 (500 credits) to $30–$249/mo (SNIPPET) | Optional reference. A polled aggregate, **never instantaneous**. Alignment checks are mandatory. |
| Historical research | SportsDataIO Discovery Lab | Last season (free) or 1-day-delayed (paid) (SNIPPET) | $0 / $99–$149/mo (SNIPPET) | Historical only. **Must not power live decisions.** The separate Free Trial is **scrambled**: never use it for research. |
| Live NHL game state | SportsDataIO real-time / Sportradar push | Play-by-play typically 15–20 s behind TV (SportsDataIO, SNIPPET). Sportradar push needs a sales rep (SNIPPET). | **UNKNOWN** (sales quote) | **BLOCKED.** No source has passed a coverage + freshness + licensing test within budget. |
| Live NHL game state (free) | api-web.nhle.com | Undocumented, unauthenticated | $0 | **BLOCKED.** No official docs or terms were found (UNKNOWN). Not used until permitted use is documented. |
| MLB (follow-on) | statsapi.mlb.com | — | $0 | **BLOCKED for commercial/bulk use.** A copyright notice reportedly limits it to individual, non-commercial, non-bulk use (UNKNOWN, not read first-hand). |

**Live game-state dependency: BLOCKED.** As the specification requires, V1 still delivers recording, offline training, replay and paper research. `sports-edge live` refuses to run. It never silently falls back to stale or synthetic data.

## Kalshi (venue 1)

| Item | Value | Status |
|---|---|---|
| REST base | `https://external-api.kalshi.com/trade-api/v2`. `api.elections.kalshi.com` is also supported and covers all markets. | SNIPPET |
| Public market-data REST auth | Not required | SNIPPET |
| WebSocket | `wss://external-api-ws.kalshi.com/trade-api/ws/v2`. **Signed API-key handshake required**, even for public channels. | SNIPPET |
| Book messages | `orderbook_snapshot`, then `orderbook_delta`, carrying `seq` (`sid` not confirmed). | SNIPPET |
| Price format | Dollar strings (`"0.4200"`) and fixed-point counts (`"13.00"`). NO levels use NO-leg prices by default. | SNIPPET |
| Book sides | YES and NO bids only. YES ask = 1 − best NO bid. | SNIPPET |
| Gap procedure | No official rule found. The docs mention a `get_snapshot` resync action. **Our policy:** invalidate the book and resubscribe for a snapshot. | SNIPPET / UNKNOWN |
| Rate limits (Basic) | 200 read / 100 write tokens per second, about 10 tokens per request | SNIPPET |
| Fee formula | `round_up(M × 0.07 × C × P × (1−P))`, with a reduced 0.0175 variant (July 2026 schedule) | SNIPPET |
| Fee multiplier for KXNHLGAME / KXMLBGAME | Read `GET /series/{ticker}` at runtime | **UNKNOWN** |
| `KXNHLGAME` includes OT/shootout? | **Not confirmed.** An official college-hockey market says "inclusive of overtime but excluding shootouts". | **UNKNOWN.** `sports-edge discover` prints each market's rules text and its hash for human confirmation. |
| History | Candlesticks at 1 / 60 / 1440-minute intervals. Settled markets move to `/historical/...` after a cutoff. | SNIPPET |
| Permitted storage / redistribution | Not reviewed | **UNKNOWN.** Raw payloads are stored only locally. Do not redistribute. |

The fee model in code (`pricing/fees.py`, `KALSHI_FEE_RATE`) is labelled **UNVERIFIED** until the series multiplier is read from the API.

## Polymarket (venue 2, not started)

- **International:** CLOB at `https://clob.polymarket.com` (VERIFIED). Market WebSocket at `wss://ws-subscriptions-clob.polymarket.com/ws/market` (SNIPPET). Sports taker fee is `C × 0.05 × p × (1−p)` (SNIPPET). **US persons are blocked** (SNIPPET).
- **Polymarket US** is a separate CFTC-regulated exchange (SNIPPET):
  - REST at `https://api.polymarket.us/v1`, WebSocket at `wss://api.polymarket.us/v1/ws/markets`.
  - 20 requests per second per key.
  - Taker coefficient 0.0695; the unit basis is UNKNOWN.

Implementation: `adapters/polymarket.py` covers the parser (`book`, `price_change`, `tick_size_change`) and a taker fee model. **Fee rounding UNKNOWN**: we round up to the cent (conservative). No sequence numbers are documented for this channel, so after a reconnect every book stays invalid until a fresh `book` message arrives. There is no transport yet.

## The Odds API v4

| Item | Value | Status |
|---|---|---|
| Sport keys | `icehockey_nhl`, `baseball_mlb` | SNIPPET |
| Market | `h2h` (moneyline). For NHL, this must be matched to the contract's OT/SO rule. | SNIPPET |
| Cost | Live: markets × regions credits. Historical: 10 × markets × regions. | SNIPPET |
| Plans | 500 free credits; $30 (20K), $59 (100K), $119 (5M), $249 (15M) per month | SNIPPET |
| Historical | 5-minute snapshots since Sep 2022, paid plans only | SNIPPET |
| Pinnacle / Circa | Pinnacle: `eu` region, delayed. Circa: not listed. | SNIPPET / UNKNOWN |

5-minute snapshots cannot support intraminute entry claims. Any replay built on them is labelled **approximate**.

## LLM providers (shadow mode only)

| Provider | Model IDs seen | Price $/MTok (input/output) | Status |
|---|---|---|---|
| Anthropic | `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5-20251001` | 10/50, 4/20, 2/10, 1/5 | **VERIFIED** |
| OpenAI | `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna` | 10/50, 2/10, 0.10/0.50 | SNIPPET |
| xAI | `grok-4.7`, `grok-4.3` | 2/6, 1.25/2.50 | SNIPPET |
| Google | `gemini-3.8-flash` (price doubles 2027-01-01) | 1.35/6.75 | SNIPPET |

The Anthropic adapter is wired into the server. OpenAI/xAI (chat-completions shape) and Google (generateContent) adapters exist in `llm/providers.py` but are not yet wired into settings. All of them are untested against the live APIs from the build environment. The model ID and prices are **configuration** (`LLM_ANTHROPIC_MODEL`, `*_USD_PER_MTOK`), not code. Log every change to them.

## Monthly cost scenarios (planning ceiling $500/mo; nothing purchased)

| Scenario | Recurring | One-time | Notes |
|---|---|---|---|
| A. Research only (today) | ~$0–$30 | $0 | Kalshi data free; Odds API free or $30 tier; local Postgres |
| B. A + historical odds | ~$59–$119 | — | Odds API historical needs a paid plan |
| C. B + Discovery Lab | +$99–$149 | — | Delayed/historical only |
| D. C + LLM shadow reviews | +~$10–$25 | — | Capped by `LLM_MONTHLY_BUDGET_USD`; material triggers only |
| E. Licensed live NHL feed | **UNKNOWN** | **UNKNOWN** | Sales quote needed (SportsDataIO real-time / Sportradar). Required before live decisions. |

Hosting is not needed until a live feed exists. A small VM would cost roughly $10–$40/mo (not priced here).

## Open checks (require credentials or a human)

1. `sports-edge discover --series KXNHLGAME`: read the rules text and confirm OT/SO handling. Record the rules hash in each mapping.
2. `GET /series/KXNHLGAME`: record the fee multiplier, then set `KALSHI_FEE_RATE`.
3. `sports-edge record <tickers>`: capture real WebSocket messages and confirm field names (`sid`, `seq`, `yes_dollars[_fp]`, `delta_fp`). Then mark these MEASURED.
4. The Odds API: confirm `pinnacle` appears for `icehockey_nhl` with `regions=eu`. Measure the lag between `last_update` and receipt.
5. A human should read the NHL.com and MLB terms before any public league endpoint is used.
6. Get quotes for licensed live NHL play-by-play (SportsDataIO, Sportradar) and compare them with the $500/mo ceiling.
