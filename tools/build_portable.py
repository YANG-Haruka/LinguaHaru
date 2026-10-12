#!/usr/bin/env python
"""Build the LIGHTWEIGHT portable (the ~100MB "portable build", not the fat
PyInstaller freeze): an embeddable Python 3.12 + the app + ONLY the base deps.
Engines (OCR/STT/PDF/video — torch/onnxruntime/rapidocr/…) are NOT bundled; the
plugin system pip-installs them on demand into this Python at runtime (which works
because it's a real Python, unlike the frozen build — see module_manager).

    python build_portable.py web    -> dist/LinguaHaru-web/
    python build_portable.py qt     -> dist/LinguaHaru-desktop/
    python build_portable.py mac    -> dist/LinguaHaru-<ver>-desktop-mac.zip (Apple Silicon)

Run on Windows (the embeddable Python is win amd64; the Mac builds are cross-
built, see build_mac). Needs network (downloads Python + pip + base wheels).
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
# This file lives in tools/; the app root is the repo root one level up.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "dist")

# App payload copied into every portable (the backend + static config/assets +
# the plugin manifests, but NOT plugin deps).
APP_DIRS = ["core", "config", "assets", "plugins", "requirements", "glossary"]
APP_FILES = ["version.json"]
VARIANTS = {
    "web": {"name": "LinguaHaru-web", "dirs": ["webapp"],
            "reqs": ["requirements/base.txt", "requirements/web.txt"],
            "entry": "-m webapp.server", "launcher": "Start-Web.bat"},
    # The desktop app's LAN mode starts `python -m webapp.server` as a subprocess,
    # so the portable must ALSO ship the webapp package + its FastAPI/uvicorn deps —
    # otherwise the LAN admin feature can't launch in the portable (ModuleNotFound).
    "qt":  {"name": "LinguaHaru-desktop", "dirs": ["qt_app", "webapp"],
            "files": ["app_qt.py"],
            "reqs": ["requirements/base.txt", "requirements/qt.txt", "requirements/web.txt"],
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
    # system_config.json is the BUILDER's local (gitignored) config — personal
    # defaults, onboarding_seen, etc. It must NEVER ship: core/paths.py seeds a
    # fresh one from system_config.default.json on the user's first run.
    for d in APP_DIRS + v["dirs"]:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                      "system_config.json"))
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


# --------------------------------------------------------------------------- #
# macOS desktop portable — cross-built from Windows.
# There is no "embeddable" Python for macOS; python-build-standalone ships a
# relocatable CPython we can unpack anywhere. Deps are resolved by uv FOR macOS
# (--python-platform), so markers like `pywin32; sys_platform == "win32"` are
# evaluated for the Mac, not for the Windows builder (pip --platform can't).
# --------------------------------------------------------------------------- #
PBS_TAG, PBS_PY = "20261009", "3.12.15"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
           "{tag}/cpython-{py}%2B{tag}-{arch}-apple-darwin-install_only.tar.gz")
MAC_VARIANTS = {  # name -> (pbs arch, uv --python-platform, zip suffix)
    "mac": ("aarch64", "aarch64-apple-darwin", "mac"),   # Apple Silicon (M1+) only
}
# Same idea as _prune_pyside6, Mac layout (frameworks + abi3 .so modules).
_PYSIDE6_MAC_PRUNE = [
    "Qt/lib/QtWebEngine*.framework", "Qt/lib/QtQuick*.framework", "Qt/lib/QtQml*.framework",
    "Qt/lib/Qt3D*.framework", "Qt/lib/QtCharts*.framework",
    "Qt/lib/QtDataVisualization*.framework", "Qt/lib/QtPdf*.framework",
    "Qt/lib/QtDesigner*.framework", "Qt/qml", "Qt/resources", "Qt/libexec/QtWebEngineProcess*",
    "Qt/translations/qtwebengine_locales",
    "QtWebEngine*.so", "QtQuick*.so", "QtQml*.so", "Qt3D*.so", "QtCharts*.so",
    "QtDataVisualization*.so", "QtPdf*.so", "QtDesigner*.so",
]
_MACH_O = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf")

MAC_LAUNCHER = """#!/bin/bash
# Same as double-clicking LinguaHaru.app (kept for people who look for a
# "Start" file). Starting python from here would make macOS attribute the
# microphone / screen-recording permission to Terminal instead of LinguaHaru.
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine ./LinguaHaru.app 2>/dev/null   # this app only
open ./LinguaHaru.app
"""
MAC_README = """LinguaHaru for macOS (13 Ventura or newer, Apple Silicon M1 or later)

1. Unzip, then double-click "LinguaHaru.app".
   The first time, macOS says it can't verify the app. Click the GREY "Done"
   button — NOT the blue "Move to Trash" — then open System Settings >
   Privacy & Security, scroll to the bottom and click "Open Anyway" next to
   LinguaHaru, then confirm. (On macOS 14 and older,
   right-click LinguaHaru.app > Open also works.)
2. On first start LinguaHaru asks for the Microphone and for Screen & System
   Audio Recording (needed to translate what your Mac is playing) — click
   Allow, then restart LinguaHaru once.
3. A "LinguaHaru" shortcut is also added to your Desktop. Don't move this
   folder afterwards, or the shortcut breaks.

macOS 13 以上、M 系列芯片。
1. 解压后双击 LinguaHaru.app。第一次会弹出「Apple 无法验证…」：
   点左边灰色的「完成」，不要点蓝色的「移到废纸篓」；然后打开「系统设置 >
   隐私与安全性」，拉到最下面，在 LinguaHaru 旁点「仍要打开」并确认。（macOS 14 及更早版本也可以右键 > 打开。）
2. 首次启动会请求「麦克风」和「屏幕与系统录音」（翻译电脑正在播放的声音需要）
   权限，点允许，然后重启一次 LinguaHaru。
3. 桌面上也会生成 LinguaHaru 快捷方式。之后不要再移动本文件夹。
"""


# Ad-hoc bundle signing on Windows (no codesign there): rcodesign, pinned + verified.
RCODESIGN_URL = ("https://github.com/indygreg/apple-platform-rs/releases/download/"
                 "apple-codesign%2F0.29.0/apple-codesign-0.29.0-x86_64-pc-windows-msvc.zip")
RCODESIGN_SHA256 = "54bb500e2da7a8de02fcae0f331d1cac6e6d7173b4281042ff9c528ba3159aaa"


def _rcodesign():
    import hashlib
    tool_dir = os.path.join(OUT_ROOT, ".tools", "rcodesign-0.29.0")
    exe = os.path.join(tool_dir, "rcodesign.exe")
    if not os.path.exists(exe):
        data = _download(RCODESIGN_URL)
        if hashlib.sha256(data).hexdigest() != RCODESIGN_SHA256:
            raise RuntimeError("rcodesign download failed its SHA-256 check")
        os.makedirs(tool_dir, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(n for n in z.namelist() if n.endswith("/rcodesign.exe"))
            with open(exe, "wb") as f:
                f.write(z.read(member))
    return exe


def _sign_mac_app(app):
    """Ad-hoc sign the whole bundle. A downloaded app whose bundle isn't signed
    is reported as "damaged" on Apple Silicon (no "Open Anyway"), and TCC would
    key permissions to the bare launcher instead of com.linguaharu.desktop.
    The designated requirement is the bundle id, not the default cdhash, so TCC
    grants survive the Desktop copy and every update (a cdhash DR made each copy
    and version a different app: the user was re-asked on every launch)."""
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--force", "--sign", "-",
                        f"-r={MAC_DESIGNATED_REQ}", app], check=True)
    else:
        # rcodesign wants the COMPILED requirement (csreq -b of MAC_DESIGNATED_REQ).
        # rcodesign reads "<scope>:<value>", so a Windows path's drive letter
        # ("F:\...") was taken as a scope and the requirement silently landed on a
        # nested path "F" ("main:" is ALSO read as a path in 0.29): the app kept a
        # cdhash DR. A colon-free RELATIVE path, run from ROOT, is unambiguous.
        subprocess.run([_rcodesign(), "sign", "--code-requirements-file",
                        "assets/macos/designated.req", app], check=True, cwd=ROOT)
        _check_mac_designated(app)


def _check_mac_designated(app):
    """Fail the build unless the main executable's designated requirement is the
    bundle id (a cdhash DR re-breaks every TCC grant on each copy / update)."""
    import re
    out = subprocess.run([_rcodesign(), "print-signature-info", app],
                         capture_output=True, text=True, check=True).stdout
    main = out.split("path: Contents\\MacOS\\LinguaHaru", 1)[-1].split("- path:", 1)[0]
    m = re.search(r"slot: RequirementSet \(2\)\s+magic: \w+\s+length: (\d+)", main)
    # An EMPTY requirement set is just its 12-byte header -> macOS falls back to
    # a cdhash DR. Ours holds the 44-byte identifier requirement.
    if not m or int(m.group(1)) <= 12:
        raise SystemExit("mac signing: designated requirement NOT applied to the main "
                         f"executable (RequirementSet length {m and m.group(1)})")
    print(f"  designated requirement OK (RequirementSet {m.group(1)} bytes)")


MAC_DESIGNATED_REQ = 'designated => identifier "com.linguaharu.desktop"'


def _uv():
    try:
        subprocess.run([sys.executable, "-m", "uv", "--version"], check=True,
                       capture_output=True)
    except Exception:  # noqa: BLE001
        subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
    return [sys.executable, "-m", "uv"]


def _mac_icon(dest):
    """assets/img/icon.icns (for the desktop .app) from the square Windows icon."""
    from PIL import Image
    ico = Image.open(os.path.join(ROOT, "assets", "img", "ico.ico"))
    ico.size = max(ico.ico.sizes())
    ico.convert("RGBA").resize((1024, 1024), Image.LANCZOS).save(
        os.path.join(dest, "assets", "img", "icon.icns"))


def build_mac(variant):
    import glob as _glob
    import tarfile
    arch, platform, suffix = MAC_VARIANTS[variant]
    v = VARIANTS["qt"]
    dest = os.path.join(OUT_ROOT, f"LinguaHaru-desktop-{suffix}")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    print("[1/6] standalone Python for macOS")
    data = _download(PBS_URL.format(tag=PBS_TAG, py=PBS_PY, arch=arch))
    modes = {}     # Windows drops the exec bits; remember them for the zip
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            modes[m.name] = m.mode
        t.extractall(dest, filter="data")     # symlinks become copies on Windows
    sp = os.path.join(dest, "python", "lib", "python3.12", "site-packages")

    print("[2/6] base deps (resolved for macOS) + setuptools/wheel/uv")
    cmd = _uv() + ["pip", "install", "--target", sp, "--python-version", "3.12",
                   "--python-platform", platform, "--only-binary", ":all:"]
    for req in v["reqs"]:
        cmd += ["-r", os.path.join(ROOT, req)]
    subprocess.run(cmd + ["setuptools", "wheel", "uv"], check=True, env=_ISOLATED_ENV)
    # --target puts console scripts in <target>/bin; module_manager looks for uv
    # next to the interpreter (python/bin/uv).
    for exe in ("uv", "uvx"):
        src = os.path.join(sp, "bin", exe)
        if os.path.exists(src):
            shutil.move(src, os.path.join(dest, "python", "bin", exe))

    print("[3/6] prune PySide6")
    qt = os.path.join(sp, "PySide6")
    for pat in _PYSIDE6_MAC_PRUNE:
        for p in _glob.glob(os.path.join(qt, pat)):
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

    print("[4/6] app payload")
    for d in APP_DIRS + v["dirs"]:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                      "system_config.json"))
    for fn in APP_FILES + v.get("files", []):
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(dest, fn))
    _mac_icon(dest)
    # The app ships as LinguaHaru.app (native launcher): opening it — even the
    # very first time — attributes mic / screen-recording permission to it.
    sys.path.insert(0, ROOT)
    from core.desktop_shortcut import write_mac_app
    write_mac_app(os.path.join(dest, "LinguaHaru.app"), src_root=dest)
    _sign_mac_app(os.path.join(dest, "LinguaHaru.app"))

    print("[5/6] launcher")
    with open(os.path.join(dest, "Start-Desktop.command"), "w", newline="\n") as f:
        f.write(MAC_LAUNCHER)
    with open(os.path.join(dest, "README-Mac.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(MAC_README)

    print("[6/6] zip (with unix permissions)")
    # The app's own version (version.json is only bumped once assets are live).
    with open(os.path.join(ROOT, "core", "version.py"), encoding="utf-8") as f:
        import re
        ver = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
    out = os.path.join(OUT_ROOT, f"LinguaHaru-{ver}-desktop-{suffix}.zip")
    top = os.path.basename(dest)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fns in os.walk(dest):
            for fn in fns:
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, dest).replace("\\", "/")
                with open(p, "rb") as fh:
                    head = fh.read(4)
                executable = (modes.get(rel, 0) & 0o111 or head in _MACH_O
                              or head[:2] == b"#!" or rel.startswith("python/bin/"))
                zi = zipfile.ZipInfo.from_file(p, f"{top}/{rel}")
                zi.create_system = 3                       # unix, so modes apply
                zi.external_attr = ((0o100755 if executable else 0o100644) << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as fh:
                    z.writestr(zi, fh.read())
    print(f"done: {out}  ({os.path.getsize(out)/1024/1024:.0f} MB)")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "web"
    if target in MAC_VARIANTS:
        build_mac(target)
    else:
        build(target)
