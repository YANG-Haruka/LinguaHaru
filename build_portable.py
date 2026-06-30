#!/usr/bin/env python
"""Build the LIGHTWEIGHT portable (the ~100MB "portable build", not the fat
PyInstaller freeze): an embeddable Python 3.12 + the app + ONLY the base deps.
Engines (OCR/STT/PDF/video — torch/onnxruntime/rapidocr/…) are NOT bundled; the
plugin system pip-installs them on demand into this Python at runtime (which works
because it's a real Python, unlike the frozen build — see module_manager).

    python build_portable.py web    -> dist_lite/LinguaHaru-web/
    python build_portable.py qt     -> dist_lite/LinguaHaru-desktop/

Run on Windows (the embeddable Python is win amd64). Needs network (downloads the
embeddable Python + pip + base wheels).
"""
import io
import os
import sys
import shutil
import zipfile
import urllib.request
import subprocess

PY_VER = "3.12.10"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(ROOT, "dist_lite")

# App payload copied into every portable (the backend + static config/assets +
# the plugin manifests, but NOT plugin deps).
APP_DIRS = ["core", "config", "assets", "plugins", "requirements", "glossary"]
APP_FILES = ["version.json"]
VARIANTS = {
    "web": {"name": "LinguaHaru-web", "dirs": ["webapp"],
            "reqs": ["requirements/base.txt", "requirements/web.txt"],
            "entry": "-m webapp.server", "launcher": "Start-Web.bat"},
    "qt":  {"name": "LinguaHaru-desktop", "dirs": ["qt_app"],
            "files": ["app_qt.py"],
            "reqs": ["requirements/base.txt", "requirements/qt.txt"],
            "entry": "app_qt.py", "launcher": "Start-Desktop.bat",
            "prune": "pyside6"},
}


# The full PySide6 wheel (~642 MB) ships an entire embedded Chromium (WebEngine),
# the QML/Quick stack, and 3D/Charts/Pdf/Designer modules. The desktop app uses
# ONLY QtCore/Gui/Widgets/Svg/Multimedia/Network (verified: nothing in qt_app/ or
# qfluentwidgets imports any of the below). We can't swap to PySide6-Essentials
# because PySide6-Fluent-Widgets hard-requires full PySide6, so we prune the
# unused pieces post-install. Cuts ~420 MB. KEEP opengl32sw.dll (software GL for
# GPU-less machines) and the av*.dll ffmpeg codecs (QtMultimedia TTS playback).
_PYSIDE6_PRUNE_DIRS = ["resources", "qml", "metatypes",
                       os.path.join("translations", "qtwebengine_locales")]
_PYSIDE6_PRUNE_GLOBS = [
    "Qt6WebEngine*.dll", "QtWebEngine*.pyd", "QtWebEngineProcess.exe",
    "Qt6Quick*.dll", "QtQuick*.pyd", "Qt6Qml*.dll", "QtQml*.pyd",
    "Qt63D*.dll", "Qt3D*.pyd",
    "Qt6Charts*.dll", "QtCharts*.pyd",
    "Qt6DataVisualization*.dll", "QtDataVisualization*.pyd",
    "Qt6Pdf*.dll", "QtPdf*.pyd",
    "Qt6Designer*.dll", "QtDesigner*.pyd",
]


def _prune_pyside6(dest):
    import glob as _glob
    qt = os.path.join(dest, "python", "Lib", "site-packages", "PySide6")
    if not os.path.isdir(qt):
        print("  [prune] PySide6 not found; skipping")
        return
    freed = 0

    def _size(p):
        if os.path.isdir(p):
            return sum(os.path.getsize(os.path.join(dp, fn))
                       for dp, _, fns in os.walk(p) for fn in fns)
        return os.path.getsize(p)

    for rel in _PYSIDE6_PRUNE_DIRS:
        p = os.path.join(qt, rel)
        if os.path.isdir(p):
            freed += _size(p)
            shutil.rmtree(p)
    for pat in _PYSIDE6_PRUNE_GLOBS:
        for p in _glob.glob(os.path.join(qt, pat)):
            freed += _size(p)
            os.remove(p)
    print(f"  [prune] removed {freed/1024/1024:.0f} MB of unused Qt modules")


# Isolate the embeddable Python from the BUILDER's per-user site-packages
# (~/AppData/Roaming/Python/...). Without this, pip sees the builder's packages as
# "already satisfied" and skips bundling them, and at runtime the portable would
# import the builder's machine packages -> not portable at all.
_ISOLATED_ENV = {**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""}


def _download(url):
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def build(variant):
    v = VARIANTS[variant]
    dest = os.path.join(OUT_ROOT, v["name"])
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    py_dir = os.path.join(dest, "python")
    os.makedirs(py_dir)

    # 1) embeddable Python
    print("[1/6] embeddable Python")
    with zipfile.ZipFile(io.BytesIO(_download(EMBED_URL))) as z:
        z.extractall(py_dir)
    # enable site-packages + put the app root on sys.path (so `import core` works)
    pth = next(p for p in os.listdir(py_dir) if p.endswith("._pth"))
    pth_path = os.path.join(py_dir, pth)
    with open(pth_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines = [("import site" if l.strip() == "#import site" else l) for l in lines]
    if "import site" not in lines:
        lines.append("import site")
    lines.append("..")            # the app root (parent of python/) on sys.path
    lines.append("Lib\\site-packages")
    with open(pth_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    py_exe = os.path.join(py_dir, "python.exe")

    # 2) pip
    print("[2/6] pip")
    getpip = os.path.join(py_dir, "get-pip.py")
    with open(getpip, "wb") as f:
        f.write(_download(GETPIP_URL))
    subprocess.run([py_exe, getpip, "--no-warn-script-location"], check=True, env=_ISOLATED_ENV)
    # setuptools + wheel: modern get-pip installs ONLY pip, but plugin deps that
    # ship as sdists need a build backend at install time, else they fail with
    # "Cannot import 'setuptools.build_meta'". Bundle them so runtime plugin
    # installs (OCR/STT/…) can build any sdist deps.
    # uv: a MUCH faster (parallel) installer. module_manager._uv_exe() auto-detects
    # it at python/Scripts/uv.exe and prefers it over pip for plugin installs, so
    # the heavy plugins (torch/paddle, dozens of packages) download far faster. It
    # honors the same --index-url, so the China-mirror fallback still applies.
    subprocess.run([py_exe, "-m", "pip", "install", "--no-warn-script-location",
                    "setuptools", "wheel", "uv"], check=True, env=_ISOLATED_ENV)

    # 3) base deps (NO engines)
    print("[3/6] base deps")
    cmd = [py_exe, "-m", "pip", "install", "--no-warn-script-location"]
    for req in v["reqs"]:
        cmd += ["-r", os.path.join(ROOT, req)]
    subprocess.run(cmd, check=True, env=_ISOLATED_ENV)

    # 3b) prune unused heavy framework pieces (Qt only)
    if v.get("prune") == "pyside6":
        print("[3b] prune PySide6")
        _prune_pyside6(dest)

    # 4) app payload
    print("[4/6] app payload")
    for d in APP_DIRS + v["dirs"]:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for fn in APP_FILES + v.get("files", []):
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(dest, fn))

    # 5) launcher
    print("[5/6] launcher")
    bat = os.path.join(dest, v["launcher"])
    with open(bat, "w", encoding="utf-8") as f:
        # Isolate at runtime: don't read the user's per-user site-packages or a
        # stray PYTHONPATH, so the portable uses ONLY its own bundled Python env.
        f.write("@echo off\r\ncd /d \"%~dp0\"\r\n"
                "set PYTHONNOUSERSITE=1\r\nset PYTHONPATH=\r\n"
                f"python\\python.exe {v['entry']} %*\r\n"
                "if errorlevel 1 pause\r\n")

    # 6) size
    total = sum(os.path.getsize(os.path.join(dp, fn))
                for dp, _, fns in os.walk(dest) for fn in fns)
    print(f"[6/6] done: {dest}  ({total/1024/1024:.0f} MB)")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "web"
    build(target)
