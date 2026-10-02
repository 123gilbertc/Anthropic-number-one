# sports_edge

A live sports probability and market-overreaction **research** system, starting with NHL on Kalshi. It covers research, monitoring and **paper trading only**: there is no order submission, no deposits and no wallet code.

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

Open http://127.0.0.1:8000 and paste the operator token that `run.sh` prints. PostgreSQL is optional: without it the ledger stays in memory and the UI says so.

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
