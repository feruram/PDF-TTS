# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_delvewheel_libs_directory

tcl_dir = Path(sys.base_prefix) / "tcl"
if (tcl_dir / "tcl8.6/init.tcl").exists():
    os.environ["TCL_LIBRARY"] = str(tcl_dir / "tcl8.6")
    os.environ["TK_LIBRARY"] = str(tcl_dir / "tk8.6")

hiddenimports = ['pynput.keyboard._win32', 'pynput.mouse._win32']
hiddenimports += collect_submodules('comtypes.gen')
hiddenimports += collect_submodules('pywinauto', filter=lambda name: '.tests' not in name)
datas, binaries = collect_delvewheel_libs_directory('tesserocr')


a = Analysis(
    ['autopdf_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas + [(str(Path(sys.prefix) / "share/autopdf-tessdata"), "share/autopdf-tessdata")],
    hiddenimports=hiddenimports,
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
    name='AutoPDF',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
