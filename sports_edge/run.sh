#!/usr/bin/env bash
# One-command local start for sports_edge (research + paper only).
# Needs: uv (https://docs.astral.sh/uv/) and Node 20+. PostgreSQL is optional:
# without it the paper ledger stays in memory and the UI says so.
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
command -v npm >/dev/null || { echo "Install Node.js 20+ first: https://nodejs.org/"; exit 1; }

echo "==> Installing Python dependencies (locked)"
uv sync --frozen

if [ -n "${DATABASE_URL:-}" ] || pg_isready -q 2>/dev/null; then
  echo "==> Applying database migrations"
  uv run alembic upgrade head || echo "   (database not ready; continuing with in-memory ledger)"
fi

echo "==> Building the dashboard"
(cd web && npm ci --no-audit --no-fund && npm run build)

uv run python -c "from sports_edge.config import settings; from sports_edge.api.auth import Auth; Auth(settings().runs_dir)" >/dev/null
TOKEN="${SPORTS_EDGE_API_TOKEN:-$(cat runs/api_token)}"
PORT="${PORT:-8000}"
echo
echo "============================================================"
echo " Open:           http://127.0.0.1:${PORT}"
echo " Operator token: ${TOKEN}"
echo " (paste the token into 'Operator token' and click Sign in)"
echo "============================================================"
echo
exec uv run uvicorn sports_edge.api.app:app_factory --factory --host 127.0.0.1 --port "${PORT}"
