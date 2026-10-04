#!/usr/bin/env bash
# Double-click to start True Edge from this drive (macOS; also works on Linux).
# Research and paper tracking only. Heavy installs stay on this computer, not the drive.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/Anthropic-number-one/sports_edge"
[ -d "$APP" ] || APP="$(cd "$HERE/.." && pwd)"          # when run from sports_edge/launch
cd "$APP"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "==> Installing uv (Python tool, one time, into your home folder)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Keep the Python environment on this computer: USB drives are slow and exFAT breaks symlinks.
export UV_PROJECT_ENVIRONMENT="$HOME/.true-edge/venv"

if command -v git >/dev/null && [ -d ../.git ]; then
  echo "==> Getting the latest saved version from GitHub"
  git -C .. config core.fileMode false
  git -C .. pull --ff-only || echo "   (could not update; starting the copy on this drive)"
fi

echo "==> Preparing Python (first run downloads a few hundred MB, later runs are quick)"
uv sync --frozen --no-dev

if [ ! -f web/dist/index.html ]; then
  if command -v npm >/dev/null; then
    echo "==> Building the web app"
    (cd web && npm ci --no-audit --no-fund && npm run build)
  else
    echo "The web app is not built and Node.js is not installed. Install Node 20+ from https://nodejs.org/ and run this again."
    read -r -p "Press Enter to close." _; exit 1
  fi
fi

uv run --no-dev python -c "from sports_edge.config import settings; from sports_edge.api.auth import Auth; Auth(settings().runs_dir)" >/dev/null
TOKEN="${SPORTS_EDGE_API_TOKEN:-$(cat runs/api_token)}"
PORT="${PORT:-8000}"
URL="http://127.0.0.1:${PORT}"
echo
echo "============================================================"
echo " True Edge is starting:  $URL"
echo " Operator token:         $TOKEN"
echo " Keep this window open. Close it (or press Ctrl+C) to stop."
echo "============================================================"
( sleep 4; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true ) >/dev/null 2>&1 &
exec uv run --no-dev uvicorn sports_edge.api.app:app_factory --factory --host 127.0.0.1 --port "$PORT"
