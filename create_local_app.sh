#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AI翻译配音"
APP_DIR="$HOME/Applications/$APP_NAME.app"
APP_SUPPORT_DIR="$HOME/Library/Application Support/$APP_NAME"
RUNTIME_DIR="$APP_SUPPORT_DIR/runtime"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RESOURCES_DIR="$APP_DIR/Contents/Resources"
PYTHON_BIN="/usr/bin/python3"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
mkdir -p "$RUNTIME_DIR"

ditto --norsrc "$ROOT_DIR/app.py" "$RUNTIME_DIR/app.py"
ditto --norsrc "$ROOT_DIR/desktop_app.py" "$RUNTIME_DIR/desktop_app.py"
ditto --norsrc "$ROOT_DIR/requirements.txt" "$RUNTIME_DIR/requirements.txt"
ditto --norsrc "$ROOT_DIR/config.example.yaml" "$RUNTIME_DIR/config.example.yaml"
ditto --norsrc "$ROOT_DIR/pipeline" "$RUNTIME_DIR/pipeline"

if [[ -f "$ROOT_DIR/config.yaml" ]]; then
  ditto --norsrc "$ROOT_DIR/config.yaml" "$RUNTIME_DIR/config.yaml"
fi

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>com.local.ai-translate-dub.launcher</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>LSBackgroundOnly</key>
  <false/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/$APP_NAME" <<LAUNCHER
#!/usr/bin/env bash
set -euo pipefail
cd "$RUNTIME_DIR"
export GRADIO_ANALYTICS_ENABLED=False
export HF_HUB_DISABLE_TELEMETRY=1
exec /usr/bin/arch -arm64 "$PYTHON_BIN" "$RUNTIME_DIR/desktop_app.py" >> "\$TMPDIR/ai_translate_dub.log" 2>&1
LAUNCHER

chmod +x "$MACOS_DIR/$APP_NAME"
xattr -cr "$APP_SUPPORT_DIR" || true
xattr -cr "$APP_DIR" || true
codesign --force --deep --sign - "$APP_DIR" >/dev/null 2>&1 || true

echo "$APP_DIR"
