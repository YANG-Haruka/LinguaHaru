"""Install / uninstall optional modules (PDF, Image OCR, Video/Audio) by running
pip in the current interpreter. Shared by the Web (FastAPI) and Qt apps.

Heavy and slow (some pull torch/paddle); callers should run these in a
background thread and tell the user a restart is needed to (de)activate.
"""
import os
import re
import sys
import json
import subprocess
import importlib.metadata
import urllib.request

from core.log_config import app_logger

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# UI module name -> (requirements file, [pip package names to uninstall])
# "Video/Audio" and "Real-Time Voice" share the same STT stack (video.txt);
# they are listed as separate plugins so each gets its own model selection, but
# (un)installing either affects the shared speech packages.
MODULE_SPECS = {
    "PDF": ("requirements/pdf.txt", ["babeldoc"]),
    "Image OCR": ("requirements/ocr.txt",
                  ["paddleocr", "paddlepaddle", "rapidocr", "onnxruntime",
                   "opencv-python-headless"]),
    # Video/Audio and Real-Time Voice share the STT stack (video.txt). Uninstall
    # removes the engines + helpers so the plugin really reports unavailable; we
    # deliberately do NOT remove torch/torchaudio (large, shared base ML libs the
    # user may have installed as a specific CUDA build).
    "Video/Audio": ("requirements/video.txt",
                    ["faster-whisper", "funasr", "qwen-asr", "imageio-ffmpeg",
                     "soundcard", "ten-vad"]),
    "Real-Time Voice": ("requirements/video.txt",
                        ["faster-whisper", "funasr", "qwen-asr",
                         "soundcard", "ten-vad"]),
    # Quick-Translate audio = read-aloud (edge-tts) + voice input (shared STT).
    # Uninstall removes only edge-tts (keep the shared STT used by other plugins).
    "翻译语音输入": ("requirements/speechio.txt", ["edge-tts"]),
}

# Module -> the one PyPI package whose version we surface for "new version
# available" prompts. Only PDF (BabelDOC) is tracked: it ships fast-moving
# fixes and is a single self-contained package, so a simple `pip install -U`
# is safe. The OCR/Video stacks pull torch/paddle and aren't auto-upgraded.
_VERSION_PACKAGE = {"PDF": "babeldoc"}

# PyPI JSON metadata, official first then a mainland-China-friendly mirror.
_PYPI_JSON = [
    "https://pypi.org/pypi/{pkg}/json",
    "https://pypi.tuna.tsinghua.edu.cn/pypi/{pkg}/json",
]


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


def packages_to_uninstall(name):
    """Packages safe to remove when uninstalling ``name``: this plugin's packages
    MINUS any package still listed by ANOTHER plugin (so a shared dependency — the
    STT stack used by Video/Audio + Real-Time Voice + 翻译语音输入 — is kept while
    any sibling still needs it). Empty list = everything is shared, remove nothing."""
    spec = MODULE_SPECS.get(name)
    if not spec:
        return []
    mine = set(spec[1])
    others = set()
    for other, (_req, pkgs) in MODULE_SPECS.items():
        if other != name:
            others |= set(pkgs)
    return sorted(mine - others)


def uninstall_module(name):
    spec = MODULE_SPECS.get(name)
    if not spec:
        return False, f"Unknown module: {name}"
    pkgs = packages_to_uninstall(name)
    if not pkgs:
        # All of this plugin's packages are shared with another plugin -> removing
        # them would break the sibling. Nothing to uninstall at the pip level.
        return True, "All dependencies are shared with another plugin; kept."
    return _run_pip(["uninstall", "-y", *pkgs])


def upgrade_module(name):
    """Upgrade a module's packages to the latest from its requirements file."""
    spec = MODULE_SPECS.get(name)
    if not spec:
        return False, f"Unknown module: {name}"
    return _run_pip(["install", "-U", "-r", spec[0]])


def _version_tuple(v):
    """'0.6.3' -> (0, 6, 3) for ordered comparison; non-numeric -> (0,)."""
    nums = re.findall(r"\d+", str(v or ""))[:4]
    return tuple(int(n) for n in nums) if nums else (0,)


def _installed_version(pkg):
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def _latest_version(pkg, timeout=6):
    for template in _PYPI_JSON:
        url = template.format(pkg=pkg)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            latest = (data.get("info") or {}).get("version")
            if latest:
                return str(latest).strip()
        except Exception:  # noqa: BLE001 - any failure just means "can't tell"
            continue
    return None


def check_module_update(name):
    """Check PyPI for a newer version of a module's tracked package.

    Returns ``{package, current, latest, update}`` or ``None`` when the module
    has no tracked package, isn't installed, or PyPI can't be reached. Network
    only — it never installs anything (the upgrade is a separate, user-confirmed
    call to ``upgrade_module``).
    """
    pkg = _VERSION_PACKAGE.get(name)
    if not pkg:
        return None
    current = _installed_version(pkg)
    if not current:
        return None
    latest = _latest_version(pkg)
    if not latest:
        return None
    return {
        "package": pkg,
        "current": current,
        "latest": latest,
        "update": _version_tuple(latest) > _version_tuple(current),
    }
