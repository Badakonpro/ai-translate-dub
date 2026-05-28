#!/usr/bin/env bash
# install_electron_app.sh — Build Electron app and install to ~/Applications
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="VoxOver"
BUILD_APP="$ROOT_DIR/dist-electron/mac-arm64/$APP_NAME.app"
INSTALL_DIR="$HOME/Applications"
DEST_APP="$INSTALL_DIR/$APP_NAME.app"

echo "=== 1. Building with electron-builder ==="
rm -rf "$BUILD_APP"
export CSC_IDENTITY_AUTO_DISCOVERY=false
node_modules/.bin/electron-builder --dir --mac --arm64 &
BUILDER_PID=$!
LAST_SIZE=0
STABLE_TICKS=0
for _ in {1..300}; do
    if ! kill -0 "$BUILDER_PID" 2>/dev/null; then
        wait "$BUILDER_PID"
        break
    fi
    if [ -f "$BUILD_APP/Contents/Resources/app.asar" ]; then
        CURRENT_SIZE="$(du -sk "$BUILD_APP" 2>/dev/null | awk '{print $1}')"
        if [ "$CURRENT_SIZE" = "$LAST_SIZE" ]; then
            STABLE_TICKS=$((STABLE_TICKS + 1))
        else
            LAST_SIZE="$CURRENT_SIZE"
            STABLE_TICKS=0
        fi
    fi
    if [ "$STABLE_TICKS" -ge 10 ]; then
        # electron-builder can hang after writing the dir target on this machine.
        if kill -0 "$BUILDER_PID" 2>/dev/null; then
            echo "  Builder is still running after app output stabilized; continuing with generated app."
            kill "$BUILDER_PID" 2>/dev/null || true
            wait "$BUILDER_PID" 2>/dev/null || true
        fi
        break
    fi
    sleep 1
done
if kill -0 "$BUILDER_PID" 2>/dev/null; then
    echo "  electron-builder timed out before producing an app."
    kill "$BUILDER_PID" 2>/dev/null || true
    wait "$BUILDER_PID" 2>/dev/null || true
    exit 1
fi
if [ ! -d "$BUILD_APP" ]; then
    echo "  Build output missing: $BUILD_APP"
    exit 1
fi

echo "=== 2. Patching Info.plist (remove ElectronAsarIntegrity + NSMainNibFile) ==="
python3 - "$BUILD_APP/Contents/Info.plist" << 'PYEOF'
import plistlib, sys

plist_path = sys.argv[1]
with open(plist_path, 'rb') as f:
    plist = plistlib.load(f)
for key in ('ElectronAsarIntegrity', 'NSMainNibFile'):
    removed = plist.pop(key, None)
    print(f"  Removed {key}: {removed is not None}")
with open(plist_path, 'wb') as f:
    plistlib.dump(plist, f)
PYEOF

echo "=== 3. Copying to $INSTALL_DIR (strip xattrs) ==="
mkdir -p "$INSTALL_DIR"
rm -rf "$DEST_APP"
ditto --norsrc --noqtn "$BUILD_APP" "$DEST_APP"

echo "=== 4. Ad-hoc code signing ==="
# Sign inner frameworks/helpers first, then the outer bundle
find "$DEST_APP/Contents/Frameworks" -name "*.framework" -o -name "*.app" | sort -r | while read -r f; do
    codesign --force --sign - "$f" 2>/dev/null || true
done
find "$DEST_APP" -name "*.dylib" -o -name "*.so" | while read -r f; do
    codesign --force --sign - "$f" 2>/dev/null || true
done
codesign --force --deep --sign - "$DEST_APP" 2>&1 && echo "  Signed OK" || {
    echo "  --deep failed, trying without --deep"
    codesign --force --sign - "$DEST_APP"
}

echo "=== 5. Register with Launch Services ==="
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST_APP"

echo ""
echo "Done! Launch with:"
echo "  open '$DEST_APP'"
