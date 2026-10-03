# Progress log (for resuming in a new session)

## 2026-10-03: clickable recorded replay

- The user cannot reach a server running in the build container, and public hosting needs their approval and an account.
- `scripts/record_demo.py` runs the real backend over the synthetic fixture and records every changed frame, every preview, and the real outcome of a paper order placed at each of the 147 eligible (moment, contract) pairs. A static page plays the recording back and computes nothing.
- Published as a private Claude Artifact (recorded replay, not the live app). A live hosted app is still pending the user's approval and a hosting account.

## 2026-10-02: integration-validation pass

The three referenced `CLAUDE_TRUE_EDGE_*.md` specs and the True Edge app are still not in any accessible location. This pass worked from the requirements in chat; see `docs/REQUIREMENTS_TRACE.md` for the requirement-by-requirement trace and both verdicts.

**Gaps repaired (tests added first):**

1. **Competing and duplicate orders.**
   - Session state changes are serialized by a lock.
   - Pending orders reserve exposure immediately.
   - Only one pending order is allowed per contract.
   - Orders must name the contract the UI displayed (`CONTRACT_MISMATCH`).
2. **Worker restart.**
   - Persisted orders are reloaded at startup.
   - Never-filled PENDING orders become ABANDONED, with a ledger event; they are never filled retroactively.
   - Idempotency keys survive restarts and session replacement.
   - Live exposure can be rebuilt from unsettled LIVE fills.
   - New endpoint `/api/paper/history`.
3. **Late responses in the UI.**
   - A slow preview for a contract the user has left is discarded.
   - Per-key request counters drop out-of-order fetches.

**Also this pass:**

- **Timestamp-consistency rules** (`TIMESTAMP_INCONSISTENT`).
- **The first runnable acceptance test**, `tests/test_integration_path.py`: provider-shaped Kalshi and raw Odds API v4 input → … → evaluation, with cross-screen consistency checks.
- **Two real bugs found by the browser suite and fixed:**
  - a lost-wakeup race in the SSE stream (stale UI for up to 10 s after events);
  - thread-unsafe waking of the event loop from worker threads.


## 2026-10-02: connected workflow + remaining research items

### Done

**Connected paper workflow**

- **Backend (`session.py`, `api/app.py`) is the single source of truth.** It owns a paced or steppable replay session, a sequenced event stream (SSE with resync), and preview → idempotent paper order → delayed rechecked fill → ledger events → settlement → evaluation.
- **Operator auth:** HttpOnly cookie plus CSRF header, or bearer token.
- **Connection registry with real tests:** server-side secrets, readiness by capability, disconnect.
- **Ledger persisted** to Postgres (migration `8ee7ba57d575`). Every record is tagged with mode, data label and run ID.
- **Frontend (`web/`):**
  - TypeScript types generated from the OpenAPI schema; zod checks on incoming data.
  - One shared store for all screens, kept current by the update stream.
  - Screens: Connections, Games, Game, Paper tracker, Evaluation, Audit.
  - Browser tests (`npm run e2e`): 12 passing on desktop and mobile. They cover:
    - double click → exactly one order
    - an expired signal is refused
    - reconnect → refetch everything
    - an unavailable provider shows its real error
    - no horizontal overflow on any screen

**Other research items**

- **Model comparison** (`train --synthetic --compare`): logistic vs GBM vs the market's own price as a baseline.
- **Threshold estimation and freezing** (`thresholds --synthetic`).
- **MLB:** game-state reducer and features (`mlb_v1`).
- **Polymarket (international):** parser and fee model.
- **AI review adapters** for OpenAI, xAI and Google, plus a promotion check that needs prospective evidence.
- **Pregame entries:** a pregame forecaster and three pregame strategy variants.
- **Discord alerts:** off by default.

### Still blocked or missing

- **True Edge / Odds Brain** code and both spec files were not found (see docs/UI_INTEGRATION.md). The workflow lives in `sports_edge/web` until they are provided.
- **Credentials:** Kalshi, The Odds API and the LLM providers. Without them the real-provider paths stay unproven.
- **Live NHL feed:** BLOCKED.
- **Real historical data:** none yet.


## 2026-10-02: first vertical slice

### Works

- **End-to-end replay:** synthetic NHL fixture → Kalshi-format book messages + normalized game events + reference quotes → state reducer → features → forecast → trigger engine → paper broker → settlement → Postgres audit trail → API → dashboard.
- **The seven specification acceptance cases** pass as tests (`tests/test_acceptance.py`).
- **Deterministic replay:** the same file gives the same decision digest.
- **Chronological training and evaluation** on synthetic data, with clustered-bootstrap CIs, a phase breakdown, an experiment log, and hash-checked artifacts.
- **Matched paper-strategy comparison:** dip_conditional vs dip_unconditional vs live-only.
- **LLM shadow harness:** schema/evidence/staleness validation, timeouts, budget, cache. The Anthropic adapter is written.

### Mocked or synthetic

- All game, market and reference data come from `fixtures/nhl_synthetic_dip.jsonl`, which is generated by `scripts/make_fixtures.py`.
- All models are SYNTHETIC_ONLY.
- Heartbeat lines in the fixture stand in for WebSocket ping/pong.

### Measured

- Nothing from real providers yet. The build environment's network policy blocks provider hosts (403).

### Blockers

- **Live NHL game state:** no qualified source (cost UNKNOWN, needs sales quotes). Live decisions are BLOCKED.
- **Kalshi API key:** needed for the WebSocket recorder (`KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`).
- **KXNHLGAME OT/shootout rule and series fee multiplier:** UNKNOWN until read via the API.
- **Licensed historical NHL data:** needed for Phase 2 with real data.

### Next steps (in order)

1. Run `discover` and `record` with credentials. Confirm the message fields and mark them MEASURED in DATA_SOURCES.md.
2. Get quotes for live play-by-play. Pick the cheapest source that passes a coverage and freshness test.
3. Load real historical NHL data and train the logistic and GBM models. Wire the market-implied baseline into the reports.
4. Estimate thresholds on validation data, freeze a strategy version, and start the prospective paper log.
5. Add the second LLM provider adapter and keep it at zero weight until prospective evidence exists.

### Commands verified this session

- `uv run pytest`: all pass.
- `uv run ruff check src tests scripts`.
- `uv run sports-edge demo` / `paper ... --mechanics-demo` / `live` (exits BLOCKED).
- `uv run alembic upgrade head` against local Postgres 16.
- `npm run build` in `web/`; dashboard screenshots checked at desktop and phone widths.
