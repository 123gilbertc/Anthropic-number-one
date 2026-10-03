# Commercial readiness: rights, costs and launch gate

Checked 2026-10-03. This environment's network policy blocked every provider and documentation host (Sportradar, SportsDataIO, Kalshi, The Odds API, Stripe), so **no provider fact below was re-verified this session**. Labels follow DATA_SOURCES.md: VERIFIED / SNIPPET / MEASURED / UNKNOWN.

Technical API access and a commercial entitlement are separate things. A personal or trial key does not establish subscriber display, redistribution, model-training or external-LLM rights. **An unknown right blocks the customer-facing capability that depends on it.** It does not block local development.

## 1. Provider / product rights matrix

Each cell holds the status of that right. Nothing is cleared. "Code" says what is implemented in the repository.

| Provider · product | Schedule | Live facts | History | Market depth | Odds | Imagery | Storage | Model training | External-LLM use | Customer display / export | Code |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Sportradar NHL / NFL / MLB / tennis (push + REST) | UNKNOWN | UNKNOWN (quote needed) | UNKNOWN | n/a | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | Schedule adapter contract; disabled until an endpoint version is verified and a payload sample is checked. No push client yet. |
| SportsDataIO (commercial real-time) | UNKNOWN | UNKNOWN (quote needed) | UNKNOWN | n/a | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | Same adapter contract as Sportradar. |
| SportsDataIO Discovery Lab | SNIPPET: historical / delayed only | **must not** power live | SNIPPET | n/a | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | None (research import not written). |
| Kalshi market data | SNIPPET (series KXNHLGAME, KXMLBGAME; NFL and tennis series UNKNOWN) | n/a | SNIPPET (candlesticks) | SNIPPET (WS book; signed key) | n/a | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | WS book manager, REST discovery, recorder. |
| Polymarket (US product vs international) | SNIPPET | n/a | UNKNOWN | SNIPPET | n/a | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN (US persons blocked on international) | Parser + fee model only. |
| The Odds API v4 | n/a | n/a | SNIPPET (5-min snapshots, paid) | n/a | SNIPPET | n/a | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | Parser, alignment checks, connection test. |
| LLM providers (Anthropic, OpenAI, Google, xAI) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | Provider terms UNKNOWN for licensed sports data inside prompts | n/a | Shadow adapters, zero decision weight. |
| Team / player imagery | — | — | — | — | — | **none licensed** | — | — | — | — | Text initials only. |
| Payment provider (Stripe-style, TEST mode) | — | — | — | — | — | — | — | — | — | Merchant acceptance UNKNOWN | Signed, idempotent webhooks; checkout disabled until a price is approved. |

## 2. Cost configurations

No purchases have been made. A quote-dependent cost is shown as UNKNOWN, never estimated.

### Lean research configuration (one operator, replay plus recorded markets)

| Item | Monthly | Status |
|---|---|---|
| Kalshi market data | $0 (account + key) | SNIPPET |
| The Odds API | $0–$59 | SNIPPET |
| SportsDataIO Discovery Lab | $0–$149 | SNIPPET |
| Postgres + small VM | ~$10–$40 | not priced |
| LLM shadow reviews (capped) | ≤ $25 budget cap | configurable |
| **Total** | **~$10–$275** | within the $500 prototype ceiling |

### Commercial configuration (customers, four sports, live)

| Item | Monthly | Status |
|---|---|---|
| Live play-by-play, NHL + NFL + MLB + tennis (Sportradar or SportsDataIO) | **UNKNOWN** | sales quotes required, per sport and per display rights |
| Commercial redistribution / subscriber display rights | **UNKNOWN** | contract term |
| Historical point-level tennis and play-by-play history | **UNKNOWN** | quote |
| Market data at production rate limits | UNKNOWN | venue terms |
| Sportsbook reference (Odds API higher tier) | $119–$249 (SNIPPET) | needs commercial-use confirmation |
| Hosting: API, workers, Postgres, backups | not priced | depends on load |
| LLM explanations (shared per game event, not per customer) | scales with games, not customers | capped |
| Payment processing | provider fees (UNKNOWN until account) | |
| **Total** | **UNKNOWN** | The $500 ceiling does **not** cover this scope. |

**Cost drivers:**
- Live feeds are shared across customers. A customer opening another tab adds no feed subscription and no LLM call, because reviews run on game events and are cached by evidence, model and prompt version.
- Per-customer cost is mainly API serving and storage.

## 3. Launch checklist (commercial verdict inputs)

| Requirement | State |
|---|---|
| Signed data licences covering live display, storage, training, LLM use | **BLOCKED**: none obtained |
| Payment provider account approval for this category | **BLOCKED**: not applied |
| Approved pricing | **BLOCKED**: no price defined (`PLANS[*].price = None`) |
| Terms of service, privacy policy, marketing review | **BLOCKED**: drafts not written; drafts would not be legal clearance |
| Age and region restrictions (prediction-market data; sports-betting adjacency) | **BLOCKED**: legal review required |
| Secrets management | Partial: server-side store (file mode 0600) + env. No vault or KMS. |
| Rate limits | Partial: login failures only |
| Error reporting | Not set up |
| Backups and restore test | Not done |
| Audit logs | Done (account actions, billing events) |
| Account export and deletion | Done (tested) |
| Support / status page | Not done |
| Public deployment | **Not done**: requires your explicit approval and a hosting account |
