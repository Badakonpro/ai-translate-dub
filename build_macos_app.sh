#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv-build}"
APP_NAME="AI翻译配音"
INSTALL_DIR="$HOME/Applications"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip wheel setuptools
"$VENV_DIR/bin/python" -m pip install -r requirements.txt
"$VENV_DIR/bin/python" -m pip install pyinstaller

"$VENV_DIR/bin/pyinstaller" --clean --noconfirm ai_translate_dub.spec

if [[ -d "dist/$APP_NAME.app" ]]; then
  xattr -cr "dist/$APP_NAME.app" || true
  mkdir -p "$INSTALL_DIR"
  rm -rf "$INSTALL_DIR/$APP_NAME.app"
  cp -R "dist/$APP_NAME.app" "$INSTALL_DIR/$APP_NAME.app"
  xattr -c "$INSTALL_DIR/$APP_NAME.app" || true
  xattr -c "$INSTALL_DIR/$APP_NAME.app/Contents/Frameworks/Python3.framework" || true
  codesign --force --deep --sign - "$INSTALL_DIR/$APP_NAME.app" || true
fi

echo "Done: $ROOT_DIR/dist/$APP_NAME.app"
echo "Installed: $INSTALL_DIR/$APP_NAME.app"
