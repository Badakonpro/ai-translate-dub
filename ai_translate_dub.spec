# -*- mode: python ; coding: utf-8 -*-

import os
for _key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
             "HTTPS_PROXY", "https_proxy", "FTP_PROXY", "ftp_proxy",
             "RSYNC_PROXY", "GRPC_PROXY", "grpc_proxy", "NO_PROXY", "no_proxy"):
    os.environ.pop(_key, None)

from PyInstaller.utils.hooks import collect_all


datas = [
    ("config.example.yaml", "."),
]
binaries = []
hiddenimports = []

for package in [
    "gradio",
    "whisper",
    "openai",
    "ollama",
    "pysrt",
    "yaml",
]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


a = Analysis(
    ["desktop_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxOver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoxOver",
)
app = BUNDLE(
    coll,
    name="VoxOver.app",
    icon="assets/app-icon.icns",
    bundle_identifier="com.local.ai-translate-dub",
    info_plist={
        "NSHighResolutionCapable": "True",
        "NSRequiresAquaSystemAppearance": "False",
    },
)
