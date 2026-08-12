# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition.

One single, windowed executable — ``dist\\CS2ClipGenerator.exe`` — that a user
double-clicks. ``console=False`` means no terminal window ever appears, and a
one-file build means there is nothing to unzip: just the .exe. FFmpeg is
deliberately *not* bundled: it is a large binary with its own licensing story,
and the app already knows how to find it or ask for it.
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

# One-file, windowed build: a single CS2ClipGenerator.exe with no console.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CS2ClipGenerator",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)
