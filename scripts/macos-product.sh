#!/usr/bin/env bash
# Product status for macOS. This is not an installer.
# A native Mac and a signed app bundle were not available in this workspace.
set -euo pipefail

echo "ORNG LED CONTROL macOS product: NOT VERIFIED"
echo "Blocker: no Mac and no signed .app bundle in this workspace."
echo "scripts/macos-install.sh clones the git repo and uses Homebrew, npm and a venv."
echo "That path is a developer checkout, not the installed product."
echo "Gatekeeper is not disabled by any script in this repository."
echo "Pi, Windows and web acceptance do not wait on this blocker."
exit 2
