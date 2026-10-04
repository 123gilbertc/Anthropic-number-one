# sports_edge specification

> **Scope change, 2026-10-03 (True Edge commercial multi-sport upgrade).** This section
> supersedes the V1 limits in §2 where they conflict:
> - **Sports:** NHL, NFL, tennis and MLB are all in scope. They share infrastructure and
>   interface conventions, but each has its own rules, state reducer, features, model,
>   calibration and evaluation (`src/sports_edge/sports/`).
> - **Product:** a customer-facing subscription product (Live Board, Watchlist, Paper
>   Portfolio, Performance, Research), with accounts, plan entitlements and test-mode
>   billing. The earlier "no redesign" instruction no longer applies; the former dashboard
>   now lives under Research.
> - **Commercial use** means selling access to analytics, explanations, alerts and paper
>   tracking. It never means accepting wagers or executing real-money trades.
> - **Unchanged:** paper-only; evidence labels; no silent fallbacks; one code path
>   (injected Clock); LLMs have zero decision weight; thresholds stay provisional until
>   frozen; approval before destructive migrations, purchases or public deployment.
> - **Winner markets** remain the forecasting scope. Spreads and totals are separate
>   contracts that would need their own rules and evaluation.
> - The $500/month figure is a prototype planning ceiling only. Commercial costs are
>   quote-dependent and listed as UNKNOWN in `docs/COMMERCIAL_READINESS.md`.

## 1. Objective

Answer two separate questions for every live game:

1. **MODEL_FAVORED_TEAM**: which team is most likely to win the whole game from the current position?
2. **VALUE_SIDE**: is a currently *executable* contract price attractive relative to the updated probability, after costs and uncertainty?

A likely winner can be overpriced, so the two answers are reported separately.

**Hypothesis under test:** some initially attractive teams suffer live price drops that are larger than the change in their real prospects justifies. The hypothesis may be false. The system produces evidence, not assurances. No win rate, return target or "beat Vegas" claim is built in.

**Workflow:** pregame assessment → live monitoring → updated probability → data-quality and edge checks → optional LLM review (shadow) → paper decision and alert → outcome and performance tracking.

## 2. Scope (V1)

- **Sports:**
  - NHL is the first live research adapter.
  - MLB is the historical/replay follow-on, built behind the same adapter boundary.
- **Markets:** full-game winner markets only. Props, parlays, micro-markets, tennis and NFL are excluded.
- **Settlement must match exactly.** These are distinct products, and a contract is priced only when its rule equals the forecast target:
  - NHL regulation-only (three-way)
  - NHL including OT/shootout
  - MLB full game including extra innings
  - MLB listed-pitcher (void if either listed starter doesn't start)
- **Postponed/cancelled games** are treated as VOID pending venue rules. Suspended games stay PENDING.
- **Doubleheaders** are identified by `doubleheader_game_number`.
- **Research, monitoring and paper trading only.** There is no order submission, no deposits, no wallet signing and no real-money execution anywhere in the code.
- **Budget:** a planning ceiling of $500/month for the whole stack. This is not authorization to buy anything. One-time historical purchases are tracked separately from recurring costs (see DATA_SOURCES.md).
- **Live decisions need an authorized live game-state feed.** None has qualified yet, so live decisions are **BLOCKED**. Recording, replay and offline research continue.

## 3. Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short:

- Python 3.11: FastAPI + Pydantic, asyncio ingestion.
- PostgreSQL (append-only records), with Parquet/DuckDB for offline research.
- scikit-learn + LightGBM.
- A React/TypeScript dashboard.
- Modules are separated by responsibility: adapters, normalization, storage, features, forecasts, triggers, LLM review, paper broker, evaluation, alerts.
- The event path does not depend on LLM availability.
- Live and replay share every decision path through an injectable clock.

## 4. Data rules

- Game feeds, market prices, sportsbook references and news are **separate sources**. A market-price stream is never a substitute for play-by-play.
- Every record keeps four separate timestamps: `event_time`, `published_time`, `received_time` and `decision_time`. A missing timestamp stays `None` (unknown); it is never filled with "now".
- **Order books:** a snapshot followed by deltas.
  - A duplicate (`seq` at or below the last seen) is ignored.
  - A gap (`seq` jumps ahead) invalidates the book until a fresh snapshot arrives.
  - A negative size invalidates the book.
- **Game events:**
  - Duplicates are ignored.
  - A correction (same event id, different content) or a late or skipped sequence number flags `PENDING_RECONCILIATION`, which blocks new entries until a full SNAPSHOT arrives. History is never rewritten.
- **Freshness** is measured as connection liveness, including heartbeats, not "time since the price last changed".
- **Temporal alignment:** a sportsbook quote can be compared only if the provider's own update time is *after* the last material game event. A recent receipt time is not enough.
  - A misaligned quote blocks any strategy that relies on references (`require_reference`).
  - For other strategies it is excluded from the comparison, with an audit note.
- **Post-event settle window:** for 15 s (provisional) after a material event, the engine abstains, because resting quotes may not reflect the event yet.

## 5. Canonical records

These live in `domain/records.py` and are immutable and versioned (`schema_version`):

- Game, MarketMapping, RawEvent, NHLState, OrderBookSnapshot, SportsbookQuote
- FeatureVector, ModelVersion, Prediction, Decision (with EVBreakdown)
- PaperFill, Position, Settlement, LLMReviewRecord

Every Decision references:

- the game, venue, contract, selection and settlement rule
- the state snapshot ID, book ID and reference quote IDs
- the prediction, feature version, model version and strategy version
- EV and costs, reason codes, expiry, and the maximum eligible addition

## 6. Probability engine

The target is the exact contract's final payout.

| Model | Description | Status |
|---|---|---|
| A | Same-time market-implied baseline | Implemented as a function; not yet wired into the evaluation reports |
| B | L2-regularized logistic game-state model with a pregame prior | Implemented |
| C | LightGBM challenger | Implemented |

- **Calibration:** Platt scaling on a later chronological validation window.
- **Uncertainty:** a game-clustered bootstrap ensemble gives `probability_low` / `probability_high`. The band reflects estimation uncertainty only; it is not a guarantee.
- **Reduced-feature fallback model.** If critical features are missing, the model abstains.
- **Odds-free vs market-informed** variants are flagged with `uses_market_inputs`. A model that ingests a market is not independent confirmation of that market.
- **Model status** is one of UNTRAINED, SYNTHETIC_ONLY, UNVALIDATED or VALIDATED. Only VALIDATED passes the model gate in normal operation.

## 7. Price and EV

```
expected_net_profit = quantity × p − executable_entry_cost − expected_fees
```

- **Entry cost** comes from walking the visible ask ladder, with a depth haircut (the share of displayed size assumed reachable). Spread and slippage are already inside it, so they are never subtracted again.
- **Conservative EV** uses `probability_low` instead of `p`.
- **Units are kept apart:** dollars, cents per contract, % return on outlay, and probability points.
- **Average entry** = total entry spend ÷ total contracts. The all-in version includes fees and is named separately.
- **Sportsbook margin** is removed by the proportional or power method, using *all* outcomes. Incomplete quotes are rejected.

## 8. Trigger engine

The decision flow is:

`WATCH → CANDIDATE → REVIEW → PAPER_ENTRY / PAPER_ADD / HOLD / NO_ADD / STOP_BUYING / DATA_BLOCKED`

The gates run in this order:

1. **Data:** mapping and settlement match, feed status, state freshness, coherence, reconciliation, settle window, book validity and freshness, reference alignment.
2. **Model:** validated, current snapshot, not expired.
3. **Risk room.**
4. **Dip trigger:** detection plus persistence. This only starts evaluation.
5. **Spread and depth.**
6. **Point and conservative EV** above the margin.
7. **Reference disagreement:** routes to REVIEW, never to a buy.
8. **Cooldown and dedupe.**

Further rules:

- A drop alone never approves a buy.
- STOP_BUYING means no more additions. It never means an exit happened.
- The paper broker:
  - re-runs every gate after the decision delay, against the current state, book and exposure;
  - rejects the fill if any material state changed or the decision expired;
  - fills only a haircut fraction of displayed depth, never above the modeled average price plus one tick, and allows partial fills.
- **All thresholds are PROVISIONAL** and are labelled that way in every decision. They must be estimated on training/validation data and frozen before a prospective test.

## 9. Risk

- The per-team cap covers the initial purchase, every addition, and fees.
- Further limits apply per game, across the portfolio and per day, plus a cash reserve.
- Unsettled proceeds can never fund a purchase.
- There is no Martingale and no stake scaling after wins or losses.
- `RiskLimits.example_1000_bankroll_100_cap()` is the user's example: a $1,000 bankroll with a $100 cap per team. It is a paper scenario labelled with its **10% single-team concentration**, not a safe default.

## 10. LLM layer (shadow)

- Only material triggers call LLMs, with bounded concurrency, deadlines, caching and a monthly budget.
- Every model gets the same immutable evidence bundle, and assessments are independent (no model sees another's answer).
- **Output is schema-validated.** A review is unusable if it fails the schema, cites evidence not in the bundle, cites an obsolete snapshot, times out or errors.
- **`decision_weight = 0`.** A model is promoted only on held-out prospective evidence of incremental value after cost and latency.
- An LLM can't change limits, call tools or place trades. Retrieved text is untrusted data.

## 11. Evaluation

- **Chronological whole-game splits:** train < validation < test < holdout. Leakage guards assert that no game appears in two partitions and that partitions don't overlap in time.
- **Holdout access is logged.** Every evaluation run goes to `runs/experiments.jsonl`.
- **Metrics:** Brier score, log loss, calibration, accuracy by game phase, and clustered-bootstrap confidence intervals by game.
- **Report these groups separately:** all games, selected picks, dipped picks and filled picks. A subset with no data shows UNKNOWN.
- Historical LLM tests are exploratory only, since models may already know the outcomes. LLM contribution needs prospectively timestamped, locked forecasts.
- Closing-line comparisons apply only to pregame entries.

## 12. Strategy comparison

`sports-edge paper` runs these under identical capital and limits:

- **Model-validated dip adds** (`dip_conditional`)
- **Unconditional dip adds** (`dip_unconditional`, a baseline)
- **Live-only entries** (`any_edge`)

Pregame-only, quant-vs-LLM and same-time sportsbook baselines are reported as NOT_RUN until the data they need exists. Losing dips, unfilled approvals, skipped signals and abstentions are all counted.

## 13. Acceptance cases (tests/test_acceptance.py)

1. The estimate falls from 65% to 57% and the price falls to 45¢. The system evaluates costs, uncertainty, freshness and limits; it does not buy automatically.
2. The estimate is 40% and the price is 45¢. The add is rejected.
3. The edge uses a sportsbook quote published before the latest score. The signal is blocked.
4. There is an overall 65% hit rate but no dip-subset evidence. The dip subset shows UNKNOWN.
5. The per-team cap is reached. Additions are blocked, and there is no LLM input path to override this.
6. An LLM agrees but cites an obsolete state. The review is discarded and the decision is re-evaluated.
7. There is no trained model or authorized live feed. The system shows the limitation and produces no alerts.

## 14. Phases

| Phase | Content | State |
|---|---|---|
| 0 | Audit, source verification, cost scenarios, architecture, scaffold | Done (provider docs partly SNIPPET) |
| 1 | Adapter/fixture → normalization → persistence → state endpoint → dashboard, health, replay; market recording | Done for fixtures. Kalshi recorder written but untested against the live API. |
| 2 | Historical features, baselines, calibrated challenger, chronological evaluation, artifacts | Plumbing done on synthetic data. **Real historical data not yet licensed.** |
| 3 | Live quant updates, triggers, paper execution, risk, alerts | Engine done in replay. **Live path BLOCKED** (no feed). |
| 4 | LLM shadow adapters, matched comparisons, prospective logs | Anthropic wired; OpenAI/xAI/Google adapters; promotion harness requires prospective data |
| 5 | Second venue/sport, hardening | MLB state and features; Polymarket parser and fee; no transport yet |
| UI | Connected workflow (docs/UI_INTEGRATION.md) | Done in `sports_edge/web`; True Edge app not available |

Gate rule: no phase claims production readiness from synthetic tests.
