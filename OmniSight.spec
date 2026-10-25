# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['omnisight_app.py'],
    pathex=[],
    binaries=[('build/ffmpeg.exe', '.')],
    datas=[('models/yolov8n.onnx', 'models')],
    hiddenimports=['PyQt6.QtSvg', 'PyQt6.QtMultimedia', 'Crypto.Cipher.AES', 'onnxruntime'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['imageio_ffmpeg'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OmniSight',
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
    icon=['omnisight.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OmniSight',
)
