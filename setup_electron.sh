#!/usr/bin/env bash
# ── Electron App Setup Script ─────────────────────────────────────────────
# This script installs npm dependencies and builds the Electron app.
# Requires: Node.js >= 18, npm
#
# Usage:
#   ./setup_electron.sh        # Install deps and test in dev mode
#   ./setup_electron.sh build  # Full production build
#   ./setup_electron.sh dist   # Build and create distributable DMG/ZIP
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "==> Installing npm dependencies..."
npm install

if [ "${1:-}" = "dist" ]; then
  echo "==> Building distributable package..."
  npm run dist:dmg
  echo ""
  echo "Done! Look in dist-electron/ for the DMG and ZIP files."
elif [ "${1:-}" = "build" ]; then
  echo "==> Packaging Electron app..."
  npm run pack
  echo ""
  echo "Done! Look in dist-electron/ for the packaged app."
else
  echo "==> Starting Electron in dev mode..."
  echo "    (Make sure Python dependencies are installed: pip3 install -r requirements.txt)"
  npm start
fi
