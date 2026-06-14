"""Install / uninstall optional modules (PDF, Image OCR, Video/Audio) by running
pip in the current interpreter. Shared by the Web (FastAPI) and Qt apps.

Heavy and slow (some pull torch/paddle); callers should run these in a
background thread and tell the user a restart is needed to (de)activate.
"""
import os
import sys
import subprocess

from config.log_config import app_logger

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# UI module name -> (requirements file, [pip package names to uninstall])
MODULE_SPECS = {
    "PDF": ("requirements-pdf.txt", ["babeldoc"]),
    "Image OCR": ("requirements-ocr.txt",
                  ["paddleocr", "paddlepaddle", "rapidocr", "onnxruntime",
                   "opencv-python-headless"]),
    "Video/Audio": ("requirements-video.txt", ["faster-whisper", "funasr"]),
}


def _run_pip(args):
    """Run `python -m pip <args>`; return (ok, tail_of_output)."""
    cmd = [sys.executable, "-m", "pip", *args]
    app_logger.info(f"Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=REPO_ROOT)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out[-4000:]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def install_module(name):
    spec = MODULE_SPECS.get(name)
    if not spec:
        return False, f"Unknown module: {name}"
    return _run_pip(["install", "-r", spec[0]])


def uninstall_module(name):
    spec = MODULE_SPECS.get(name)
    if not spec:
        return False, f"Unknown module: {name}"
    return _run_pip(["uninstall", "-y", *spec[1]])
