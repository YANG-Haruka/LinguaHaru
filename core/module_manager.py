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

# Plugin metadata now lives in plugins/<key>/plugin.json (+ requirements.txt),
# loaded via core.plugins_registry — adding/editing a plugin is a folder drop, no
# code change here. "Video/Audio" and "Real-Time Voice" share the STT stack, so
# uninstalling one keeps any package another plugin still lists (incl. the
# torch/torchaudio "shared_packages" the user may have as a specific CUDA build).
from core import plugins_registry

# Backward-compatible mapping view {name: (requirements_path, packages)} built from
# the manifests — some call sites (qt worker, web server, lib-size calc) still use
# the dict form. The manifests in plugins/<key>/ are the source of truth.
MODULE_SPECS = {m["name"]: (m["requirements_path"], m.get("packages", []))
                for m in plugins_registry.all_plugins().values()}

# PyPI JSON metadata, official first then a mainland-China-friendly mirror.
_PYPI_JSON = [
    "https://pypi.org/pypi/{pkg}/json",
    "https://pypi.tuna.tsinghua.edu.cn/pypi/{pkg}/json",
]

# Mainland-China PyPI mirror used as the auto-fallback (full mirror, serves both
# metadata AND wheels, so it works where files.pythonhosted.org is throttled).
_PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
_PYPI_OFFICIAL = "https://pypi.org/simple"


import shutil


# Optional live-progress sink: a callable(str) invoked with each output line as a
# job runs. The UI no longer shows raw lines; instead a _ProgressParser converts
# them into a PERCENTAGE (see make_progress_parser). Jobs run serially (single
# worker), so a module global is safe.
_progress_cb = None


def set_progress_callback(cb):
    """Set (or clear with None) the per-line progress sink for the next job."""
    global _progress_cb
    _progress_cb = cb


class _ProgressParser:
    """Turn pip/uv install output (and tqdm model-download lines) into a MONOTONIC
    fraction + a short stage word, so the UI shows real PROGRESS instead of a wall
    of log lines. ``emit(frac, stage)`` is called with the overall fraction mapped
    into [base, base+span].

    Granularity is best-effort: uv resolves a package total then prints one line
    per installed package; pip prints Collecting/Downloading/Installing lines; the
    model phase reports a true tqdm percentage. We never go backwards."""

    def __init__(self, emit, base=0.0, span=1.0):
        self._emit = emit
        self._base = base
        self._span = span
        self._total = 0
        self._done = 0
        self._frac = 0.0          # overall (already includes base/span)

    def _push(self, local_frac, stage):
        local_frac = max(0.0, min(1.0, local_frac))
        overall = self._base + self._span * local_frac
        if overall <= self._frac:
            return                # monotonic — ignore regressions
        self._frac = overall
        try:
            self._emit(round(self._frac, 3), stage)
        except Exception:  # noqa: BLE001 — progress must never break a job
            pass

    def feed(self, line):
        l = (line or "").strip()
        if not l:
            return
        # Total package count (uv: "Resolved N packages"; pip: "Installing
        # collected packages: a, b, c").
        m = re.search(r"(?:Resolved|Prepared|Found)\s+(\d+)\s+package", l)
        if m:
            self._total = max(self._total, int(m.group(1)))
            self._push(0.05, "resolving")
            return
        m = re.match(r"Installing collected packages:\s*(.+)", l)
        if m:
            self._total = max(self._total, len([x for x in m.group(1).split(",") if x.strip()]))
        # tqdm model-download percentage ("... 45%|####|") — a true percentage.
        m = re.search(r"(\d{1,3})%\|", l)
        if m:
            self._push(int(m.group(1)) / 100.0, "downloading")
            return
        # Per-package signals (uv "+ pkg==ver"; pip Collecting/Downloading/Installing).
        if (l.startswith("+ ") or l.startswith("Collecting ") or l.startswith("Downloading ")
                or l.startswith("Installing ") or l.startswith("Using cached ")):
            self._done += 1
            frac = (self._done / self._total) if self._total else min(0.9, self._done * 0.04)
            self._push(0.1 + 0.88 * frac, "installing")

    def done(self, stage="done"):
        self._push(1.0, stage)


def make_progress_parser(emit, base=0.0, span=1.0):
    """Factory for a _ProgressParser whose .feed(line) is used as the
    set_progress_callback / download_plugin_model progress_cb."""
    return _ProgressParser(emit, base, span)


def _emit(line):
    if _progress_cb and line:
        try:
            _progress_cb(line)
        except Exception:  # noqa: BLE001 — progress must never break the install
            pass


def _run(cmd):
    """Run a command list, STREAMING output lines to the progress sink; return
    (ok, tail_of_output)."""
    app_logger.info(f"Running: {' '.join(cmd)}")
    tail = []
    # On Windows, hide the child console window (uv/pip would otherwise flash a
    # black terminal on the GUI).
    no_window = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **no_window)
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                tail.append(line)
                if len(tail) > 200:
                    del tail[0]
                _emit(line)
        proc.wait()
        return proc.returncode == 0, "\n".join(tail)[-4000:]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _uv_exe():
    """A uv executable if available — bundled next to python.exe (portable build)
    or on PATH. uv installs MUCH faster than pip (parallel, cached resolver), so it
    is used when present; pip is the universal fallback. Returns None if absent."""
    exe = "uv.exe" if os.name == "nt" else "uv"
    cand = os.path.join(os.path.dirname(sys.executable), exe)
    if os.path.exists(cand):
        return cand
    cand = os.path.join(os.path.dirname(sys.executable), "Scripts", exe)
    if os.path.exists(cand):
        return cand
    return shutil.which("uv")


def _frozen_block():
    """In a PyInstaller build sys.executable is the app exe, not a Python — pip/uv
    can't install into it. Plugin install requires a real Python (the portable
    build), so refuse cleanly instead of spawning the exe and failing cryptically."""
    if getattr(sys, "frozen", False):
        return (False, "Plugin install is not available in the packaged (PyInstaller) "
                       "build. Use the portable build to install plugins.")
    return None


_pypi_index = None   # probed once per process


def pick_pypi_index():
    """The PyPI index URL to install from. Explicit env / config first, else probe
    the official PyPI and fall back to the Tsinghua mirror when it's unreachable
    (so mainland-China users can install plugin deps — torch/paddle etc. — without
    timing out). Mirror used ONLY when official is unreachable, so users who can
    reach pypi.org aren't slowed. Set PIP_INDEX_URL or config 'pypi_index' to pin."""
    global _pypi_index
    if _pypi_index is not None:
        return _pypi_index
    env = os.environ.get("PIP_INDEX_URL") or os.environ.get("UV_DEFAULT_INDEX")
    if env:
        _pypi_index = env
        return _pypi_index
    try:
        import json as _json
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            cfg_idx = _json.load(f).get("pypi_index")
        if cfg_idx:
            _pypi_index = cfg_idx
            return _pypi_index
    except Exception:  # noqa: BLE001
        pass
    # Probe the WHEEL host (files.pythonhosted.org, a Fastly CDN), NOT pypi.org:
    # in mainland China the metadata host often answers while the CDN that actually
    # serves the wheels is throttled/blocked — which made installs crawl after the
    # probe wrongly chose "official". HTTPError (e.g. 404) still means the server
    # responded, so treat it as reachable; only a conn/timeout error -> mirror.
    import urllib.error
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://files.pythonhosted.org/packages/",
                                   method="HEAD"), timeout=4)
        _pypi_index = _PYPI_OFFICIAL
        app_logger.info("PyPI index: official")
    except urllib.error.HTTPError:
        _pypi_index = _PYPI_OFFICIAL
        app_logger.info("PyPI index: official")
    except Exception:  # noqa: BLE001 — unreachable/blocked -> China mirror
        _pypi_index = _PYPI_MIRROR
        app_logger.info("PyPI index: Tsinghua mirror (wheel host unreachable)")
    return _pypi_index


def _install_cmd(req, index, upgrade):
    uv = _uv_exe()
    up = ["--upgrade"] if upgrade else []
    idx = ["--index-url", index]
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *idx, *up, "-r", req]
    return [sys.executable, "-m", "pip", "install", *idx, *up, "-r", req]


def _run_install(req, upgrade=False):
    """Install from a requirements file into THIS interpreter. uv (with --python
    pointing at our interpreter) when available, else `python -m pip`. Uses a
    mainland-China PyPI mirror automatically when the official wheel host is
    unreachable, AND auto-retries on the mirror if an official install fails (so a
    wrong probe / transient CDN block still recovers instead of just erroring)."""
    blocked = _frozen_block()
    if blocked:
        return blocked
    index = pick_pypi_index()
    ok, out = _run(_install_cmd(req, index, upgrade))
    if not ok and index != _PYPI_MIRROR:
        app_logger.info("Install failed on %s — retrying on China mirror", index)
        _emit("⚠ 官方源安装失败，正在切换国内镜像重试… / retrying on China mirror…")
        ok, out2 = _run(_install_cmd(req, _PYPI_MIRROR, upgrade))
        out = (out + "\n--- retry on mirror ---\n" + out2)[-4000:]
    return ok, out


def _run_uninstall(pkgs):
    """Uninstall packages from THIS interpreter (uv if available, else pip)."""
    blocked = _frozen_block()
    if blocked:
        return blocked
    uv = _uv_exe()
    if uv:
        cmd = [uv, "pip", "uninstall", "--python", sys.executable, *pkgs]
    else:
        cmd = [sys.executable, "-m", "pip", "uninstall", "-y", *pkgs]
    return _run(cmd)


def _refresh_after_change():
    """After an install/uninstall the running process has a STALE view of
    site-packages (its import finders cached the dir before the change), so
    find_spec / importlib.metadata wouldn't see the new/removed package — which is
    why a just-installed plugin still showed 'not installed' and lib size 0.
    Invalidate the import caches + drop the lib-size cache so the next status/usage
    call is correct without a restart. Both the Web and Qt paths call this."""
    import importlib
    importlib.invalidate_caches()
    try:
        from core.optional_modules import clear_size_caches
        clear_size_caches()
    except Exception:  # noqa: BLE001
        pass


def _norm(pkg):
    """Canonical distribution name (PEP 503): lowercase, runs of -_. -> -."""
    return re.sub(r"[-_.]+", "-", str(pkg)).lower()


def _delta_path(key):
    """File recording the packages a plugin's install ADDED (freeze diff), so an
    uninstall can remove exactly those (minus what siblings added) — true cleanup
    of transitive deps WITHOUT the risk of an autoremove deleting base/undeclared
    deps the app needs at runtime."""
    from core.paths import DATA_DIR
    d = os.path.join(DATA_DIR, "plugin_state")
    os.makedirs(d, exist_ok=True)
    # _norm lowercases + collapses separators but does NOT strip path separators;
    # a manifest key like "/escape" or "a/b" could write outside plugin_state.
    # Keep only safe slug chars so the state file can't escape the dir.
    safe = re.sub(r"[^a-z0-9_-]", "_", _norm(key))[:64] or "_"
    return os.path.join(d, f"{safe}.json")


def _plugin_installed(key):
    """A sibling 'still needs' a shared package only if it was actually INSTALLED
    (has a recorded freeze-delta), not merely declared. The voice trio
    (Video/Audio + Real-Time Voice + 翻译语音输入) all declare the same STT
    packages, so without this gate none of them could ever be uninstalled — each
    thought a (never-installed) sibling needed the stack, leaving it 'available'
    after a 'successful' uninstall."""
    return bool(key) and os.path.exists(_delta_path(key))


def _freeze_names():
    """Set of currently-installed distribution names (canonical)."""
    return {_norm(d.metadata["Name"]) for d in importlib.metadata.distributions()
            if d.metadata and d.metadata.get("Name")}


def _record_install_delta(key, before):
    """After an install, persist (now - before) = the dists this plugin pulled in."""
    try:
        added = sorted(_freeze_names() - before)
        with open(_delta_path(key), "w", encoding="utf-8") as f:
            json.dump(added, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001 — recording is best-effort
        app_logger.warning(f"Could not record install delta for {key}: {e}")


# PyTorch CUDA wheel index. cu121 is broadly driver-compatible (>=525); override
# via config "torch_cuda_index" (e.g. cu124), or set it to "" to force the CPU
# build even on a GPU machine.
_PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu121"
# Mainland-China fallback for CUDA torch wheels: the Aliyun mirror of
# download.pytorch.org/whl. It is a FLAT autoindex (one directory of .whl files),
# NOT a PEP 503 simple index, so it can't be used with --index-url (pip would 404
# on .../torch/) — it must be used with --find-links, and we pin the exact
# +cuNNN version so pip can't fall back to the CPU torch on the PyPI mirror.
_PYTORCH_OFFICIAL_HOST = "https://download.pytorch.org/whl/"
_PYTORCH_CHINA_FINDLINKS = "https://mirrors.aliyun.com/pytorch-wheels/{cu}/"


def _cu_tag(index):
    """The cuNNN tag from a pytorch wheel index URL (cu121, cu124 …), or None."""
    if not index.startswith(_PYTORCH_OFFICIAL_HOST):
        return None
    tail = index[len(_PYTORCH_OFFICIAL_HOST):].strip("/")
    return tail if tail.startswith("cu") else None


def _pytorch_host_reachable(index):
    """HEAD-probe the wheel index host. HTTPError still means the server answered
    (reachable); only a connection/timeout error means blocked -> use the mirror.
    Mirrors pick_pypi_index()'s logic so China users don't stall on the slow
    download.pytorch.org CDN."""
    import urllib.error
    try:
        urllib.request.urlopen(
            urllib.request.Request(index, method="HEAD"), timeout=4)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001 — unreachable/blocked
        return False


def _wheel_platform_tag():
    """The current interpreter's wheel platform tag (win_amd64 / linux_x86_64).
    None for platforms the mirror doesn't carry (skip the GPU swap there)."""
    if os.name == "nt":
        return "win_amd64"
    if sys.platform.startswith("linux"):
        return "linux_x86_64"
    return None


def _china_cuda_torch_pins(cu):
    """Discover the newest torch/torchaudio +cuNNN wheels on the Aliyun mirror that
    match THIS interpreter (cpXY) and platform, and return
    (find_links_url, ["torch==V+cuNNN", "torchaudio==V+cuNNN"]) or None.

    Pinning the exact local version is what makes --find-links safe: the PyPI
    mirror has no +cuNNN local version, so pip can only satisfy the pin from the
    mirror — it can't silently grab a newer CPU torch."""
    import html
    import re
    import urllib.error
    plat = _wheel_platform_tag()
    if not plat:
        return None
    pytag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    url = _PYTORCH_CHINA_FINDLINKS.format(cu=cu)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            page = html.unescape(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, Exception):  # noqa: BLE001 — mirror down -> skip
        return None
    hrefs = re.findall(r'href="([^"]+\.whl)"', page)
    pins = []
    for pkg in ("torch", "torchaudio"):
        # torch-2.5.1+cu121-cp311-cp311-win_amd64.whl
        rx = re.compile(rf"^{pkg}-(\d+\.\d+\.\d+)\+{re.escape(cu)}-{pytag}-{pytag}-{re.escape(plat)}\.whl$")
        vers = sorted(
            (m.group(1) for m in (rx.match(h) for h in hrefs) if m),
            key=lambda v: tuple(int(x) for x in v.split(".")),
        )
        if not vers:
            return None  # this interpreter/platform isn't covered -> don't try
        pins.append(f"{pkg}=={vers[-1]}+{cu}")
    return url, pins


def _config_get(key, default=None):
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _nvidia_gpu_present():
    """True if an NVIDIA GPU/driver is present (so a CUDA torch build would run)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.WinDLL("nvcuda.dll")   # only loads when the NVIDIA driver is installed
            return True
        except Exception:  # noqa: BLE001
            return False
    return bool(shutil.which("nvidia-smi"))


def _torch_is_cuda():
    """True if the installed torch is a CUDA build (torch.version.cuda set)."""
    try:
        import torch
        return bool(getattr(torch.version, "cuda", None))
    except Exception:  # noqa: BLE001
        return False


def _maybe_install_cuda_torch(name):
    """If this plugin uses torch and an NVIDIA GPU is present but torch is CPU-only
    (or absent), install the CUDA torch wheels FIRST so transcription runs on the
    GPU. Without this the portable build's default CPU torch makes a 4090 idle.
    Best-effort; controlled by config 'torch_cuda_index' ("" disables)."""
    m = plugins_registry.get(name)
    pkgs = (set(m.get("packages", [])) | set(m.get("shared_packages", []))) if m else set()
    if "torch" not in pkgs:
        return
    if not _nvidia_gpu_present() or _torch_is_cuda():
        return
    idx = _config_get("torch_cuda_index", _PYTORCH_CUDA_INDEX)
    if not idx:
        return   # explicitly disabled -> keep CPU torch

    uv = _uv_exe()

    def _install_official(index):
        if uv:
            cmd = [uv, "pip", "install", "--python", sys.executable, "--index-url",
                   index, "torch", "torchaudio"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--index-url", index,
                   "torch", "torchaudio"]
        ok, _ = _run(cmd)
        return ok

    def _install_china(cu):
        """Aliyun --find-links + pinned +cuNNN versions, deps off the PyPI mirror."""
        info = _china_cuda_torch_pins(cu)
        if not info:
            return False
        find_links, pins = info
        app_logger.info("Installing CUDA torch from Aliyun mirror: %s", " ".join(pins))
        if uv:
            cmd = [uv, "pip", "install", "--python", sys.executable, "--index-url",
                   _PYPI_MIRROR, "--find-links", find_links, *pins]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--index-url", _PYPI_MIRROR,
                   "--find-links", find_links, *pins]
        ok, _ = _run(cmd)
        return ok

    cu = _cu_tag(idx)
    app_logger.info("NVIDIA GPU detected + CPU torch — installing CUDA torch from %s", idx)
    _emit("检测到 NVIDIA GPU，正在安装 GPU 版 torch（较大，请耐心）… / installing CUDA torch…")
    # If the official pytorch host is reachable, prefer it (correct deps, all
    # platforms). When it's unreachable (mainland China) or the install fails, fall
    # back to the Aliyun mirror with pinned +cuNNN wheels so the download still
    # succeeds. Only the official host has a known China mirror mapping (cu != None).
    ok = False
    if cu and not _pytorch_host_reachable(idx):
        app_logger.info("download.pytorch.org unreachable — using Aliyun CUDA torch mirror")
        _emit("官方源不可达，改用国内镜像下载 GPU torch… / using China mirror for CUDA torch…")
        ok = _install_china(cu)
    if not ok:
        ok = _install_official(idx)
    if not ok and cu:
        app_logger.warning("Official CUDA torch index failed; retrying on Aliyun mirror")
        _emit("官方源失败，切换国内镜像重试 GPU torch… / retrying CUDA torch on China mirror…")
        ok = _install_china(cu)
    if not ok:
        app_logger.warning("CUDA torch install failed; the CPU build will be used instead.")


# onnxruntime-gpu on PyPI is built for CUDA 12 through 1.26.x (1.27 switched to
# CUDA 13). We pin <1.27 so the wheel matches the CUDA-12 torch/nvidia stack, and
# install from the PyPI mirror (China-friendly) rather than the Azure cu12 feed.
_ORT_GPU_CUDA12_SPEC = "onnxruntime-gpu<1.27"
# CUDA-12 runtime libs onnxruntime's CUDA EP loads (via ort.preload_dlls()) in the
# torch-free OCR child. torch bundles its own copies, but the OCR subprocess never
# imports torch (that would reintroduce the cuDNN conflict), so these standalone
# wheels are what make GPU OCR work there.
# ⚠️ PINNED to the CUDA-12.6-era versions onnxruntime-gpu 1.26 was built against.
# Unpinned, pip grabs the LATEST (e.g. cuDNN 9.23 / cuBLAS 12.9), which onnxruntime
# 1.26 can't use -> the CUDA EP loads but inference dies with
# "CUDNN_BACKEND_API_FAILED" and silently falls back to CPU. This exact set is the
# one verified working (cudnn 9.5.1.17 + cublas 12.6.4.1).
_ORT_CUDA12_NVIDIA = ["nvidia-cudnn-cu12==9.5.1.17", "nvidia-cublas-cu12==12.6.4.1",
                      "nvidia-cuda-runtime-cu12==12.6.77", "nvidia-cufft-cu12==11.3.0.4",
                      "nvidia-curand-cu12==10.3.7.77", "nvidia-cusparse-cu12==12.5.4.2"]


def _onnxruntime_is_gpu():
    """True if the INSTALLED onnxruntime exposes the CUDA execution provider.

    Checks in a FRESH subprocess, not in-process: the running app likely already
    imported onnxruntime at startup (the CPU build), and Python caches imports, so
    an in-process check would report the STALE pre-swap state — which made the GPU
    swap's own verification think it failed and roll back to CPU. A subprocess
    imports the on-disk package as it is right now."""
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import onnxruntime,sys;"
             "sys.exit(0 if 'CUDAExecutionProvider' in onnxruntime.get_available_providers() else 1)"],
            cwd=REPO_ROOT, timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _maybe_install_cuda_onnxruntime(name):
    """If this plugin uses onnxruntime (the Image-OCR plugin) and an NVIDIA GPU is
    present but the installed onnxruntime is CPU-only, swap in the CUDA-12
    onnxruntime-gpu + nvidia CUDA-12 runtime wheels so RapidOCR's PP-OCRv6 runs on
    the GPU (~60x faster than CPU PaddleOCR, same accuracy). Runs AFTER the plugin
    reqs (which pull CPU onnxruntime) so it can replace it. Best-effort; opt out
    with config 'ocr_gpu' = false."""
    m = plugins_registry.get(name)
    pkgs = (set(m.get("packages", [])) | set(m.get("shared_packages", []))) if m else set()
    if not ({"onnxruntime", "onnxruntime-gpu", "rapidocr"} & pkgs):
        return
    if not _nvidia_gpu_present() or _onnxruntime_is_gpu():
        return
    if not _config_get("ocr_gpu", True):
        return   # explicitly disabled -> keep CPU onnxruntime
    if _wheel_platform_tag() is None:
        return   # platform the cu12 wheels don't cover

    pypi = pick_pypi_index()
    uv = _uv_exe()
    app_logger.info("NVIDIA GPU detected — installing CUDA-12 onnxruntime-gpu for GPU OCR")
    _emit("检测到 NVIDIA GPU，正在安装 GPU 版 OCR 运行时（较大，请耐心）… / installing CUDA onnxruntime for OCR…")

    def _pip(*args):
        if uv:
            return _run([uv, "pip", "install", "--python", sys.executable, *args])[0]
        return _run([sys.executable, "-m", "pip", "install", *args])[0]

    # CPU onnxruntime and onnxruntime-gpu provide the same import name and can't
    # coexist, so we must uninstall the CPU build to install the GPU one. The risk:
    # if the GPU install then fails (mirror down / no matching wheel), the env is
    # left with NO onnxruntime at all — and on a RapidOCR-only install (no paddle)
    # that kills OCR completely. So: try the swap, VERIFY the CUDA EP actually
    # loads, and if it doesn't, restore a working CPU onnxruntime. Never leave OCR
    # worse than CPU, and report the state truthfully.
    _run([sys.executable, "-m", "pip", "uninstall", "-y",
          "onnxruntime", "onnxruntime-directml"])
    ok = _pip("--index-url", pypi, _ORT_GPU_CUDA12_SPEC)
    ok = _pip("--index-url", pypi, *_ORT_CUDA12_NVIDIA) and ok
    if ok and _onnxruntime_is_gpu():
        return   # GPU OCR ready
    # Failed, or installed but the CUDA EP won't load -> restore CPU onnxruntime so
    # OCR keeps working (RapidOCR/PaddleOCR on CPU) instead of being dead.
    app_logger.warning("CUDA onnxruntime unavailable; restoring CPU onnxruntime for OCR.")
    _emit("GPU OCR 运行时不可用，已恢复 CPU onnxruntime。/ CUDA onnxruntime unavailable; restored CPU onnxruntime.")
    _run([sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime-gpu"])
    _pip("--index-url", pypi, "onnxruntime")


def install_module(name):
    req = plugins_registry.requirements_path(name)
    if not req:
        return False, f"Unknown module: {name}"
    m = plugins_registry.get(name)
    before = _freeze_names()
    _maybe_install_cuda_torch(name)   # GPU machines get CUDA torch before the rest
    ok, out = _run_install(req)
    if ok:
        # AFTER the reqs (which pull CPU onnxruntime): swap in CUDA onnxruntime for GPU OCR.
        _maybe_install_cuda_onnxruntime(name)
    if ok and m:
        _record_install_delta(m["key"], before)   # for full cleanup on uninstall
        _refresh_after_change()
    return ok, out


def packages_to_uninstall(name):
    """Packages safe to remove when uninstalling ``name``: this plugin's OWN
    packages (manifest ``packages`` minus its ``shared_packages``) MINUS any
    package still listed by ANOTHER plugin (its packages OR shared_packages), so a
    shared dependency — the STT stack used by Video/Audio + Real-Time Voice +
    翻译语音输入, and torch/torchaudio — is kept while any sibling still needs it.
    Empty list = everything is shared, remove nothing."""
    m = plugins_registry.get(name)
    if not m:
        return []
    mine = set(plugins_registry.removable_packages(name))
    others = set()
    for other in plugins_registry.all_plugins().values():
        # Only an INSTALLED sibling reserves shared packages — a sibling that's
        # merely declared (never installed) must not keep the stack alive.
        if other["name"] != name and _plugin_installed(other.get("key", "")):
            others |= set(other.get("packages", []))
            others |= set(other.get("shared_packages", []))
    return sorted(mine - others)


def _transitive_to_remove(name):
    """Packages this plugin's install ADDED that are safe to also remove on
    uninstall: this plugin's recorded freeze-delta, MINUS the delta + manifest
    packages of every OTHER plugin, MINUS anything still REQUIRED by a remaining
    distribution (live dependency-graph gate), MINUS tooling. Empty if no delta
    was recorded (older install) — then we fall back to manifest-only removal."""
    m = plugins_registry.get(name)
    if not m:
        return []
    try:
        with open(_delta_path(m["key"]), encoding="utf-8") as f:
            mine = {_norm(p) for p in json.load(f)}
    except Exception:  # noqa: BLE001 — no recorded delta -> nothing extra
        return []
    keep = {"pip", "setuptools", "wheel", "uv"}
    for other in plugins_registry.all_plugins().values():
        if other["name"] == name:
            continue
        # Only an INSTALLED sibling's packages are protected from removal.
        if not _plugin_installed(other.get("key", "")):
            continue
        keep |= {_norm(p) for p in other.get("packages", [])}
        keep |= {_norm(p) for p in other.get("shared_packages", [])}
        try:
            with open(_delta_path(other["key"]), encoding="utf-8") as f:
                keep |= {_norm(p) for p in json.load(f)}
        except Exception:  # noqa: BLE001
            pass
    candidates = mine - keep
    # Safety gate: never remove a package still required by a distribution we are
    # NOT removing (protects shared/undeclared-but-pulled deps).
    survivors = {_norm(d.metadata["Name"]) for d in importlib.metadata.distributions()
                 if d.metadata and d.metadata.get("Name")
                 and _norm(d.metadata["Name"]) not in candidates}
    required_by_survivors = set()
    for d in importlib.metadata.distributions():
        nm = _norm(d.metadata["Name"]) if d.metadata and d.metadata.get("Name") else None
        if nm not in survivors:
            continue
        for req in (d.requires or []):
            if "; extra" in req and "extra ==" in req:
                continue
            dep = _norm(re.split(r"[<>=!~;\[\(\s]", req.strip())[0])
            required_by_survivors.add(dep)
    return sorted(candidates - required_by_survivors)


def _import_names(p):
    """Plausible import roots for a distribution name (handle common aliases)."""
    n = _norm(p)
    cand = {n.replace("-", "_"), n.replace("-", "")}
    if n == "pymupdf":
        cand |= {"fitz", "pymupdf"}
    if n in ("opencv-python-headless", "opencv-python"):
        cand |= {"cv2"}
    if n == "pillow":
        cand |= {"PIL"}
    return cand


def _loaded_blockers(pkgs):
    """Candidate packages that have a LOADED NATIVE EXTENSION (.pyd/.dll/.so) in
    this process. ONLY those genuinely can't be deleted on Windows (uv/pip would
    abort mid-batch and corrupt the dist-info), so we refuse and ask for a restart.

    Pure-Python modules are safe to delete even while imported — importing them is
    exactly what probing availability does (e.g. ffmpeg_exe() imports the
    pure-Python imageio_ffmpeg), and that must NOT block an uninstall. (The old
    check blocked on ANY imported module, so merely opening the Plugins page made
    Video/Audio impossible to uninstall without a restart.)"""
    native_roots = set()
    for mod_name, mod in list(sys.modules.items()):
        f = (getattr(mod, "__file__", None) or "").lower()
        if f.endswith((".pyd", ".dll", ".so")):
            native_roots.add(mod_name.split(".")[0])
    loaded = set()
    for p in pkgs:
        if _import_names(p) & native_roots:
            loaded.add(p)
    return loaded


def _drop_still_required(pkgs):
    """Final safety gate: remove from the uninstall set any package still REQUIRED
    by a distribution that will SURVIVE (one we are NOT removing). A plugin may
    declare a package that a BASE dependency also needs — e.g. Image OCR lists
    ``Pillow`` in its manifest, but ``python-pptx`` (base) depends on Pillow too, so
    uninstalling OCR must NOT delete it or PPT translation breaks. packages_to_uninstall()
    only subtracts other PLUGINS' packages, so this graph-level check is what protects
    base/undeclared deps from the manifest-declared removals as well."""
    if not pkgs:
        return pkgs
    removing = {_norm(p) for p in pkgs}
    required_by_survivors = set()
    for d in importlib.metadata.distributions():
        nm = _norm(d.metadata["Name"]) if d.metadata and d.metadata.get("Name") else None
        if not nm or nm in removing:
            continue   # a dist we're removing doesn't get to protect its deps
        for req in (d.requires or []):
            if "; extra" in req and "extra ==" in req:
                continue   # optional extra -> not a hard runtime dep
            dep = _norm(re.split(r"[<>=!~;\[\(\s]", req.strip())[0])
            required_by_survivors.add(dep)
    return [p for p in pkgs if _norm(p) not in required_by_survivors]


def uninstall_module(name):
    m = plugins_registry.get(name)
    if not m:
        return False, f"Unknown module: {name}"
    # This plugin's own top-level packages (shared-aware) + the transitive deps its
    # install pulled in (freeze-delta, gated so siblings/base are never touched).
    pkgs = set(packages_to_uninstall(name)) | set(_transitive_to_remove(name))
    # Never remove a package a SURVIVING distribution still requires (e.g. Pillow,
    # which OCR declares but base python-pptx needs).
    pkgs = sorted(_drop_still_required(sorted(pkgs)))
    blockers = _loaded_blockers(pkgs)
    if blockers:
        # Loaded in-process -> deleting now would fail on Windows and corrupt the
        # env. Tell the user to restart, then uninstall on a fresh process.
        return False, ("This plugin is in use this session "
                       f"({', '.join(sorted(blockers)[:3])}…). Please restart the app, "
                       "then uninstall without translating first.")
    if not pkgs:
        # All of this plugin's packages are shared with another plugin -> removing
        # them would break the sibling. Nothing to uninstall at the pip level.
        return True, "All dependencies are shared with another plugin; kept."
    ok, out = _run_uninstall(pkgs)
    if ok and m:   # forget the recorded delta so a reinstall re-records fresh
        try:
            os.remove(_delta_path(m["key"]))
        except OSError:
            pass
        _refresh_after_change()
    return ok, out


def upgrade_module(name):
    """Upgrade a plugin. Prefer upgrading ONLY its tracked version_package (the one
    check_module_update watches) — NOT `-U` over the whole requirements file, which
    would also upgrade torch/paddle and could replace a user's CUDA build with the
    CPU wheel. Falls back to the full requirements only when no version_package is
    declared."""
    m = plugins_registry.get(name)
    if not m:
        return False, f"Unknown module: {name}"
    blocked = _frozen_block()
    if blocked:
        return blocked
    pkg = m.get("version_package")
    if pkg:
        uv = _uv_exe()
        idx = ["--index-url", pick_pypi_index()]
        if uv:
            return _run([uv, "pip", "install", "--upgrade", "--python", sys.executable, *idx, pkg])
        return _run([sys.executable, "-m", "pip", "install", "--upgrade", *idx, pkg])
    return _run_install(m["requirements_path"], upgrade=True)


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
    m = plugins_registry.get(name)
    pkg = m.get("version_package") if m else None
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
