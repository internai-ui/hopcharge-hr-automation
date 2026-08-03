# -*- mode: python ; coding: utf-8 -*-
"""
hopcharge.spec — PyInstaller recipe to build the one-click HopCharge HR app.

You run this ONCE on each operating system you want a build for:
    pyinstaller hopcharge.spec

It produces, in the dist/ folder:
    • macOS:   dist/HopChargeHR.app   (a normal Mac app you can double-click)
    • Windows: dist/HopChargeHR/HopChargeHR.exe   (a folder with the .exe inside)

Notes baked in below:
    • The web UI (static/) is bundled as read-only data.
    • The spaCy English model is collected explicitly — PyInstaller does not find
      it automatically, and CV name-detection needs it.
    • FastAPI/uvicorn/google libs have a few hidden imports declared so nothing
      is missing at runtime.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

datas = []
binaries = []
hiddenimports = []

# --- Bundle the web UI (index.html etc.) as read-only data under "static/" ---
import os
if os.path.isdir("static"):
    for root, _dirs, files in os.walk("static"):
        for f in files:
            full = os.path.join(root, f)
            datas.append((full, root))  # keep folder structure

# --- spaCy + the English model (the tricky one) ---
for pkg in ("spacy", "en_core_web_sm", "thinc", "cymem", "preshed", "blis", "murmurhash", "srsly", "catalogue", "wasabi", "spacy_legacy", "spacy_loggers", "langcodes"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

# --- Web stack + Google client hidden imports PyInstaller can miss ---
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("googleapiclient")
hiddenimports += collect_submodules("google")
hiddenimports += [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    "pandas", "openpyxl", "pdfplumber", "fitz", "bs4",
    "google.oauth2.service_account", "googleapiclient.discovery",
]

# Pull in data files some libs ship (certificates, etc.)
for pkg in ("certifi", "pdfplumber", "pdfminer"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2", "notebook"],
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
    name="HopChargeHR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # keep a small status window; it shows the data-folder path & errors
    disable_windowed_traceback=False,
    argv_emulation=True,   # lets macOS file-drag/double-click work
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HopChargeHR",
)

# macOS: also wrap the COLLECT output into a .app bundle.
import sys as _sys
if _sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="HopChargeHR.app",
        icon=None,
        bundle_identifier="com.hopcharge.hr",
        info_plist={
            "CFBundleName": "HopCharge HR",
            "CFBundleDisplayName": "HopCharge HR Dashboard",
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
        },
    )
