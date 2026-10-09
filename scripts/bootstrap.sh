#!/usr/bin/env bash
# Create venv and install backend + frontend deps (macOS / Linux).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.12 (e.g. brew install python@3.12)" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js 18+ (e.g. brew install node)" >&2
  exit 1
fi

PY_VER="$(python3 --version 2>&1)"
echo "Using $PY_VER"
if [[ "$PY_VER" != *"3.12"* ]]; then
  echo "Warning: canon expects Python 3.12 (detected: $PY_VER)" >&2
fi

if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
  echo "Creating virtual environment at .venv ..."
  python3 -m venv "$REPO_ROOT/.venv"
fi

PYTHON="$REPO_ROOT/.venv/bin/python"
"$PYTHON" -m pip install --upgrade pip
(
  cd "$REPO_ROOT/backend"
  "$PYTHON" -m pip install -e ".[dev]"
)
(
  cd "$REPO_ROOT/frontend"
  npm install
)

echo "Bootstrap complete."
echo "Next: ./scripts/run.sh"
echo "  or: ./scripts/venue-start.sh   (venue: Art-Net + Arm + browser)"
