# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: empaqueta el HUD en un .exe autónomo (sin Python instalado).

    python -m PyInstaller --noconfirm --clean framemod.spec

Resultado: dist/FRAMEMOD/FRAMEMOD.exe (+ la carpeta data/ al lado).
El ejecutable detecta PPSSPP y la región del juego por sí mismo; el panel de
ajustes (F8) escribe config/hud.json junto al .exe.
"""
import os

ROOT = os.path.abspath(os.getcwd())

# La base de datos local viaja con el ejecutable.  Los mapas que se aprendan
# con --learn se escriben en esta misma carpeta (junto al .exe).
datas = []
for folder in ("data",):
    src = os.path.join(ROOT, folder)
    if os.path.isdir(src):
        datas.append((src, folder))

hiddenimports = [
    "game_states",
    "learn",
    "lookup_engine",
    "mapper",
    "psp_mem",
    "scraper",
    "tekken_notation",
    "tracker_hybrid",
]

a = Analysis(
    ["tools/hud.py"],
    pathex=[os.path.join(ROOT, "tools")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FRAMEMOD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # necesario para --learn (preguntas por consola)
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
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FRAMEMOD",
)
