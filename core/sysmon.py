"""Lightweight system monitor for the task dashboard: CPU/GPU live usage + a
static hardware summary (is translation/STT running on GPU or CPU).

All best-effort: every probe degrades gracefully (None) if a dependency or device
is absent, so the dashboard just shows "—" instead of erroring.
"""
import os
import shutil
import subprocess

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
_hw_cache = None


def _nvidia_present():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.WinDLL("nvcuda.dll")
            return True
        except Exception:  # noqa: BLE001
            return False
    return bool(shutil.which("nvidia-smi"))


def hardware_summary():
    """{device: 'GPU'|'CPU', gpu: bool, name: str, detail: str} — what the heavy
    work (STT transcription, OCR) will actually run on (torch CUDA). Cached."""
    global _hw_cache
    if _hw_cache is not None:
        return _hw_cache
    info = {"device": "CPU", "gpu": False, "name": "", "detail": ""}
    try:
        import torch
        if torch.cuda.is_available():
            try:
                name = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001
                name = "NVIDIA GPU"
            info = {"device": "GPU", "gpu": True, "name": name,
                    "detail": f"CUDA {getattr(torch.version, 'cuda', '') or ''}".strip()}
        else:
            # torch present but CPU-only — flag if a GPU exists (so the user knows
            # to install the CUDA torch build for GPU acceleration).
            info["detail"] = ("GPU present · torch=CPU" if _nvidia_present()
                              else "no GPU")
    except Exception:  # noqa: BLE001 — torch missing
        info["detail"] = ("GPU present · torch missing" if _nvidia_present()
                          else "no GPU")
    _hw_cache = info
    return info


def _gpu_mem():
    """(used_MB, total_MB) VRAM from nvidia-smi, or (None, None)."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return (None, None)
    try:
        r = subprocess.run(
            [smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, **_NO_WINDOW)
        mu, mt = [x.strip() for x in (r.stdout or "").strip().splitlines()[0].split(",")]
        return (int(float(mu)), int(float(mt)))
    except Exception:  # noqa: BLE001
        return (None, None)


def _gpu_util_nvsmi():
    """nvidia-smi utilization.gpu (%) — the COMPUTE/graphics engine only. Used as a
    cross-platform fallback when the Windows Task-Manager counters aren't available."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, **_NO_WINDOW)
        return float((r.stdout or "").strip().splitlines()[0].strip())
    except Exception:  # noqa: BLE001
        return None


import re as _re
import time as _time
_pdh = None            # (query_handle, [(path, counter_handle), ...])
_pdh_built = 0.0
_last_gpu = None
_PDH_REBUILD_S = 8.0   # rebuild periodically to pick up new/gone GPU processes


def _gpu_util_windows():
    """GPU utilization the way Windows Task Manager shows it: the MAX across engine
    types (3D / Compute / Copy / Video…) of the per-type summed utilization, using
    the same '\\GPU Engine(*)\\Utilization Percentage' perf counters Task Manager
    reads. nvidia-smi's utilization.gpu only covers the compute engine, so it misses
    3D/graphics work — this matches the number users see in Task Manager. None if
    unavailable (non-Windows, no pywin32, or no GPU)."""
    global _pdh, _pdh_built, _last_gpu
    if os.name != "nt":
        return None
    try:
        import win32pdh
    except Exception:  # noqa: BLE001 — pywin32 not present
        return None
    try:
        now = _time.time()
        if _pdh is None or (now - _pdh_built) > _PDH_REBUILD_S:
            if _pdh is not None:
                try:
                    win32pdh.CloseQuery(_pdh[0])
                except Exception:  # noqa: BLE001
                    pass
                _pdh = None
            q = win32pdh.OpenQuery()
            try:
                paths = win32pdh.ExpandCounterPath(r"\GPU Engine(*)\Utilization Percentage")
            except Exception:  # noqa: BLE001
                paths = []
            handles = []
            for p in paths:
                try:
                    handles.append((p, win32pdh.AddCounter(q, p)))
                except Exception:  # noqa: BLE001
                    pass
            if not handles:
                try:
                    win32pdh.CloseQuery(q)
                except Exception:  # noqa: BLE001
                    pass
                return _last_gpu
            win32pdh.CollectQueryData(q)          # prime (rate counters need 2 samples)
            _pdh = (q, handles)
            _pdh_built = now
            return _last_gpu                      # real value from the next poll
        q, handles = _pdh
        win32pdh.CollectQueryData(q)
        from collections import defaultdict
        per_engine = defaultdict(float)
        for path, h in handles:
            try:
                _t, val = win32pdh.GetFormattedCounterValue(h, win32pdh.PDH_FMT_DOUBLE)
            except Exception:  # noqa: BLE001 — instance gone / not yet 2 samples
                continue
            m = _re.search(r'engtype_([A-Za-z0-9]+)', path)
            per_engine[m.group(1) if m else "other"] += max(0.0, float(val))
        if not per_engine:
            return _last_gpu
        _last_gpu = int(round(min(100.0, max(per_engine.values()))))
        return _last_gpu
    except Exception:  # noqa: BLE001
        return _last_gpu


def usage():
    """Live snapshot: {cpu: %|None, gpu: %|None, gpu_mem_used/total: MB|None}.

    gpu% matches Windows Task Manager (all engines) where possible, else falls back
    to nvidia-smi. CPU% is since the previous call (psutil) — poll on a fixed
    interval so the first reading primes and subsequent ones are meaningful."""
    out = {"cpu": None, "gpu": None, "gpu_mem_used": None, "gpu_mem_total": None}
    try:
        import psutil
        out["cpu"] = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pass
    gu = _gpu_util_windows()
    if gu is None:
        gu = _gpu_util_nvsmi()
    out["gpu"] = gu
    mu, mt = _gpu_mem()
    out["gpu_mem_used"], out["gpu_mem_total"] = mu, mt
    return out
