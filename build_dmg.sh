#!/usr/bin/env bash
# build_dmg.sh — Build a distributable DMG from the signed local app.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="QuietKit"
VERSION="$(node -p "require('./package.json').version")"
APP_PATH="$HOME/Applications/$APP_NAME.app"
DMG_PATH="$ROOT_DIR/dist-electron/$APP_NAME-$VERSION-arm64.dmg"
STAGING_DIR="$(mktemp -d /tmp/ai-translate-dmg.XXXXXX)"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

echo "=== 1. Building and signing app ==="
"$ROOT_DIR/install_electron_app.sh"

echo "=== 2. Preparing DMG staging folder ==="
mkdir -p "$STAGING_DIR"
ditto --norsrc --noqtn "$APP_PATH" "$STAGING_DIR/$APP_NAME.app"
ln -s /Applications "$STAGING_DIR/Applications"

echo "=== 3. Verifying staged app signature ==="
codesign --verify --deep --strict --verbose=2 "$STAGING_DIR/$APP_NAME.app"

echo "=== 4. Creating DMG ==="
mkdir -p "$ROOT_DIR/dist-electron"
rm -f "$DMG_PATH" "$DMG_PATH.blockmap"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo ""
echo "Done! DMG written to:"
echo "  $DMG_PATH"
