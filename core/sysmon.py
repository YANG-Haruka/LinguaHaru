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


def _gpu_usage():
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, **_NO_WINDOW)
        line = (r.stdout or "").strip().splitlines()[0]
        u, mu, mt = [x.strip() for x in line.split(",")]
        return {"gpu": float(u), "gpu_mem_used": int(float(mu)), "gpu_mem_total": int(float(mt))}
    except Exception:  # noqa: BLE001
        return None


def usage():
    """Live snapshot: {cpu: %|None, gpu: %|None, gpu_mem_used/total: MB|None}.

    CPU% is since the previous call (psutil) — poll on a fixed interval so the
    first reading primes and subsequent ones are meaningful."""
    out = {"cpu": None, "gpu": None, "gpu_mem_used": None, "gpu_mem_total": None}
    try:
        import psutil
        out["cpu"] = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pass
    g = _gpu_usage()
    if g:
        out.update(g)
    return out
