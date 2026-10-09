#!/usr/bin/env bash
# Put a Desktop .app launcher with the ORNG logo icon.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP="${HOME}/Desktop"
APP_NAME="ORNG LED CONTROL.app"
APP_PATH="$DESKTOP/$APP_NAME"
PNG_SRC="$REPO_ROOT/branding/orng-led-control-1024.png"
LOGO_PNG="$REPO_ROOT/branding/orng-led-control-logo.png"

chmod +x "$REPO_ROOT/scripts/"*.sh
chmod +x "$REPO_ROOT/ORNG-LED-CONTROL.command" 2>/dev/null || true

# Remove old START launchers if present.
rm -rf "$DESKTOP/ORNG LED START.app" 2>/dev/null || true
rm -f "$DESKTOP/ORNG LED START.command" 2>/dev/null || true

if [[ ! -f "$PNG_SRC" ]]; then
  if [[ -f "$LOGO_PNG" ]] && command -v sips >/dev/null 2>&1; then
    sips -z 1024 1024 "$LOGO_PNG" --out "$PNG_SRC" >/dev/null
  else
    echo "Missing $PNG_SRC (and cannot build from logo png)." >&2
    exit 1
  fi
fi

TMP="$(mktemp -d)"
ICONSET="$TMP/orng.iconset"
mkdir -p "$ICONSET"

make_size() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$PNG_SRC" --out "$ICONSET/$name" >/dev/null
}

make_size 16 icon_16x16.png
make_size 32 'icon_16x16@2x.png'
make_size 32 icon_32x32.png
make_size 64 'icon_32x32@2x.png'
make_size 128 icon_128x128.png
make_size 256 'icon_128x128@2x.png'
make_size 256 icon_256x256.png
make_size 512 'icon_256x256@2x.png'
make_size 512 icon_512x512.png
make_size 1024 'icon_512x512@2x.png'

ICNS="$TMP/orng-led-control.icns"
iconutil -c icns "$ICONSET" -o "$ICNS"

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"
cp "$ICNS" "$APP_PATH/Contents/Resources/AppIcon.icns"

cat >"$APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ORNG LED CONTROL</string>
  <key>CFBundleDisplayName</key><string>ORNG LED CONTROL</string>
  <key>CFBundleIdentifier</key><string>local.orng.led.control</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>ORNG LED CONTROL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
PLIST

cat >"$APP_PATH/Contents/MacOS/ORNG LED CONTROL" <<EOF
#!/bin/bash
REPO="$REPO_ROOT"
if [[ ! -f "\$REPO/scripts/venue-start.sh" ]]; then
  REPO="\$HOME/ORNG-LED-CONTROL-V1"
fi
cd "\$REPO" || exit 1
chmod +x scripts/*.sh 2>/dev/null || true
exec ./scripts/venue-start.sh
EOF
chmod +x "$APP_PATH/Contents/MacOS/ORNG LED CONTROL"

cp -f "$REPO_ROOT/ORNG-LED-CONTROL.command" "$DESKTOP/ORNG LED CONTROL.command"
chmod +x "$DESKTOP/ORNG LED CONTROL.command"

xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true
xattr -dr com.apple.quarantine "$DESKTOP/ORNG LED CONTROL.command" 2>/dev/null || true
xattr -dr com.apple.quarantine "$REPO_ROOT/scripts" 2>/dev/null || true

rm -rf "$TMP"

echo "Desktop app: $APP_PATH"
echo "Double-click it after plugging USB-C Ethernet."
