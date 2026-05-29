#!/usr/bin/env bash
# install_electron_app.sh — Build Electron app and install to ~/Applications
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="QuietKit"
BUILD_APP="$ROOT_DIR/dist-electron/mac-arm64/$APP_NAME.app"
INSTALL_DIR="$HOME/Applications"
DEST_APP="$INSTALL_DIR/$APP_NAME.app"

echo "=== 1. Building with electron-builder ==="
rm -rf "$BUILD_APP"
export CSC_IDENTITY_AUTO_DISCOVERY=false
export NO_UPDATE_NOTIFIER=1
touch dist-electron/.metadata_never_index 2>/dev/null || true
node_modules/.bin/electron-builder --dir --mac --arm64
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
