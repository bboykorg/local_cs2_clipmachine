# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition.

One folder, one executable. FFmpeg is deliberately *not* bundled: it is a large
binary with its own licensing story, and the app already knows how to find it or
ask for it.
"""

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

hidden_imports = [
    *collect_submodules("demoparser2"),
    "pandas",
    "cramjam",
    "psutil",
]
# obsws-python is optional; include it when it is installed.
try:
    import obsws_python  # noqa: F401

    hidden_imports += collect_submodules("obsws_python")
except ImportError:
    pass

binaries = collect_dynamic_libs("demoparser2")

a = Analysis(
    ["cs2_clip_generator/app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=[],
    hiddenimports=hidden_imports,
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CS2ClipGenerator",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CS2ClipGenerator",
)
