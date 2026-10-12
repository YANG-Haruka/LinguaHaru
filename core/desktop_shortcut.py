"""Put a LinguaHaru shortcut on the desktop the first time the portable build
runs (Windows: LinguaHaru.lnk, macOS: LinguaHaru.app). Done once — if the user
deletes it, it stays deleted. Source checkouts are left alone."""
import os
import subprocess
import sys

from core.log_config import app_logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DONE_KEY = "desktop_shortcut_done"


def _is_portable():
    """The portable builds run their own Python from <root>/python."""
    return os.path.abspath(sys.executable).startswith(os.path.join(ROOT, "python") + os.sep)


def _windows():
    def q(s):   # PowerShell single-quoted literal
        return "'" + s.replace("'", "''") + "'"
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"   # honors OneDrive redirection
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'LinguaHaru.lnk'));"
        # pythonw: no console window. -s: ignore the user's site-packages
        # (what Start-Desktop.bat does with PYTHONNOUSERSITE).
        f"$s.TargetPath={q(os.path.join(ROOT, 'python', 'pythonw.exe'))};"
        "$s.Arguments='-s app_qt.py';"
        f"$s.WorkingDirectory={q(ROOT)};"
        f"$s.IconLocation={q(os.path.join(ROOT, 'assets', 'img', 'ico.ico'))};"
        "$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, timeout=30, creationflags=0x08000000)  # CREATE_NO_WINDOW


def _mac():
    app = os.path.join(os.path.expanduser("~/Desktop"), "LinguaHaru.app")
    contents = os.path.join(app, "Contents")
    os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
    os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
    icns = os.path.join(ROOT, "assets", "img", "icon.icns")
    if os.path.isfile(icns):
        import shutil
        shutil.copy2(icns, os.path.join(contents, "Resources", "icon.icns"))
    with open(os.path.join(contents, "Info.plist"), "w", encoding="utf-8") as f:
        f.write("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>LinguaHaru</string>
<key>CFBundleIdentifier</key><string>com.linguaharu.desktop</string>
<key>CFBundleExecutable</key><string>LinguaHaru</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>NSMicrophoneUsageDescription</key><string>Real-time voice translation listens to the microphone.</string>
</dict></plist>
""")
    exe = os.path.join(contents, "MacOS", "LinguaHaru")
    root = ROOT.replace('"', '\\"')
    with open(exe, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n"
                f'cd "{root}"\n'
                "export PYTHONNOUSERSITE=1\nunset PYTHONPATH\n"
                f'exec "{root}/python/bin/python3" app_qt.py\n')
    os.chmod(exe, 0o755)


def ensure_desktop_shortcut():
    """Create the shortcut once (portable builds only). Best-effort."""
    try:
        from core import backend
        if not _is_portable() or backend.get_config(_DONE_KEY, False):
            return
        if sys.platform == "win32":
            _windows()
        elif sys.platform == "darwin":
            _mac()
        else:
            return
        backend.set_config(_DONE_KEY, True)
        app_logger.info("Desktop shortcut created")
    except Exception as e:  # noqa: BLE001 — a shortcut must never block startup
        app_logger.warning(f"Desktop shortcut not created: {e}")
