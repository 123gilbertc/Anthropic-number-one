# Architecture

```
 adapters/                ingest/                     forecast/           triggers/          paper/
 ┌───────────────┐   ┌────────────────────┐      ┌──────────────┐    ┌──────────────┐   ┌────────────┐
 │ kalshi (WS)   │──▶│ orderbook.LocalBook│──┐   │ features/nhl │    │ engine       │──▶│ broker     │
 │ odds_api      │──▶│ SportsbookQuote    │──┼──▶│ models       │──▶│ alignment    │   │ (recheck,  │
 │ game feed     │──▶│ nhl_state reducer  │──┘   │ (chain/abst.)│    │ strategy     │   │  fills)    │
 └───────────────┘   └────────────────────┘      └──────────────┘    └──────┬───────┘   └─────┬──────┘
                              │                                              │                 │
                              ▼                                              ▼                 ▼
                     monitor.Monitor (one event loop; same code for live and replay)  ── risk/exposure
                              │
                 ┌────────────┼──────────────┐
                 ▼            ▼              ▼
          storage (Postgres, append-only)  api (FastAPI) ──▶ web (React dashboard)
                                             llm/ (shadow, off the event path)
```

## Key decisions, in plain language

- **Monitor:** one event-driven core. `Monitor` handles every incoming event the same way: validate, store, update state, re-evaluate. Replay feeds it recorded events with a `ReplayClock`; live operation will feed it network events with a `SystemClock`. Because the code is shared, a replay result is what the live system *would* have done with the same inputs.
- **Append-only storage.** Each table row is one immutable record (key columns plus the full JSON). Corrections are new rows, so we can always reconstruct what the system knew at decision time. This keeps backtests honest.
- **Order book policy.** When a sequence gap appears we stop trusting the book until a full snapshot arrives. Guessing the missing messages could create phantom prices.
- **Game-state policy.** Late, skipped or corrected game events don't silently rewrite the score. They flag the state as needing reconciliation, which blocks new paper positions until an authoritative snapshot confirms the truth.
- **Freshness = connection liveness.** A WebSocket book that hasn't changed is still current if the connection is alive (heartbeats). A connection that has gone quiet is stale.
- **Settle window.** Right after a goal, posted prices may not have caught up, and faster traders will take them first. The engine waits a configurable 15 s (provisional) before considering a trade.
- **Prediction reuse.** A price-only update reuses the current prediction if it was made for the same game-state snapshot and hasn't expired. A new snapshot (a goal, or clock movement) triggers a fresh forecast.
- **LLMs are off the hot path.** The engine has no LLM input. LLM reviews are stored with zero decision weight.
- **Thresholds are data.** `StrategyConfig` holds every threshold and is marked `provisional=True` until thresholds are estimated and frozen.

## Order-book specifics (Kalshi)

Kalshi publishes YES bids and NO bids. Buying YES at price p matches a resting NO bid at 1 − p, so `LocalBook.snapshot()` derives YES asks from NO bids.

Sequence numbers are treated as per-subscription (`sid`). A gap invalidates every book on that subscription. If `sid` is absent, the code falls back to per-market sequence checks.

## Module map

| Path | Responsibility |
|---|---|
| `clock.py` | Injectable clocks. `ReplayClock` refuses to go backwards. |
| `domain/` | Enums and immutable records |
| `contracts/settlement.py` | Mapping validation and settlement resolution per rule |
| `pricing/` | Odds and margin removal, fees, book walking, EV |
| `ingest/` | Local order book; NHL state reducer and coherence checks |
| `adapters/` | Kalshi (parser, reconnecting feed, signer, recorder, REST discovery), The Odds API |
| `features/nhl.py` | Versioned features (`nhl_v1`); missing values stay missing |
| `forecast/` | Models, calibration, bootstrap bands, training, synthetic generator, artifacts |
| `triggers/` | Strategy config, alignment/freshness gates, deterministic engine |
| `risk/exposure.py` | Caps, cash locking, settlement release |
| `paper/` | Broker (delay, recheck, conservative fills), strategy comparison |
| `llm/` | Evidence bundle, schema validation, shadow reviewer, Anthropic adapter |
| `evaluation/` | Metrics, clustered bootstrap, chronological splits, experiment log |
| `monitor.py` | Event loop core; game view for the dashboard |
| `replay/runner.py` | Deterministic replay plus decision digest |
| `storage/` | SQLAlchemy tables and SQL sink (migrations in `migrations/`) |
| `api/app.py` | FastAPI endpoints; serves the built dashboard |
| `health.py` | Per-source counters and latency percentiles |
| `sources.py` | Provider registry shown in the dashboard |

## Adding a sport or venue (Phase 5)

- **New sport:** add a state reducer and a feature module with its own `FEATURE_VERSION`, plus settlement rules in `contracts/settlement.py` (MLB rules exist already).
- **New venue:** add a book manager that outputs `OrderBookSnapshot` and a `FeeModel`.
- **Rule:** the engine, broker, risk and evaluation layers are sport- and venue-agnostic and must not change.
