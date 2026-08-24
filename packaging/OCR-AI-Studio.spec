# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH).parent
assets_dir = project_root / "ocr_ai_studio" / "assets"
icon_ico = assets_dir / "app" / "ocr-ai-studio.ico"
version_info = project_root / "packaging" / "windows_version_info.txt"
asset_files = [
    (
        str(asset),
        f"ocr_ai_studio/assets/{asset.parent.relative_to(assets_dir).as_posix()}",
    )
    for asset in assets_dir.rglob("*.png")
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=asset_files,
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name="OCR-AI-Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_ico),
    version=str(version_info),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
