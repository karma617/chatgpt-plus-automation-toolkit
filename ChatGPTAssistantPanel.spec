# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = ["_tkinter", "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"]
hiddenimports += collect_submodules("modules")
hiddenimports += collect_submodules("mail_adapters")
hiddenimports += collect_submodules("control_panel")
hiddenimports += collect_submodules("camoufox")
hiddenimports += collect_submodules("browserforge")
hiddenimports += collect_submodules("apify_fingerprint_datapoints")
hiddenimports += collect_submodules("language_tags")
hiddenimports += collect_submodules("ua_parser")
hiddenimports += collect_submodules("screeninfo")
hiddenimports += collect_submodules("geoip2")
hiddenimports += collect_submodules("maxminddb")

block_cipher = None

tcl_root = Path(sys.base_prefix) / "tcl"
tcl_roots = [
    Path(sys.base_prefix) / "tcl",
    Path(sys.base_prefix) / "Library" / "lib",
    Path(sys.prefix) / "tcl",
    Path(sys.prefix) / "Library" / "lib",
]
datas = []
datas += collect_data_files("camoufox")
datas += collect_data_files("browserforge")
datas += collect_data_files("apify_fingerprint_datapoints")
datas += collect_data_files("language_tags")
datas += collect_data_files("ua_parser")
datas += collect_data_files("screeninfo")
datas += collect_data_files("geoip2")
datas += collect_data_files("maxminddb")
for tcl_root in tcl_roots:
    if (tcl_root / "tcl8.6" / "init.tcl").exists():
        # PyInstaller 6 + Python 3.13 runtime hook expects _tcl_data under dist/_internal
        datas.append((str(tcl_root / "tcl8.6"), "_tcl_data"))
        break
for tcl_root in tcl_roots:
    if (tcl_root / "tk8.6" / "tk.tcl").exists():
        # Conda puts Tcl/Tk data under Library/lib instead of sys.base_prefix/tcl.
        datas.append((str(tcl_root / "tk8.6"), "_tk_data"))
        break


a = Analysis(
    ["control_panel_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChatGPTAssistantPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ChatGPTAssistantPanel",
)
