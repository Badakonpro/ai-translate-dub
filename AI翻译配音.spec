# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=[('pipeline', 'pipeline'), ('config.example.yaml', '.')],
    hiddenimports=['gradio', 'whisper', 'openai', 'ollama', 'pysrt', 'yaml', 'torch', 'requests', 'huggingface_hub'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name='VoxOver',
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
    name='VoxOver',
)
app = BUNDLE(
    coll,
    name='VoxOver.app',
    icon='assets/app-icon.icns',
    bundle_identifier=None,
)
