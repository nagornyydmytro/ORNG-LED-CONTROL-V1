#!/usr/bin/env bash
# Build frontend (unless SKIP_BUILD=1) and start production-like FastAPI + SPA.
set -euo pipefail

HOST_ADDRESS="${HOST_ADDRESS:-127.0.0.1}"
PORT="${PORT:-8000}"
SKIP_BUILD="${SKIP_BUILD:-0}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtualenv not found. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: Port $PORT is already in use on $HOST_ADDRESS." >&2
  echo "Stop the existing ORNG / uvicorn process and retry." >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 3
fi

FRONTEND_DIR="$REPO_ROOT/frontend"
DIST_INDEX="$FRONTEND_DIR/dist/index.html"
DIST_ASSETS="$FRONTEND_DIR/dist/assets"

if [[ "$SKIP_BUILD" != "1" || ! -f "$DIST_INDEX" ]]; then
  echo "Building frontend production bundle ..."
  (
    cd "$FRONTEND_DIR"
    npm run build
  )
fi

if [[ ! -f "$DIST_INDEX" ]]; then
  echo "frontend/dist/index.html missing after build." >&2
  exit 1
fi
if [[ ! -d "$DIST_ASSETS" ]]; then
  echo "frontend/dist/assets missing after build." >&2
  exit 1
fi

echo "Starting ORNG LED CONTROL at http://${HOST_ADDRESS}:${PORT}/"
echo "Health: http://${HOST_ADDRESS}:${PORT}/api/health"
exec "$PYTHON" -m uvicorn orng_led.main:app --host "$HOST_ADDRESS" --port "$PORT"
