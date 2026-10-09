#!/usr/bin/env bash
# Clone ORNG LED CONTROL from GitHub (or update), bootstrap, venue-start.
# Usage on a Mac:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/<branch>/scripts/macos-install.sh | bash
# Or after clone:
#   ./scripts/macos-install.sh
set -euo pipefail

REPO_URL="${ORNG_REPO_URL:-https://github.com/nagornyydmytro/ORNG-LED-CONTROL-V1.git}"
REPO_DIR="${ORNG_REPO_DIR:-$HOME/ORNG-LED-CONTROL-V1}"
BRANCH="${ORNG_BRANCH:-main}"

echo "=== ORNG LED — macOS install ==="
echo "Repo:   $REPO_URL"
echo "Branch: $BRANCH"
echo "Dir:    $REPO_DIR"
echo ""

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing: $1" >&2
    echo "Install with Homebrew, e.g.: brew install $2" >&2
    exit 1
  }
}

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Install from https://brew.sh then re-run." >&2
  exit 1
fi

need git git
need npm node
# Prefer python@3.12 from brew if available.
if ! command -v python3 >/dev/null 2>&1; then
  echo "Installing python@3.12 via Homebrew ..."
  brew install python@3.12
fi

if [[ -d "$REPO_DIR/.git" ]]; then
  echo "Updating existing clone ..."
  git -C "$REPO_DIR" fetch origin
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
else
  echo "Cloning ..."
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
chmod +x scripts/*.sh ORNG-LED-START.command 2>/dev/null || true

echo ""
echo "Bootstrap (venv + npm) ..."
./scripts/bootstrap.sh

echo ""
echo "Creating Desktop launcher ..."
./scripts/install-macos-launcher.sh || true

echo ""
echo "Starting venue boot (Art-Net + Arm + browser) ..."
echo "Plug USB-C Ethernet first if you use real lights."
echo ""
exec ./scripts/venue-start.sh
