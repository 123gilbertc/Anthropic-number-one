# sports_edge (True Edge by Odds Brain)

A multi-sport (NHL, NFL, tennis, MLB) live probability and market-overreaction analytics product with paper tracking. It covers research, monitoring and **paper trading only**: there is no order submission, no deposits and no wallet code.

- Customer app: Live Board, Watchlist, Paper Portfolio, Performance, Research (+ Settings, Admin)
- Commercial readiness, rights and costs: [docs/COMMERCIAL_READINESS.md](docs/COMMERCIAL_READINESS.md)
- Requirement → code → test map and the three verdicts: [docs/TRUE_EDGE_TRACE.md](docs/TRUE_EDGE_TRACE.md)

- Specification: [SPEC.md](SPEC.md)
- Data sources and verification status: [DATA_SOURCES.md](DATA_SOURCES.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Models and evaluation: [docs/MODELS_EVALUATION.md](docs/MODELS_EVALUATION.md)
- Where things stand: [PROGRESS.md](PROGRESS.md)
- UI workflow and integration audit: [docs/UI_INTEGRATION.md](docs/UI_INTEGRATION.md)

## Current honest status

- **Live game-state feed: BLOCKED.** No licensed source has qualified yet, so `sports-edge live` refuses to run.
- **Data:** everything you can run today replays a **synthetic** fixture.
- **Models:** they are **SYNTHETIC_ONLY**, so in normal mode they produce **no value alerts**.
- **Mechanics demo:** the explicit opt-in `--mechanics-demo` mode shows the full path, with labels everywhere. Its output is not evidence of anything.

## Quick start (try it locally)

```bash
git clone -b claude/sharp-carson-aeydxp https://github.com/123gilbertc/Anthropic-number-one.git
cd Anthropic-number-one/sports_edge
./run.sh
```

Open http://127.0.0.1:8000. The Live Board opens on the labelled **demo slate** (synthetic, fictional teams). Create a customer account under *Sign in* to keep a paper portfolio and watchlist. The operator token that `run.sh` prints unlocks Admin and Research replay controls. PostgreSQL is optional: without it the ledger and accounts stay in memory and the UI says so.

### Configuration (operator)

| Setting | Where | Effect |
|---|---|---|
| `SPORTS_EDGE_API_TOKEN` | env | operator token (else generated into `runs/api_token`) |
| `SPORTS_EDGE_DEMO_FIXTURE`, `SPORTS_EDGE_DEMO_MODE` | env | demo session (default `slate_synthetic.jsonl`, `mechanics`) |
| `SPORTS_EDGE_WORKERS` | env | `1` runs supervised schedule/market discovery in the server (default) |
| `SPORTRADAR_API_KEY`, `SPORTRADAR_<SPORT>_SCHEDULE_URL` | Admin → Connections or env | schedule source. Stays NOT_CONFIGURED until you verify the current endpoint version; parsing stays BLOCKED until a recorded payload is checked |
| `SPORTSDATAIO_API_KEY`, `SPORTSDATAIO_<SPORT>_SCHEDULE_URL` | same | same |
| `KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH` | Admin → Connections | order-book WebSocket |
| `ODDS_API_KEY` | Admin → Connections | sportsbook reference |
| `BILLING_WEBHOOK_SECRET_TEST`, `BILLING_TEST_SECRET_KEY` (`sk_test_…` only) | env / secret store | test-mode billing webhooks; live keys are never read |

Customers never enter provider keys: the operator configures shared licensed feeds.

## Setup (reproducible)

Requirements: Python 3.11, [uv](https://docs.astral.sh/uv/), PostgreSQL 16, Node 20+.

```bash
cd sports_edge
uv sync --frozen                 # exact versions from uv.lock
cp .env.example .env             # fill in only what you have

# database (local Postgres)
createuser sports_edge -P        # password: sports_edge (dev default; change it)
createdb -O sports_edge sports_edge
createdb -O sports_edge sports_edge_test
uv run alembic upgrade head

# dashboard
cd web && npm ci && npm run build && cd ..
```

## Commands

| Purpose | Command |
|---|---|
| Tests | `uv run pytest` (Postgres tests skip if `TEST_DATABASE_URL` is unreachable) |
| Lint | `uv run ruff check src tests scripts` |
| Demo | `uv run sports-edge demo` |
| Replay | `uv run sports-edge replay fixtures/nhl_synthetic_dip.jsonl [--persist] [--model artifacts/<id>.json]` |
| Train (synthetic) | `uv run sports-edge train --synthetic [--kind gbm]` |
| Paper comparison | `uv run sports-edge paper fixtures/nhl_synthetic_dip.jsonl [--mechanics-demo]` |
| API + dashboard | `uv run sports-edge serve` → http://127.0.0.1:8000. Sign in with the operator token from `SPORTS_EDGE_API_TOKEN` or `runs/api_token`. |
| Model comparison | `uv run sports-edge train --synthetic --compare` |
| Freeze thresholds | `uv run sports-edge thresholds --synthetic` |
| Regenerate API types | `uv run sports-edge openapi && (cd web && npm run gen:api)` |
| Browser tests | `cd web && npm run build && npm run e2e` (desktop + mobile; Chromium via Playwright) |
| Dashboard dev | `cd web && npm run dev` (proxies `/api` to :8000) |
| Kalshi market discovery | `uv run sports-edge discover --series KXNHLGAME` |
| Kalshi recording | `uv run sports-edge record <TICKER>...` (needs Kalshi API key) |
| Regenerate fixture | `uv run python scripts/make_fixtures.py` |
