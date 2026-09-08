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
    # The settings database ships as plain JSON - see database.py's module
    # docstring for why it's not encrypted.
    datas=[(str(ROOT / "data" / f"{name}.json"), "data") for name in
           ("gpu_db", "cpu_db", "cfg_commands", "ingame_settings", "system_tweaks")],
    hiddenimports=["bf6tuner._build_info"],
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

COMMON = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
    icon=str(BUILD / "bf6tuner.ico"),
    version=str(BUILD / "version_info.txt"),
)

# Two binaries from one analysis, because Windows makes an executable either a
# GUI one or a console one at link time and there is no runtime switch.
#
#   BF6Tuner.exe      GUI subsystem. Double-click, no console window flashes up.
#                     A GUI-subsystem process has no stdout, so it cannot be
#                     used from a terminal - the shell does not even wait for it.
#   BF6Tuner-cli.exe  Console subsystem. Real stdout, real exit codes, output
#                     redirection works, and a shell waits for it to finish.
exe_gui = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="BF6Tuner", console=False, **COMMON,
)

exe_cli = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="BF6Tuner-cli", console=True, **COMMON,
)
