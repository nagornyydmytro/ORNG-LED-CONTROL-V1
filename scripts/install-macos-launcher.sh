#!/usr/bin/env bash
# Put a double-clickable launcher on the macOS Desktop.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP="${HOME}/Desktop"
CMD_SRC="$REPO_ROOT/ORNG-LED-START.command"
CMD_DST="$DESKTOP/ORNG LED START.command"

chmod +x "$REPO_ROOT/scripts/"*.sh
chmod +x "$CMD_SRC"

cp -f "$CMD_SRC" "$CMD_DST"
chmod +x "$CMD_DST"

# Clear quarantine so double-click works after download/clone.
xattr -dr com.apple.quarantine "$CMD_DST" 2>/dev/null || true
xattr -dr com.apple.quarantine "$REPO_ROOT/scripts" 2>/dev/null || true

echo "Desktop launcher: $CMD_DST"
echo "Double-click it after plugging USB-C Ethernet."
