#!/usr/bin/env bash
# ── AI 翻译配音 - 安装器 ───────────────────────────────────────────────
# 用法: ./install.sh
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AI翻译配音"
SOURCE_APP="$ROOT_DIR/dist-electron/mac-arm64/$APP_NAME.app"
INSTALL_DIR="$HOME/Applications"
DEST_APP="$INSTALL_DIR/$APP_NAME.app"

echo "╔══════════════════════════════════════╗"
echo "║   AI 翻译配音 - 安装器              ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Install ──────────────────────────────────────────────────────────
echo "→ 安装到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rm -rf "$DEST_APP" 2>/dev/null || true
ditto --norsrc --noqtn "$SOURCE_APP" "$DEST_APP"
xattr -cr "$DEST_APP" 2>/dev/null || true
echo "  ✓ 完成"

# ── Launch ───────────────────────────────────────────────────────────
echo ""
echo "→ 启动 App..."
open "$DEST_APP"
echo "  浏览器将自动打开 http://127.0.0.1:7860"
echo ""
echo "  以后可以从 Spotlight (⌘Space) 搜索 'AI翻译配音' 启动"
