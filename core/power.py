# core/power.py
# Keep heavy work running at full speed in the background on Windows.
#
# Two independent protections (both no-ops off Windows / on old Windows):
#   1. disable_background_throttling() — opt the whole PROCESS out of Windows'
#      background power throttling (EcoQoS). Set ONCE at startup so a minimized
#      or unfocused window never gets its CPU throttled mid-task. Because it's
#      process-wide it covers EVERYTHING — document/video/subtitle translation,
#      quick translate, and real-time voice — with no per-task wiring.
#   2. begin_activity()/end_activity() (ref-counted) or the keep_awake() context
#      manager — while ANY task is in progress, tell Windows not to sleep. The
#      last end_activity() releases it so the PC can still sleep when idle.
import contextlib
import threading

from core.log_config import app_logger

_lock = threading.Lock()
_busy = 0

# SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _kernel32():
    """The kernel32 DLL on Windows, else None (so every call degrades to a no-op)."""
    import sys
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32
    except Exception:  # noqa: BLE001 — no ctypes/Windows API -> no-op
        return None


def disable_background_throttling():
    """Process-wide opt-out of Windows EcoQoS background throttling. Call once at
    startup. Safe + a no-op on non-Windows or pre-1809 Windows (the API is absent)."""
    k = _kernel32()
    if k is None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _PowerThrottlingState(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_uint32),
                        ("ControlMask", ctypes.c_uint32),
                        ("StateMask", ctypes.c_uint32)]

        PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
        ProcessPowerThrottling = 4   # PROCESS_INFORMATION_CLASS

        # Set argtypes/restype: without them ctypes mishandles the 64-bit process
        # HANDLE and the call silently fails (returns 0).
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
        k.SetProcessInformation.restype = wintypes.BOOL

        state = _PowerThrottlingState()
        state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION
        # Control execution-speed throttling, and set it OFF (StateMask = 0) =
        # "run me at full speed even in the background".
        state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        state.StateMask = 0
        ok = k.SetProcessInformation(k.GetCurrentProcess(), ProcessPowerThrottling,
                                     ctypes.byref(state), ctypes.sizeof(state))
        if ok:
            app_logger.info("Background power throttling disabled (EcoQoS opt-out)")
        else:
            app_logger.debug("Power-throttling opt-out call returned false")
    except Exception as e:  # noqa: BLE001 — never let this break startup
        app_logger.debug(f"Power-throttling opt-out unavailable: {e}")


def _set_exec_state(flags):
    k = _kernel32()
    if k is not None:
        try:
            import ctypes
            from ctypes import wintypes
            k.SetThreadExecutionState.argtypes = [wintypes.DWORD]
            k.SetThreadExecutionState.restype = wintypes.DWORD
            k.SetThreadExecutionState(ctypes.c_uint32(flags).value)
        except Exception:  # noqa: BLE001
            pass


def begin_activity():
    """Mark a task as in progress: keep the system awake until end_activity()."""
    global _busy
    with _lock:
        _busy += 1
        first = _busy == 1
    if first:
        _set_exec_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)


def end_activity():
    """Balance a begin_activity(); the last one lets the PC sleep again."""
    global _busy
    with _lock:
        _busy = max(0, _busy - 1)
        last = _busy == 0
    if last:
        _set_exec_state(_ES_CONTINUOUS)


@contextlib.contextmanager
def keep_awake():
    """Context manager around a unit of work (translation run, live session)."""
    begin_activity()
    try:
        yield
    finally:
        end_activity()
