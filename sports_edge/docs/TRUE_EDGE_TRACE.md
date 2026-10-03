# True Edge commercial multi-sport upgrade: evidence map and verdicts

Date: 2026-10-03. Branch `claude/sharp-carson-aeydxp`.

**Missing spec files and app.** The referenced `CLAUDE_TRUE_EDGE_*.md` files and a separate Odds Brain / True Edge codebase were not found. `sports_edge/` *is* the application, now branded True Edge.

**Evidence levels used below:**
- **Fixture:** deterministic synthetic data, local only.
- **Authorized provider:** a real, credentialed provider call.

No requirement here has authorized-provider verification. This environment's egress policy blocked every provider host.

## Requirement → code → test

| # | Requirement | Code | Test | Evidence |
|---|---|---|---|---|
| 1 | Scope change recorded; NHL, NFL, tennis, MLB | `SPEC.md` (top), `src/sports_edge/sports/` | `test_sports_rules.py`, `test_product.py` | fixture |
| 2 | Customer shell: Live Board, Watchlist, Paper Portfolio, Performance, Research; settings and admin separate; old routes redirect | `web/src/App.tsx`, `router.ts`, `components/Shell.tsx` | e2e "old dashboard routes redirect" | fixture, desktop + mobile |
| 3 | Persistent demo/replay label; no silent fixture substitution | `ModeRibbon`, `product/board.py` (live vs demo never mixed) | e2e "live mode shows unknown coverage…", `test_live_board_reports_unknown_coverage_not_zero_games` | fixture |
| 4 | Live Board: sport tabs, date navigation, search, filters, favourites/watchlist, sorting, stable order | `screens/LiveBoard.tsx` | e2e "demo board lists every sport…", "rows keep their place…" | fixture |
| 5 | Rows show teams/players, score/state, favoured side and probability, price and source, sparkline, assessment, freshness, model status; unavailable games shown | `product/assess.py` `board_row`, `LiveBoard.tsx` | `test_demo_board_rows_counts_and_unavailable_games_are_shown` | fixture |
| 6 | "Most likely winner" ≠ "attractive price"; value side may be NONE; no forced picks | `Assessor.game`, workspace Q1/Q2 | `test_workspace_answers_three_questions_with_evidence` | fixture |
| 7 | Server-side schedule discovery; canonical IDs; reschedules, cancellations, doubleheaders, duplicate names; failures never empty the slate; separate counts | `product/catalog.py`, `product/discovery.py`, `product/board.py` | `test_reschedule_…`, `test_doubleheaders_…`, `test_failed_or_unconfigured_sources_…` | fixture. Provider parsers are BLOCKED until a payload sample is verified. |
| 8 | Coverage registry per competition | `product/coverage.py`, Research → Coverage | `test_live_board_…`, e2e redirect test opens it | fixture |
| 9 | Workspace: score header, probability-vs-price chart, decision panel, evidence drawer, tabs | `screens/GameWorkspace.tsx`, `components/*` | e2e "full workflow…" | fixture, desktop + mobile |
| 10 | Decision panel: exact contract, model probability, size-aware price, fees, net EV for a stated amount, readiness, last update; plain-English EV; no Buy button | `Assessor.contract_view`, `DecisionPanel.tsx` | `test_workspace_…` ("not a promised profit"), e2e | fixture |
| 11 | Line movement: first-observed vs provider open; ask / bid / mid / size-aware entry kept separate; gaps; annotations with evidence; reference with alignment flag; render limits keep records | `product/history.py`, `ProbPriceChart.tsx` | `test_line_history_vocabulary_and_render_limits`, `test_gap_and_pre_event_reference_…` | fixture |
| 12 | Odds formats (probability / decimal / American / cents) kept distinct | `product/formats.py`, `format.ts`, preference `odds_format` | `test_account…` (preferences) | fixture |
| 13 | Three separate connections (game facts, market, sportsbook) | `discovery.default_sources`, `adapters/kalshi*.py`, `adapters/odds_api.py` | `test_failed_or_unconfigured_…`; existing adapter tests | contracts only; **no authorized provider** |
| 14 | Supervised workers independent of browsers; shared upstream across customers; customer data isolated | `DiscoveryService.supervise`, FastAPI lifespan; one Monitor shared; per-owner `Portfolio` | `test_paper_portfolios_are_isolated…`, `test_stream_never_carries…` | fixture |
| 15 | Heartbeats, snapshot/delta, sequence gaps, dedupe, corrections, resync | `adapters/kalshi.py`, `sports/base.py` `BaseReducer` | reducer and book tests (all sports) | fixture |
| 16 | Event / publication / receipt / decision times kept separate; alignment blocks comparisons | records, `triggers/alignment.py`, history `RefObs.compatible` | `test_mismatched_timestamps…`, `test_gap_and_pre_event…` | fixture |
| 17 | Separate probability engine per sport (NHL logistic/GBM; NFL win + tie; tennis exact Markov; MLB base-out Markov) | `sports/nhl.py`, `nfl.py`, `nfl_sim.py`, `tennis_model.py`, `mlb.py` | closed-form and Monte Carlo agreement tests | fixture. Strengths and training data are **SYNTHETIC**. |
| 18 | NFL ties not forced into two complementary probabilities; tie-void settlement in EV | `NFLForecaster` (`probability_void`), `pricing/ev.py` | `test_nfl_tie_…`, `test_void_probability_enters_expected_value` | fixture |
| 19 | Tennis: format, server rotation, tiebreak and match tiebreak, retirement/walkover rules; doubles shown but forecasts disabled | `tennis_rules.py`, `tennis.py` | `test_tiebreak_…`, `test_set_and_final_set_formats`, `test_tennis_retirement_…` | fixture |
| 20 | MLB: walk-off, extra-innings rules, listed-pitcher condition | `sports/mlb.py`, settlement | `test_mlb_win_probability_rules`, `test_mlb_listed_pitcher_void…` | fixture |
| 21 | "Why this assessment?": drivers via controlled sensitivity, contrary evidence, what changed, missing inputs, invalidation; evidence kinds labelled | `product/explain.py`, `Assessor.evidence`, `Evidence.tsx` | `test_workspace_…`, e2e drawer check | fixture |
| 22 | What-if: sport-valid hypotheticals through the real model, SIMULATION label, no side effects | `explain.what_if`, `/api/events/{id}/whatif` | `test_what_if_is_a_labelled_simulation…` | fixture |
| 23 | States WATCH / VALUE CANDIDATE / PAPER ENTRY ELIGIBLE / HOLD / NO ADD / DATA UNAVAILABLE; abstention allowed; no confidence score | `assess.customer_state` | `test_workspace_…` | fixture |
| 24 | Revalidation before every paper entry; delayed rechecked fills; idempotency; one pending order per contract | `Monitor.preview` / `submit_signal`, `PaperBroker`, `AppSession.place_order` | `test_failure_cases.py`, e2e double-click | fixture |
| 25 | LLM adapters (Anthropic, OpenAI, Google, xAI) in shadow; zero weight; triggered, cached, budgeted | `llm/*` | existing LLM tests | fake providers only |
| 26 | Evaluation discipline (chronological splits, clustered bootstrap, INSUFFICIENT EVIDENCE shown) | `evaluation/*`, `screens/Performance.tsx` | existing evaluation tests | synthetic |
| 27 | Alerts: watchlist scope, dedupe, expiry, quiet hours, pause ≠ stop ingestion, no pressure language | `product/alerting.py` | `test_alerts_are_scoped_deduplicated…` | fixture |
| 28 | Paper portfolio: cash, reserved, exposure, fees, quantity-weighted basis, realized vs unrealized; no martingale | `/api/paper/portfolio`, `Portfolio.tsx`, `risk/exposure.py` | `test_pending_order_reserves_exposure`, isolation tests | fixture |
| 29 | Accounts: signup/login, sessions, CSRF, rate limits, preferences, watchlist, export, deletion, audit | `accounts.py`, `api/account_routes.py`, migration `b6c4f7a508fc` | `test_accounts.py` (8 tests) | fixture; memory store in tests, Postgres migration applied locally |
| 30 | Server-enforced entitlements; same safety and forecasts on every plan | `Accounts.entitlements` / `require` | `test_entitlements_billing_webhooks…` | fixture |
| 31 | Test-mode billing: signed webhooks, idempotency, out-of-order events, renewal failure; no live charges; no invented prices | `billing.py` | same test | fixture (signature scheme UNVERIFIED against provider docs) |
| 32 | Operator-only credentials and model promotion | `authed` dependency | `test_customers_cannot_use_operator_commands` | fixture |
| 33 | Mobile layout, bottom nav, keyboard, focus, touch targets, reduced motion | `styles.css`, `Shell.tsx` | e2e "keyboard…", "no horizontal page overflow…" (desktop + Pixel 7) | fixture |
| 34 | Local load test with hardware and latencies | `scripts/load_test.py` | results below | measured locally |
| 35 | Rights / cost matrix and launch gate | `docs/COMMERCIAL_READINESS.md` | — | documentation |

## Local load test (measured)

**Hardware:** 4 vCPU x86_64, 16 GB RAM, Python 3.11, one uvicorn process. **Workload:** the full synthetic slate, 9 games in 4 sports, about 24.5k replay lines.

| Measure | p50 | p95 | p99 | max |
|---|---|---|---|---|
| Market message processing (22,830) | 0.13 ms | 0.26 ms | 0.39 ms | 317 ms (one-off) |
| Game event processing, including re-forecast (1,101) | 3.6 ms | 11.7 ms | 74 ms | 148 ms |
| `/api/board` with 25 concurrent clients, replay advancing (6,769 requests, 0 errors) | 38 ms | 156 ms | 268 ms | |
| `/api/events/{id}/workspace`, same load (6,769 requests, 0 errors) | 38 ms | 141 ms | 216 ms | |

**What these numbers are not:**
- They measure **local processing and serving only**, not stadium-to-screen latency. Provider delay, network and browser rendering are excluded.
- No external paid API was load-tested.
- Provisional budgets, not yet load-proven at commercial scale: per-event processing p95 < 50 ms; board p95 < 250 ms at 25 clients.

## Three verdicts

### ENGINEERING: what works and has been tested?

**PASS on fixtures; not yet on authorized providers.**

What works:
- The four-sport pipeline: reducers, rules, models, settlement, void-aware EV.
- The customer product: board, workspace, chart, evidence, what-if, alerts, portfolio, accounts, entitlements and test-mode billing.

How it was tested:
- Backend: 119 Python tests.
- Browser: 20 tests on desktop and mobile, run against the real backend (19 passed; 1 skipped by design: the keyboard test is desktop-only). Two real bugs were found and fixed this pass: a render loop on Research, and a stale-response guard.

Not established:
- live provider transports (Sportradar/SportsDataIO push, Kalshi WS with a real key);
- multi-process deployment;
- load beyond one process;
- backups and restore.

### FORECASTING: what has credible out-of-sample or prospective evidence?

**NONE. INSUFFICIENT EVIDENCE for every sport.**
- Every model is SYNTHETIC_ONLY. Tennis and MLB strengths are synthetic; NHL and NFL models are trained on simulated games.
- The tennis and MLB Markov models are *rules-correct*: they agree with closed forms and with rules-engine Monte Carlo. That shows the arithmetic is right. It is not evidence of predictive skill.
- No accuracy or profit claim is published anywhere in the product.

### COMMERCIAL: rights, payments, security, launch

**BLOCKED.**
- No data licence, display right or LLM-use right has been obtained.
- No payment-provider approval and no approved pricing.
- No terms or privacy review.
- No public deployment, which needs your approval and a hosting account.

**Security foundations are in place:**
- hashed passwords;
- HttpOnly sessions with a CSRF header;
- per-user isolation in the API, ledger and update stream;
- audit log;
- data export and deletion.

**Gaps that still block launch:** a secrets vault, error reporting, backup and restore drills, and a status page.

## Next concrete blocker

**Licensed live game data.** Request quotes from Sportradar and SportsDataIO for real-time NHL, NFL, MLB and tennis (singles first). Each quote should cover:
- subscriber display;
- storage;
- model training;
- sending data to external LLMs.

Bring the quotes back; nothing is purchased without your approval. With one sport licensed:
- the adapter's endpoint version gets verified;
- a recorded payload is checked;
- the schedule and push parsers get enabled;
- prospective forecasts start being logged for evaluation.
