"""macOS: ask for Microphone + Screen & System Audio Recording up front, so a new
user just clicks "Allow" in the system prompts instead of hunting through
System Settings later (Windows needs neither).

Only asks when we run under LinguaHaru.app's native launcher: TCC attributes a
request to the *responsible* app, so asking from Start-Desktop.command would put
the grant on Terminal instead of LinguaHaru. Asks once per status — anything the
user already decided (allowed/denied) is never re-prompted here; the live page
still shows its own hint if a capture is denied later."""
import os
import subprocess
import sys

from core.log_config import app_logger


def _under_app_launcher():
    try:
        parent = subprocess.run(["ps", "-o", "comm=", "-p", str(os.getppid())],
                                capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        return False
    return parent.endswith(".app/Contents/MacOS/LinguaHaru")


def _request_screen_capture():
    """CoreGraphics via ctypes — no pyobjc needed. Request shows the system
    prompt and adds LinguaHaru to the Screen & System Audio Recording list."""
    import ctypes
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    if not cg.CGPreflightScreenCaptureAccess():
        cg.CGRequestScreenCaptureAccess()
        app_logger.info("Requested Screen & System Audio Recording access")


def _migrate_tcc_identity():
    """Once per install: drop TCC grants pinned to an old cdhash. Builds before
    7.0.2 were signed with the default (cdhash) designated requirement, so every
    copy / version was a different app to TCC and the grant never matched again:
    the screen-recording prompt came back on every launch, and the "−" button in
    System Settings does NOT clear the stale requirement. `tccutil reset` for our
    own bundle id does (no admin needed); the next request then binds to the
    stable identifier requirement and survives every later update."""
    from core import backend
    if backend.get_config("mac_tcc_dr_migrated", False):
        return
    for svc in ("ScreenCapture", "Microphone"):
        subprocess.run(["/usr/bin/tccutil", "reset", svc, "com.linguaharu.desktop"],
                       capture_output=True, timeout=10, check=False)
    backend.set_config("mac_tcc_dr_migrated", True)
    app_logger.info("Reset stale TCC grants for com.linguaharu.desktop (one-time)")


def request_permissions():
    """Fire the system prompts (non-blocking). Call once the main window shows."""
    if sys.platform != "darwin" or not _under_app_launcher():
        return
    try:
        _migrate_tcc_identity()
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"TCC identity migration skipped: {e}")
    try:
        import AVFoundation as av
        status = av.AVCaptureDevice.authorizationStatusForMediaType_(av.AVMediaTypeAudio)
        if status == 0:   # AVAuthorizationStatusNotDetermined
            # Screen capture is asked AFTER the mic prompt is answered, so the two
            # system dialogs don't stack on top of each other.
            av.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                av.AVMediaTypeAudio, lambda _granted: _request_screen_capture())
            app_logger.info("Requested Microphone access")
            return
    except Exception as e:  # noqa: BLE001 — pyobjc AVFoundation missing: mic asks on first use
        app_logger.debug(f"Mic permission request skipped: {e}")
    try:
        _request_screen_capture()
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Screen capture permission request failed: {e}")
