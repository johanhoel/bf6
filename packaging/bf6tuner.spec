# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec. Driven by packaging/build.py, which prepares _build/ first."""

from pathlib import Path

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "packaging" / "_build"

block_cipher = None

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # Only the encrypted bundle ships. The plain JSON in data/ is deliberately
    # left out so a shipped build cannot fall back to editable files.
    datas=[(str(BUILD / "bf6tuner.db"), ".")],
    hiddenimports=["bf6tuner._keyring"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest", "pdb", "test",
        "numpy", "matplotlib", "PIL", "pytest", "setuptools", "pip",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
        "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BF6Tuner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(BUILD / "bf6tuner.ico"),
    version=str(BUILD / "version_info.txt"),
)
