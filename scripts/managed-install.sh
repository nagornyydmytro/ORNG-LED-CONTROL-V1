#!/usr/bin/env bash
# Managed install bootstrap for macOS / Linux / Raspberry.
# Creates app/data layout. Does NOT enable systemd. Does NOT point current.json
# at a git checkout. Does NOT write into the source repository.
set -euo pipefail

OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
  APP_ROOT="${ORNG_APP_ROOT:-$HOME/Library/Application Support/ORNG-LED-CONTROL/app}"
  DATA_ROOT="${ORNG_CONFIG_ROOT:-$HOME/Library/Application Support/ORNG-LED-CONTROL/data}"
else
  APP_ROOT="${ORNG_APP_ROOT:-/opt/orng-led-control}"
  DATA_ROOT="${ORNG_CONFIG_ROOT:-/var/lib/orng-led-control}"
fi

mkdir -p "$APP_ROOT/bootstrap" "$APP_ROOT/releases" "$APP_ROOT/logs" "$DATA_ROOT/scenes"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$REPO_ROOT/bootstrap/updater_helper.py" ]]; then
  cp "$REPO_ROOT/bootstrap/updater_helper.py" "$APP_ROOT/bootstrap/updater_helper.py"
fi

# Initial current.json is intentionally absent until a verified signed release
# is staged. Never point at $REPO_ROOT.
if [[ -f "$APP_ROOT/current.json" ]]; then
  echo "current.json already present — leaving unchanged"
else
  echo "No current.json yet. Stage a signed release under $APP_ROOT/releases/ first."
fi

echo "Managed layout ready:"
echo "  app:  $APP_ROOT"
echo "  data: $DATA_ROOT"
echo "systemd template (static, not installed): $REPO_ROOT/scripts/systemd/orng-led-control.service"
echo "Do not enable services from this script; hardware acceptance is separate."
