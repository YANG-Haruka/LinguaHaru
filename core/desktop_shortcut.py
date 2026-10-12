"""Put a LinguaHaru shortcut on the desktop the first time the portable build
runs (Windows: LinguaHaru.lnk, macOS: LinguaHaru.app). Done once — if the user
deletes it, it stays deleted. Source checkouts are left alone."""
import os
import subprocess
import sys

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
        "$p=Join-Path $d 'LinguaHaru.lnk'; if (Test-Path $p) { exit 0 };"   # never overwrite
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
        # pythonw: no console window. -s: ignore the user's site-packages
        # (what Start-Desktop.bat does with PYTHONNOUSERSITE).
        f"$s.TargetPath={q(os.path.join(ROOT, 'python', 'pythonw.exe'))};"
        "$s.Arguments='-s app_qt.py';"
        f"$s.WorkingDirectory={q(ROOT)};"
        f"$s.IconLocation={q(os.path.join(ROOT, 'assets', 'img', 'ico.ico'))};"
        "$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, timeout=30, creationflags=0x08000000)  # CREATE_NO_WINDOW


def write_mac_app(app, src_root=ROOT, root=None):
    """Write a LinguaHaru.app bundle at `app`, taking the launcher + icon from
    `src_root`. With `root`, the launcher starts that install (root.txt — the
    Desktop copy); without it, the install is the folder holding the .app (the
    copy shipped inside the download, built by tools/build_portable.py)."""
    contents = os.path.join(app, "Contents")
    os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
    os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
    import shutil
    icns = os.path.join(src_root, "assets", "img", "icon.icns")
    if os.path.isfile(icns):
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
<key>NSMicrophoneUsageDescription</key><string>LinguaHaru listens to the microphone for real-time voice translation. / LinguaHaru 需要麦克风进行实时语音翻译。</string>
<key>NSAudioCaptureUsageDescription</key><string>LinguaHaru captures system audio for real-time voice translation. / LinguaHaru 需要系统声音进行实时语音翻译。</string>
</dict></plist>
""")
    # A NATIVE launcher (tools/macos_launcher.c) that spawns python as a child:
    # with a script that exec()s python, macOS attributes the microphone /
    # screen-recording permission to python or Terminal and never prompts for
    # LinguaHaru.app.
    exe = os.path.join(contents, "MacOS", "LinguaHaru")
    shutil.copy2(os.path.join(src_root, "assets", "macos", "LinguaHaru-launcher"), exe)
    os.chmod(exe, 0o755)
    if root:
        with open(os.path.join(contents, "Resources", "root.txt"), "w", encoding="utf-8") as f:
            f.write(root + "\n")
    if sys.platform == "darwin":
        # Ad-hoc sign the BUNDLE (identifier com.linguaharu.desktop, Info.plist
        # bound, resources sealed). Unsigned, a downloaded copy is reported as
        # "damaged" with no way to open it, and TCC keys permissions to the
        # launcher instead of the app. Builds made on Windows sign with rcodesign.
        subprocess.run(["codesign", "--force", "--sign", "-", app],
                       capture_output=True, timeout=60, check=False)


def _mac():
    app = os.path.join(os.path.expanduser("~/Desktop"), "LinguaHaru.app")
    if os.path.exists(app):      # never overwrite a launcher the user already has
        return
    write_mac_app(app, root=ROOT)


def ensure_desktop_shortcut():
    """Create the shortcut once (portable builds only). Best-effort."""
    from core.log_config import app_logger
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
        app_logger.info("Desktop shortcut created (or one already existed)")
    except Exception as e:  # noqa: BLE001 — a shortcut must never block startup
        app_logger.warning(f"Desktop shortcut not created: {e}")
