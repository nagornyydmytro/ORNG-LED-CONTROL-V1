#!/bin/bash
# Desktop copy should always use the clone under $HOME.
if [[ -f "$HOME/ORNG-LED-CONTROL-V1/scripts/venue-start.sh" ]]; then
  REPO="$HOME/ORNG-LED-CONTROL-V1"
elif [[ -f "$(dirname "$0")/scripts/venue-start.sh" ]]; then
  REPO="$(cd "$(dirname "$0")" && pwd)"
else
  echo "ORNG repo not found. Run:"
  echo "  curl -fsSL https://raw.githubusercontent.com/nagornyydmytro/ORNG-LED-CONTROL-V1/main/scripts/macos-install.sh | bash"
  read -r -p "Press Enter to close "
  exit 1
fi
cd "$REPO"
chmod +x scripts/*.sh 2>/dev/null || true
exec ./scripts/venue-start.sh
