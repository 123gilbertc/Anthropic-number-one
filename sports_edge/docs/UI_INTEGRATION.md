# UI integration (connected workflow)

## Status of the True Edge / Odds Brain inputs

The two companion specs (`CLAUDE_TRUE_EDGE_BUILD_PROMPT.md` and `CLAUDE_TRUE_EDGE_UI_CONNECTION_PROMPT.md`) and the Odds Brain / True Edge application were **not found**. Locations checked on 2026-10-02:

- every branch of this repo
- the user's three GitHub repos
- Google Drive
- all 79 Lovable projects

The closest match, Lovable "Bet Tracker Plus", is a bet log and not the app. So the workflow below was built in `sports_edge/web`, following the requirements stated in chat. It is structured so its data layer can be dropped into the True Edge frontend without changes; see [Porting into True Edge](#porting-into-true-edge-when-its-code-is-available).

## Audit (what exists vs what works)

| Integration | State |
|---|---|
| Replay → state → forecast → triggers → paper fill → settlement | **Working.** Synthetic data only. |
| Paper workflow API: preview, order, ledger, outcome, evaluation | **Working.** API and browser tests pass. |
| Operator auth: HttpOnly cookie + CSRF header, or bearer | **Working** |
| PostgreSQL ledger: orders and ledger events, append-only | **Working** when Postgres is reachable and migrated. Otherwise a banner says the ledger is in memory only. |
| Kalshi REST (public market data) | **Implemented, untested here.** This environment's network policy blocks the host. The Connections screen shows the real failure. |
| Kalshi WebSocket recorder | **Implemented; missing credentials** (key ID + private key) |
| The Odds API | **Implemented; missing credentials** |
| Anthropic shadow review | **Implemented; missing credentials** and a configured model ID / prices |
| OpenAI / xAI / Google shadow review | **Adapters implemented, not wired into settings; missing credentials** |
| Live NHL game feed | **Not implemented, BLOCKED.** No licensed source qualified. |
| Validated forecasting model | **Missing.** Only SYNTHETIC_ONLY models exist. |
| Polymarket (international) | Parser and fee model implemented; no transport; not connected |
| Discord alerts | Implemented; disabled by default |

## Data flow

```
backend (authoritative)                         frontend (display + commands)
──────────────────────────                      ───────────────────────────────
AppSession ─ Monitor ─ engine/broker/ledger      store.ts: one cache for all screens
   │  sequenced EventLog                         ▲  REST refetch (zod-validated)
   ├── GET /api/stream (SSE, id = seq) ──────────┘  SSE invalidation, resync on gap
   ├── GET /api/games|signals|paper/ledger|connections|evaluation|decisions
   └── POST /api/paper/preview, /api/paper/orders (auth + CSRF + idempotency key)
```

**What the backend decides:** probabilities, triggers, fees, risk, eligible size, simulated fills and outcomes. The frontend never computes them.

**One cache.** Every screen reads the same `store.ts` state. The selected game and contract live there (mirrored in `sessionStorage` for reloads). Screens hold no private copies of predictions or ledger data.

**Reconciliation:**
- Every event carries a sequence number.
- The client refetches everything after reconnecting, after a `resync` event, or when it sees a sequence gap.
- The server sends `resync` if the client's cursor is outside its buffer or belongs to a replaced session.

**Validation.** Workflow payloads are checked against zod schemas. Malformed data shows an error instead of wrong numbers.

**Types.** TypeScript types are generated from the FastAPI OpenAPI schema:

```bash
uv run sports-edge openapi
cd web && npm run gen:api
```

**Orders:**
- **Idempotency:** the UI creates one idempotency key per previewed signal. A double click or retry sends the same key, and the server returns the same order.
- **A click is a request, not a fill.** The broker fills only after the decision delay, if every gate still passes against the current book, state and exposure. Otherwise the order is REJECTED, with reasons.
- **Expired or superseded signals** are refused by the server (410 / 409).

**Isolation.** Every order and ledger event carries `mode` (REPLAY / LIVE), `data_label` (e.g. SYNTHETIC) and `run_id`.

**Secrets:**
- They stay server-side: environment variables, or `secrets.local.json` (mode 0600, git-ignored).
- The browser only sees whether a secret is configured, plus its last 4 characters.
- The operator token is exchanged for an HttpOnly cookie and is not stored in the browser.

**Optional LLM review** runs as a detached task. A failure is recorded and never blocks monitoring or orders.

## Screens (sports_edge/web)

| Screen | Content |
|---|---|
| Header | Status banners; update-stream state and age; operator sign-in; replay controls (new session, play/pause, step, speed) |
| Connections | Readiness per capability; each provider's status, secret configuration, *Test connection* (real request with latency and error detail), *Disconnect* |
| Games | Game list and selection |
| Game | State, data age, MODEL_FAVORED_TEAM vs VALUE_SIDE, contract tiles, estimate vs executable price, reasons, invalidation, preview, order, live order status |
| Paper tracker | Positions (quantity-weighted average entry), orders, ledger events |
| Evaluation | Run results with UNKNOWN for empty subsets; model metrics |
| Audit | Every decision change and approval, with plain-English reasons |

## Porting into True Edge (when its code is available)

1. **Copy the data layer.** Copy `web/src/api.ts`, `web/src/store.ts` and the generated `web/src/api.gen.ts`.
2. **Bind the screens.** Bind True Edge's existing screens to `useStore(...)` selectors and the `command(...)` helpers:
   - Overview → `health`, `signals`
   - Games / Matchups → `games`
   - Forecast / Model Data → `games[].contracts`, `/api/models`
   - Tracker → `ledger`
   - Odds Brain assistant → `/api/reviews`, with no decision power
3. **Delete local engines.** Remove any probability, EV or fill math from True Edge components. The backend owns it.
4. **Keep the look.** Keep True Edge's components, navigation and branding; only the data source changes.
