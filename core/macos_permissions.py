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
    from core import backend
    # Ask at most ONCE, ever. CGPreflightScreenCaptureAccess() checks full SCREEN
    # capture, so it stays False for a user who granted only "System Audio
    # Recording" (all we need) — re-requesting on that popped the system dialog on
    # every launch even after the user had allowed it. The live page still shows
    # its own hint if system-audio capture is actually denied.
    if backend.get_config("mac_screen_capture_asked", False):
        return
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    if not cg.CGPreflightScreenCaptureAccess():
        cg.CGRequestScreenCaptureAccess()
        app_logger.info("Requested Screen & System Audio Recording access")
    backend.set_config("mac_screen_capture_asked", True)


def request_permissions():
    """Fire the system prompts (non-blocking). Call once the main window shows."""
    if sys.platform != "darwin" or not _under_app_launcher():
        return
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
