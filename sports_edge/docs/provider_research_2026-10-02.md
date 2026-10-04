# Data-Provider Documentation Check — 2026-10-02

## Method and status legend (read first)

The egress proxy **blocked WebFetch** for almost every provider domain: docs.kalshi.com, kalshi.com, docs.polymarket.com, the-odds-api.com, developer.sportradar.com, www.nhl.com, www.mlb.com, statsapi.mlb.com, api-web.nhle.com, platform.openai.com, docs.x.ai, ai.google.dev. Only **platform.claude.com** and **raw.githubusercontent.com** could be fetched in full. Everything else comes from WebSearch restricted to the official domain. The search tool returns an index summary of the official page, not the raw page text.

| Status | Meaning |
|---|---|
| **VERIFIED** | I read the full official page today with WebFetch. |
| **SNIPPET** | Comes from today's WebSearch, restricted to the official domain, with the official page URL as the source. I could not open the page itself. Treat this as "very likely, but re-check before relying on exact numbers or field names". |
| **UNKNOWN** | I could not confirm it from an official source. Third-party claims are noted only as context. |

Public docs do not prove that a given account is entitled to a feature. All checks were made on 2026-10-02.

---

## 1. Kalshi

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| REST base URL (prod) | `https://external-api.kalshi.com/trade-api/v2` (recommended). `https://api.elections.kalshi.com/trade-api/v2` is "also supported". Demo: `https://external-api.demo.kalshi.co/trade-api/v2` (also `demo-api.kalshi.co`). | SNIPPET | https://docs.kalshi.com/getting_started/api_environments | 2026-10-02 |
| Coverage of the "elections" host | The prod Trade API covers ALL Kalshi markets, not just elections. | SNIPPET | https://docs.kalshi.com/getting_started/api_environments | 2026-10-02 |
| Public market-data REST needs auth? | No. Several public market-data endpoints need no API keys. | SNIPPET | https://docs.kalshi.com/getting_started/quick_start_market_data | 2026-10-02 |
| Separate keys per environment | Demo keys and prod keys are not interchangeable. | SNIPPET | https://docs.kalshi.com/getting_started/api_environments | 2026-10-02 |
| WebSocket URL | `wss://external-api-ws.kalshi.com/trade-api/ws/v2` (primary). `wss://api.elections.kalshi.com/trade-api/ws/v2` (shared host, supported). Demo: `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. | SNIPPET | https://docs.kalshi.com/websockets/websocket-connection | 2026-10-02 |
| WS needs API-key auth? | **Yes**, at the handshake, using the headers KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE and KALSHI-ACCESS-TIMESTAMP (ms). The signature is over `timestamp + "GET" + "/trade-api/ws/v2"`. Public-data channels still use the authenticated session, but add no per-channel authorization. | SNIPPET | https://docs.kalshi.com/getting_started/quick_start_websockets | 2026-10-02 |
| orderbook_delta channel flow | The server sends `orderbook_snapshot` first, then incremental `orderbook_delta` messages. Subscribe with `market_ticker` or `market_tickers`. | SNIPPET | https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| orderbook_snapshot format | Contains `market_ticker`, `market_id`, `yes_dollars` and `no_dollars`. Each level is a string pair `[price_dollars, count_fp]`, e.g. `["0.4200","13.00"]`. | SNIPPET | https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| orderbook_delta format | Fields: `price_dollars`, `delta_fp`, `side` ("yes"/"no"). Messages carry `seq`. Whether the envelope has `sid` was not confirmed (the page was not opened). | SNIPPET (sid: UNKNOWN) | https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| Price units | **Dollar strings** (`"0.4200"`), not integer cents. Counts are fixed-point strings (`_fp`). The legacy integer-cent fields `price`/`delta` were NOT confirmed as current. | SNIPPET | https://docs.kalshi.com/getting_started/fixed_point_migration ; https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| No-side pricing | By default, no-side levels are in **no-leg pricing**: a no delta at 0.30 means "no at 30c", which matches yes at 70c. Pass `use_yes_price: true` in the subscribe params to get a single yes scale. | SNIPPET | https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| Bids only? | Yes. The book returns only YES bids and NO bids (`orderbook_fp` → `yes_dollars`, `no_dollars`). Best YES ask = 1 − best NO bid, and best NO ask = 1 − best YES bid. | SNIPPET | https://docs.kalshi.com/getting_started/orderbook_responses | 2026-10-02 |
| Seq-gap handling | Channels are sequenced (`seq`). Changelog: an `update_subscription` action `get_snapshot` returns a fresh `orderbook_snapshot` without disturbing the delta stream. I found **no explicit official rule** saying "on gap, do X". The safe practice is to stop trading on the local book and resync with get_snapshot or a resubscribe. | SNIPPET (explicit gap procedure: UNKNOWN) | https://docs.kalshi.com/changelog ; https://docs.kalshi.com/websockets/orderbook-updates | 2026-10-02 |
| Rate limits (Basic tier) | Token buckets: **read 200 tokens/s, write 100 tokens/s**. The default cost is 10 tokens per request, so about 20 reads/s and 10 writes/s. The Basic write bucket holds 1 s of burst. Endpoint costs vary. | SNIPPET | https://docs.kalshi.com/getting_started/rate_limits | 2026-10-02 |
| Trading-fee formula (general) | `fees = round up(M × 0.07 × C × P × (1−P))`. M is a per-contract multiplier (default 1). Rounding is such that fee + positionCost rounds to a centicent. A reduced variant uses `0.0175`. | SNIPPET | https://kalshi.com/docs/kalshi-fee-schedule.pdf ("Fee Schedule for July 2026 – 7.7.26 Update") ; https://docs.kalshi.com/getting_started/fee_rounding | 2026-10-02 |
| Sports (NHL/MLB game) multiplier / maker fees | The schedule says some markets, including large sporting championships, have different fees and/or maker fees. I could not read the specific M for KXNHLGAME/KXMLBGAME. Use `GET /series/{ticker}` (fee fields) and `GET series fee changes` at runtime. | UNKNOWN | https://kalshi.com/docs/kalshi-fee-schedule.pdf ; https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes | 2026-10-02 |
| NHL game series ticker | `KXNHLGAME`. Official market pages exist, e.g. kalshi.com/markets/kxnhlgame/nhl-game/kxnhlgame-26may26colvgk. | SNIPPET | https://kalshi.com/markets/kxnhlgame/nhl-game/kxnhlgame-26may26colvgk | 2026-10-02 |
| KXNHLGAME includes OT/shootout? | **Not confirmed officially.** Third-party GitHub notes claim OT and SO count. Caution: an official Kalshi **college** hockey market reads "inclusive of overtime but excluding shootouts". Read `rules_primary`/`rules_secondary` from `GET /markets/{ticker}` before modeling. | UNKNOWN | https://kalshi.com/markets/kxncaahockeygame/college-hockey-game/kxncaahockeygame-26feb271900unhpro (college, for contrast) | 2026-10-02 |
| MLB game series ticker | `KXMLBGAME` ("Professional Baseball Game"), e.g. kxmlbgame-26sep301400phiatl ("Game 2: Philadelphia vs Atlanta"). | SNIPPET | https://kalshi.com/markets/kxmlbgame/professional-baseball-game | 2026-10-02 |
| Candlesticks | `GET /series/{series}/markets/{ticker}/candlesticks` (live), a batch variant, and event candlesticks. `period_interval` ∈ {1, 60, 1440} minutes. | SNIPPET | https://docs.kalshi.com/api-reference/market/get-market-candlesticks | 2026-10-02 |
| Historical data | Data is split into a live tier and a historical tier. Markets that settled before the `GET /historical/cutoff` timestamp are only available via `GET /historical/markets` and `GET /historical/markets/{ticker}/candlesticks`. | SNIPPET | https://docs.kalshi.com/getting_started/historical_data ; https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks | 2026-10-02 |

## 2. Polymarket (international vs US)

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| International CLOB host | `https://clob.polymarket.com` (official py-clob-client README) | VERIFIED | https://raw.githubusercontent.com/Polymarket/py-clob-client/main/README.md | 2026-10-02 |
| International market WS URL | `wss://ws-subscriptions-clob.polymarket.com/ws/market`. Subscribe with `{"assets_ids":[...],"type":"market"}` and send `PING` every 10 s. | SNIPPET | https://docs.polymarket.com/market-data/websocket/market-channel | 2026-10-02 |
| Market-channel message types | `book`, `price_change`, `tick_size_change`, `last_trade_price`. Also `best_bid_ask`, `new_market` and `market_resolved`, which need `custom_feature_enabled: true`. | SNIPPET | https://docs.polymarket.com/market-data/websocket/market-channel | 2026-10-02 |
| Gamma API | Not checked today (gamma-api.polymarket.com) | UNKNOWN | — | 2026-10-02 |
| International fees, sports | Takers only: `fee = C × feeRate × p × (1−p)`, with the **sports feeRate = 0.05**. Makers are not charged. Sports maker rebate = 20% of taker fees. | SNIPPET | https://docs.polymarket.com/trading/fees | 2026-10-02 |
| US eligibility (international) | The US is on the restricted list. polymarket.com trading is blocked for US users, who are directed to polymarket.us. VPN circumvention is prohibited. | SNIPPET | https://help.polymarket.com/en/articles/13364163-geographic-restrictions | 2026-10-02 |
| Polymarket US | A separate CFTC-regulated exchange with separate docs at docs.polymarket.us. It has a Retail API with Python/TS SDKs and API keys issued at polymarket.us/developer. | SNIPPET | https://docs.polymarket.us/getting-started/what-is-polymarket-us | 2026-10-02 |
| Polymarket US REST hosts | Retail/Orders: `https://api.polymarket.us` (`/v1/...`). Institutional: `https://api.prod.polymarketexchange.com` (preprod: `api.preprod.polymarketexchange.com`). Bearer auth. | SNIPPET | https://docs.polymarket.us/api-reference/orders/overview ; https://docs.polymarket.us/institutional/introduction | 2026-10-02 |
| Polymarket US rate limit | 20 req/s per API key (global) | SNIPPET | https://docs.polymarket.us/institutional/introduction | 2026-10-02 |
| Polymarket US WS | `wss://api.polymarket.us/v1/ws/markets`. Subscription types: `SUBSCRIPTION_TYPE_MARKET_DATA`, `_MARKET_DATA_LITE`, `_TRADE`. | SNIPPET | https://docs.polymarket.us/api-reference/websocket/markets | 2026-10-02 |
| Polymarket US taker fee | A symmetric formula with θ = 0.0695 and a max fee of $1.74 at p = 0.50 (per the page's unit, apparently per 100 contracts). There are volume rebate tiers of 10/25/50%. | SNIPPET (unit basis: UNKNOWN) | https://docs.polymarket.us/fees | 2026-10-02 |

## 3. The Odds API v4

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| Sport keys | `icehockey_nhl`, `baseball_mlb` | SNIPPET | https://the-odds-api.com/liveapi/guides/v4/ ; https://the-odds-api.com/sports-odds-data/nhl-odds.html | 2026-10-02 |
| h2h market | `h2h` (moneyline) is a "featured market" | SNIPPET | https://the-odds-api.com/sports-odds-data/update-intervals.html | 2026-10-02 |
| Regions | `us`, `us2`, `uk`, `au`, `eu` (comma-delimited for several) | SNIPPET | https://the-odds-api.com/liveapi/guides/v4/ | 2026-10-02 |
| Pinnacle | Bookmaker key `pinnacle`, region `eu`. The page notes "odds are from the public website which may incur a delay". | SNIPPET | https://the-odds-api.com/sports-odds-data/bookmaker-apis.html | 2026-10-02 |
| Circa | Not found in the official bookmaker list | UNKNOWN (likely not covered) | https://the-odds-api.com/sports-odds-data/bookmaker-apis.html | 2026-10-02 |
| Quota cost (live /odds) | `cost = markets × regions` (e.g. 1 market × 1 region = 1 credit) | SNIPPET | https://the-odds-api.com/liveapi/guides/v4/ | 2026-10-02 |
| Plans | Starter free: 500 credits/mo. 20K: $30/mo. 100K: $59/mo. 5M: $119/mo. 15M: $249/mo. | SNIPPET | https://the-odds-api.com/ | 2026-10-02 |
| Update cadence | Featured markets: 60 s pre-match / 40 s in-play. Additional markets: 60/60 s. Outrights: 5 min / 60 s. Exchanges: 20 s / 10 s. The interval starts tightening 6 h before the start. | SNIPPET | https://the-odds-api.com/sports-odds-data/update-intervals.html | 2026-10-02 |
| Historical odds | Paid plans only. `cost = 10 × markets × regions`. Snapshots every **5 min from Sept 2022** (earlier data is coarser). | SNIPPET | https://the-odds-api.com/historical-odds-data/ ; https://the-odds-api.com/liveapi/guides/v4/ | 2026-10-02 |

## 4. SportsDataIO

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| Discovery Lab | A lightweight API for students and hobbyists. The free tier gives **last season's** data. Paid DL tiers: Fantasy $99/mo ($599/yr), Odds $99/mo ($599/yr), Fantasy+Odds $149/mo ($899/yr). DL data is **delayed one day, not for live use**. | SNIPPET | https://discoverylab.sportsdata.io/ ; https://discoverylab.sportsdata.io/personal-use-apis/nhl | 2026-10-02 |
| Free Trial scrambled? | Yes. The Free Trial (separate from DL) uses **scrambled** data: scores, stats and odds are randomly shifted ±5–20%. Schedules, teams and players are not scrambled. The trial has no expiry. | SNIPPET | https://sportsdata.io/help/scrambled-data | 2026-10-02 |
| Replay | Archived, time-stamped real data replayed via the API | SNIPPET | https://sportsdata.io/free-trial | 2026-10-02 |
| Live PBP NHL/MLB | Real-time coverage of every game. PBP is typically 15–20 s behind the TV broadcast. | SNIPPET | https://sportsdata.io/help/refresh-rates-feeds-and-timing | 2026-10-02 |
| Real-time (Leagues API) pricing | No public price found. Appears to be a quote/sales product. | UNKNOWN | https://sportsdata.io/developers | 2026-10-02 |

## 5. Sportradar

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| Trial | Free, **30 days, 1,000 requests per rolling 30 days, 1 QPS**. No purchase needed. Self-serve key via the developer portal. | SNIPPET | https://developer.sportradar.com/getting-started/docs/your-account | 2026-10-02 |
| Trial → production | Production needs a paid key, and the `access_level` in the URL changes from trial. Trial use in production: not explicitly confirmed, but the limits make it impractical. | SNIPPET (production-use terms: UNKNOWN) | https://developer.sportradar.com/getting-started/docs/your-account | 2026-10-02 |
| Push feeds | Cannot be self-provisioned. Push feeds and MLB Statcast need a sales rep. | SNIPPET | https://developer.sportradar.com/getting-started/docs/your-account | 2026-10-02 |
| Pricing | No public price list. Extended trials and production go through sales. | SNIPPET | https://sportradar.com/media-tech/data-content/sports-data-api/ | 2026-10-02 |

## 6. League public APIs

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| api-web.nhle.com official docs / ToU | **None found.** The API is unauthenticated and community-documented (e.g. github.com/dword4/nhlapi). I found no NHL-published API terms. The general NHL.com site terms presumably apply, but I did not read them (nhl.com is blocked). | UNKNOWN | https://github.com/dword4/nhlapi (community, not official) | 2026-10-02 |
| statsapi.mlb.com terms | Responses carry a `copyright` field: "…proprietary content of MLB Advanced Media, L.P. ("MLBAM"). Only individual, non-commercial, non-bulk use of the Materials is permitted…". This was seen only via third-party mirrors; statsapi.mlb.com is egress-blocked. | UNKNOWN (consistent across sources; not read first-hand) | https://pypi.org/project/MLB-StatsAPI/ (third-party) | 2026-10-02 |

## 7. Season calendar

| Item | Value | Status | Source URL | Checked |
|---|---|---|---|---|
| NHL 2026-27 preseason | Began Sept 19, 2026 | SNIPPET | https://media.nhl.com/public/news/19981 | 2026-10-02 |
| NHL 2026-27 regular season start | **Tue Sept 29, 2026**: CAR vs FLA (5 p.m. ET, ESPN tripleheader) and TOR vs MTL on Sportsnet. **The regular season is under way as of Oct 2.** | SNIPPET | https://www.nhl.com/news/nhl-home-openers-for-2026-27-season | 2026-10-02 |
| NHL schedule format | **84 games per team**, 1,344 games in all. The regular season ends Sat Apr 10, 2027. | SNIPPET | https://media.nhl.com/public/news/19975 | 2026-10-02 |
| MLB Wild Card Series | Best-of-3, Tue Sept 29 – Thu Oct 1, 2026 (NBC/Peacock) | SNIPPET | https://www.mlb.com/news/press-release-mlb-announces-2026-postseason-schedule | 2026-10-02 |
| MLB Division Series | Sat Oct 3 – Sat Oct 10 (NLDS G5 Fri Oct 9) | SNIPPET | https://www.mlb.com/news/press-release-mlb-announces-2026-postseason-schedule | 2026-10-02 |
| MLB LCS | NLCS starts Sun Oct 11. ALCS starts Mon Oct 12. | SNIPPET | https://www.mlb.com/news/2026-mlb-playoff-and-world-series-schedule | 2026-10-02 |
| MLB World Series | G1 Fri Oct 23 … G7 Sat Oct 31 (if necessary) | SNIPPET | https://www.mlb.com/news/2026-mlb-playoff-and-world-series-schedule | 2026-10-02 |
| Oct 2, 2026 context | No MLB games today: the gap between the WC Series and the DS. NHL regular-season games are being played. | Derived from above | — | 2026-10-02 |

## 8. LLM model IDs and pricing

### Anthropic (fetched in full: VERIFIED)
Sources: https://platform.claude.com/docs/en/about-claude/models/overview and https://platform.claude.com/docs/en/about-claude/pricing

| Model | API ID | Input $/MTok | Output $/MTok | Context | Status |
|---|---|---|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | 10 | 50 | 1M | VERIFIED |
| Claude Opus 5.5 | `claude-opus-5-5` | 4 | 20 | 1M | VERIFIED |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2 | 10 | 1M | VERIFIED |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` (alias `claude-haiku-4-5`) | 1 | 5 | 200K | VERIFIED. Retirement "not sooner than Oct 15, 2026". |

Other pricing details:
- Batch API: 50% off.
- Cache hits: 0.1× input generally, 0.05× on Opus 5.5 and 0.025× on Fable 5.1.
- Legacy models still available: Fable 5, Opus 5, Opus 4.8/4.7/4.6/4.5, Sonnet 5, Sonnet 4.6.
- Sonnet 5's $2/$10 price is now standard.

### OpenAI (SNIPPET; page blocked)

| Model ID | Input $/1M | Output $/1M | Status | Source |
|---|---|---|---|---|
| `gpt-6-astra` | 10.00 | 50.00 | SNIPPET | https://developers.openai.com/api/docs/pricing ; https://developers.openai.com/api/docs/models |
| `gpt-6.1-sol` | 2.00 | 10.00 | SNIPPET | same |
| `gpt-6-luna` | 0.10 | 0.50 | SNIPPET | same |
| `gpt-6-sol` | price not captured | — | SNIPPET (ID only) | https://developers.openai.com/api/docs/models |

### xAI (SNIPPET; page blocked)

| Model ID | Input / cached / output $/1M | Status | Source |
|---|---|---|---|
| `grok-4.7` | 2.00 / 0.50 / 6.00 | SNIPPET | https://docs.x.ai/developers/models/grok-4.7 |
| `grok-4.6` | 2 / 0.50 / 6 (<200K prompt tokens). 4 / 1 / 12 above that. | SNIPPET | https://docs.x.ai/developers/grok-4-6 |
| `grok-4.3` | 1.25 / 0.20 / 2.50 | SNIPPET | https://docs.x.ai/developers/models/grok-4.3 |
| `grok-build-0.1` | 1.00 / 0.20 / 2.00 | SNIPPET | https://x.ai/news/grok-build-0-1 |

### Google Gemini (SNIPPET; page blocked)

| Model code | Input / output $/1M (paid tier) | Status | Source |
|---|---|---|---|
| `gemini-3.8-flash` | 1.35 / 6.75 through Dec 31, 2026, then **2.70 / 13.50 from Jan 1, 2027**. 1,048,576 input / 65,536 output tokens. | SNIPPET | https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash |
| `gemini-3.1-pro-preview` | price not captured | SNIPPET (ID only) | https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview |
| `gemini-3.1-flash-lite` | 0.25 (text/image/video) / 1.50 | SNIPPET | https://ai.google.dev/gemini-api/docs/pricing |
| `gemini-2.5-flash` | 0.30 / 2.50 | SNIPPET | https://ai.google.dev/gemini-api/docs/pricing |
| `gemini-3-pro-preview` | **Shut down Mar 9, 2026**. Do not use. | SNIPPET | https://ai.google.dev/gemini-api/docs/deprecations |

---

## Open items to resolve with live API calls (cheaper than docs)
1. **Kalshi KXNHLGAME OT/SO rule.** Call `GET /markets?series_ticker=KXNHLGAME` and read `rules_primary`/`rules_secondary`. Do the same for KXMLBGAME.
2. **Kalshi fees for these series.** Use `GET /series/KXNHLGAME` (fee_type / fee_multiplier) and the series-fee-changes endpoint.
3. **Kalshi WS envelope.** Capture one live `orderbook_delta` and record the exact keys (`sid`, `seq`, `msg.*`). Confirm there are no legacy integer-cent fields.
4. **The Odds API.** Confirm the `pinnacle` key appears for `icehockey_nhl`/`baseball_mlb` with `regions=eu`. Read `x-requests-remaining` / `x-requests-used` headers to check quota math.
5. **NHL/MLB terms.** Have a human read the NHL.com Terms of Service and the MLB `copyright` field in a live statsapi response.
