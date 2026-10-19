# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
datas = [
    (str(root / "ui" / "style.qss"), "ui"),
    (str(root / "packaging" / "README-PORTABLE.txt"), "."),
]
for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
    datas += copy_metadata(package)

analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ETS-Viewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas,
                 strip=False, upx=False, name="ETS-Viewer")
