# CLAUDE.md

This repo holds two unrelated projects:

- `workflows/`, `docker-compose.yml`, root `.env.example`: an n8n Facebook Ads automation. Leave it alone unless asked.
- `sports_edge/`: True Edge, a multi-sport (NHL, NFL, tennis, MLB) live probability analytics and paper-tracking product (scope change recorded in `sports_edge/SPEC.md`).

## sports_edge rules

- **Research and paper trading only.** Never add order submission, deposits, wallet signing or real-money execution.
- **Read first:** `sports_edge/PROGRESS.md` (where things stand), `SPEC.md`, `DATA_SOURCES.md`.
- **Provider facts** are labelled VERIFIED / SNIPPET / MEASURED / UNKNOWN. Never upgrade a label without evidence. Never invent coverage, fees or settlement rules.
- **No silent fallbacks.** Never substitute stale, delayed or synthetic data for a live source. A missing timestamp stays `None`.
- **One code path.** Decision logic is shared by replay and live through the injected `Clock`. Never call `datetime.now()` in decision code.
- **LLMs:** zero decision weight. The trigger engine must not take LLM input.
- **Thresholds** stay `provisional=True` until estimated on train/validation data and frozen.
- **Frontend:** the backend decides probabilities, fees, risk, eligible size and fills. `web/` only displays results and sends authenticated commands, through the shared store (`web/src/store.ts`).
- **Tests:** never weaken a test to make it pass. The 7 acceptance cases live in `tests/test_acceptance.py`.
- **Ask first** before destructive changes, paid purchases or public deployment.

## Commands (run from `sports_edge/`)

```bash
uv sync --frozen
uv run pytest
uv run ruff check src tests scripts
uv run sports-edge demo
cd web && npm ci && npm run build && npm run e2e
```

Local Postgres: `service postgresql start`, then `uv run alembic upgrade head`.

At the end of each session, update `sports_edge/PROGRESS.md`.
