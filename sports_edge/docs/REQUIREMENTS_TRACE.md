# Requirements trace and verdicts

The three referenced spec files (`CLAUDE_TRUE_EDGE_BUILD_PROMPT.md`, `CLAUDE_TRUE_EDGE_UI_CONNECTION_PROMPT.md`, `CLAUDE_TRUE_EDGE_INTEGRATION_VALIDATION_PROMPT.md`) are **not in this repository**, on any branch, in the user's three GitHub repos, or in Google Drive (checked 2026-10-02). This trace uses the requirements stated in the chat requests instead.

The Odds Brain / True Edge application is also absent. `sports_edge/` is the only implementation.

**Columns:**
- **Implemented:** code exists.
- **Verified:** a test exercises it, and the evidence column says under which conditions. "Fixture" means synthetic, isolated data. It is never evidence of live provider access or predictive value.

## Critical requirements

| Requirement | Code | Data contract / persistence | UI | Test | Evidence / blocker |
|---|---|---|---|---|---|
| Provider-shaped market input (Kalshi WS) | `adapters/kalshi.py` | `OrderBookSnapshot`; raw events → `raw_events` | Game: ask/bid/depth | `test_engine_pipeline::test_kalshi_*`, `test_integration_path` | Fixture only. Live WS needs a key and the host is blocked here. Field names are SNIPPET. |
| Provider-shaped reference (The Odds API) | `adapters/odds_api.py` | `SportsbookQuote` → `sportsbook_quotes` | Game: reference age | `test_odds_parse_*`, `test_integration_path` (raw v4 JSON) | Fixture only. No key configured. |
| Live NHL game state | — | `NHLState` → `game_states` | Game header | reducer tests | **BLOCKED:** no licensed source. Raw provider shape UNKNOWN. |
| Normalized state (duplicates, gaps, corrections, late events) | `ingest/nhl_state.py`, `ingest/orderbook.py` | `NHLState.pending_reconciliation` | PENDING banner | `test_duplicate_and_correction`, `test_out_of_order_and_gap_flagged`, `test_kalshi_sid_gap_*` | Verified on fixtures |
| Missing order-book updates | `KalshiBookManager` (per-subscription seq) | book `valid=False` until a snapshot arrives | BOOK INVALID badge | `test_kalshi_sid_gap_invalidates_and_resync` | Verified on fixtures |
| Stale game data | `triggers/alignment.check_state` | `GAME_STATE_STALE` reason | state-age badge | `test_stale_game_data_blocks_signal` | Verified |
| Mismatched timestamps | `nhl_state._validate`, `alignment.check_reference` | `TIMESTAMP_INCONSISTENT` | reason list | `test_mismatched_timestamps_are_rejected_not_trusted` | Verified |
| Quote from before the latest score | `alignment.check_reference` | `REFERENCE_PRE_EVENT` | reason list | acceptance case 3 | Verified |
| Quantitative forecast (calibrated, with band) | `forecast/models.py`, `train.py` | `Prediction`, `ModelVersion` artifact (sha256) | probability + band + status | `test_training_is_reproducible_*` | Plumbing only. Models are **SYNTHETIC_ONLY**. |
| Trigger + risk gates | `triggers/engine.py`, `risk/exposure.py` | `Decision` → `decisions` (audit) | decision + plain-English reasons | acceptance 1–7, engine tests | Verified on fixtures |
| Exposure reserved for pending orders | `ExposureLedger.reserve` / `PaperBroker.submit` | ledger reservations | room left | `test_pending_order_reserves_exposure`, `test_reservation_released_*` | Verified (added this pass) |
| Interface signal | `Monitor._register_signal`, `/api/signals`, `/api/games` | `GameIntel` (OpenAPI → TS) | Game tile + decision panel | `test_integration_path`, e2e full workflow | Verified (desktop + mobile) |
| Paper-order preview | `Monitor.preview`, `/api/paper/preview` | `Preview` | Preview panel | API + e2e | Verified |
| Duplicate requests | `AppSession.place_order` (idempotency key + lock) | `paper_orders.idempotency_key` | single order shown | `test_concurrent_duplicate_requests_create_exactly_one_order`, e2e double click | Verified |
| Competing paper orders | one PENDING order per contract + reservations | `ORDER_PENDING_FOR_CONTRACT` (409) | error shown | `test_competing_orders_on_same_contract_*` | Verified (added this pass) |
| Order for a contract other than the one displayed | `expected_contract_id` check | `CONTRACT_MISMATCH` (409) | sent by UI | `test_order_for_a_different_contract_*` | Verified (added this pass) |
| Expired signals | `Monitor.submit_signal` | `SIGNAL_EXPIRED` (410) | EXPIRED badge | API + e2e | Verified |
| Delayed response for a previously selected game | `web/src/screens/Games.tsx` guard; `store.ts` generation counter | — | discarded | e2e "slow preview…" | Verified (added this pass) |
| Worker restart | `session.recover`, `restore_exposure`; server startup | `paper_orders` (versions), `ledger_events` (`ORDER_ABANDONED`) | restart banner; history endpoint | `test_worker_restart_*`, `test_live_exposure_is_rebuilt_*` | Verified against Postgres 16 (added this pass) |
| Reconnect / event-stream reconciliation | `EventLog` (thread-safe), `/api/stream`, `store.ts` | SSE ids = sequence numbers | update-stream badge | `test_event_log_*`, e2e reconnect (repeated runs) | Verified; a lost-wakeup race was found and fixed this pass |
| Paper ledger + settlement + evaluation | `session.py`, `Monitor._settle`, `/api/evaluation` | `LedgerEvent`, `PaperOrder` with mode / data_label / run_id | Tracker, Evaluation | `test_integration_path`, storage tests | Verified on fixtures |
| Optional LLM review off the critical path | `session._review` (detached task), `llm/*` | `LLMReviewRecord` (weight 0) | — | `test_failed_llm_review_does_not_stop_monitoring`, LLM schema tests | Verified with fake providers. Real providers have no keys. |
| Extra models need measured incremental value | `llm/evaluate.promotion_report` | — | — | `test_llm_promotion_requires_prospective_evidence` | No prospective data, so weight stays 0 |
| No frontend-generated numbers | `web/` reads `store.ts` only; zod validation | OpenAPI types | all screens | e2e + code review | Verified by test and inspection |
| Secrets server-side, real connection tests | `connections.py` | `secrets.local.json` (0600) | Connections | `test_connections_real_statuses_*`, e2e | Verified. Every provider test fails here because egress is blocked, and the UI shows the real error. |
| Paper-only, no real-money execution | whole codebase | — | PAPER ONLY banner | no order-submission code exists | Verified by inspection |
| $500/month planning ceiling | `DATA_SOURCES.md` cost scenarios | — | — | — | Nothing purchased. Live feed price UNKNOWN. |

## Verdict 1: engineering readiness

**Under the tested conditions** (isolated synthetic fixtures; local Postgres 16; Chromium on desktop and mobile), the application passes all of these:

- the end-to-end path test
- the seven acceptance cases
- the dangerous-failure tests listed above
- 90 Python tests
- the browser suite

**Not tested, so not established:**
- behaviour against real Kalshi / The Odds API / LLM endpoints
- a real live NHL feed
- multi-worker deployment (one server process is assumed)
- load beyond a single operator

**Verdict:** ready for supervised replay research and fixture-based paper workflows; **not ready** for live monitoring.

## Verdict 2: strategy evidence

**NOT ESTABLISHED.**

- Every model is SYNTHETIC_ONLY.
- No real historical or prospective out-of-sample data exists.
- On synthetic data, the market-implied baseline beat both the logistic and GBM models. That says nothing about real markets either way.
- The frozen-threshold test result is also synthetic.

Passing Verdict 1 does not change this.

**What would be required:**
- licensed historical data
- a frozen strategy version
- prospective paper logs over enough games
- clustered confidence intervals that exclude zero after costs
