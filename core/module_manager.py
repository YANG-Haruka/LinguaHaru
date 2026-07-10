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
    # A config toggle forces the China mirror outright (the reliable guarantee for
    # mainland users who don't want to depend on auto-detection).
    if _config_get("use_china_mirror", False):
        _pypi_index = _PYPI_MIRROR
        app_logger.info("PyPI index: Tsinghua mirror (forced by config)")
        return _pypi_index
    # Reachability isn't enough: in mainland China files.pythonhosted.org answers a
    # HEAD but then THROTTLES the actual multi-hundred-MB wheel downloads to a crawl,
    # so the old HEAD probe wrongly chose "official" and installs stalled (users
    # needed a VPN). Measure real THROUGHPUT — pull a small real wheel chunk and
    # require a usable speed; too slow / unreachable -> China mirror.
    if _pypi_cdn_fast_enough():
        _pypi_index = _PYPI_OFFICIAL
        app_logger.info("PyPI index: official")
    else:
        _pypi_index = _PYPI_MIRROR
        app_logger.info("PyPI index: Tsinghua mirror (official CDN slow/unreachable)")
    return _pypi_index


def _pypi_cdn_fast_enough(min_kbps=100):
    """True only if the PyPI wheel CDN is reachable AND fast enough for real wheel
    downloads — not just answering a HEAD. Resolves a small, always-present wheel
    (certifi) and times a ranged chunk fetch; below min_kbps (or any failure) we
    treat the official source as unusable and fall back to the China mirror. This
    is what catches the mainland 'reachable-but-throttled CDN' case."""
    import time
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/certifi/json", timeout=3) as r:
            files = json.loads(r.read().decode("utf-8", "replace")).get("urls", [])
        whl = next((f["url"] for f in files if str(f.get("url", "")).endswith(".whl")), None)
        if not whl:
            return False
        t0 = time.time()
        req = urllib.request.Request(whl, headers={"Range": "bytes=0-524287"})  # up to 512 KB
        with urllib.request.urlopen(req, timeout=6) as r:
            n = len(r.read())
        dt = max(time.time() - t0, 0.001)
        return n >= 65536 and (n / 1024 / dt) >= min_kbps
    except Exception:  # noqa: BLE001 — unreachable / blocked / too slow -> mirror
        return False


def _site_packages():
    """site-packages of the interpreter we install into (sys.executable)."""
    import sysconfig
    p = sysconfig.get_paths().get("purelib")
    return p if p and os.path.isdir(p) else None


def _repair_broken_metadata():
    """Remove *.dist-info dirs missing their METADATA file — leftovers of an
    interrupted install that make uv/pip choke ('Failed to read metadata for X',
    os error 2). The package's code may still import, but its install record is
    corrupt; dropping the dist-info lets the installer reinstall it cleanly.
    Returns the removed dist-info names."""
    import glob
    sp = _site_packages()
    if not sp:
        return []
    removed = []
    for di in glob.glob(os.path.join(sp, "*.dist-info")):
        if not os.path.exists(os.path.join(di, "METADATA")):
            try:
                shutil.rmtree(di)
                removed.append(os.path.basename(di))
            except Exception:  # noqa: BLE001
                pass
    if removed:
        app_logger.warning("Removed %d corrupt package record(s) before install: %s",
                           len(removed), ", ".join(removed))
        _emit("清理残缺的安装记录… / cleaning up corrupt package records…")
    return removed


def _freeze_constraints():
    """A constraints file pinning every currently-installed package, so a plugin
    install only ADDS packages and never upgrades/reinstalls one the RUNNING app
    has loaded — on Windows a loaded C-extension (Pillow, numpy, …) can't be
    replaced and the install dies with a locked-file error (os error 5). Returns a
    temp path (caller deletes it) or None if freezing failed."""
    import tempfile
    uv = _uv_exe()
    cmd = ([uv, "pip", "freeze", "--python", sys.executable] if uv
           else [sys.executable, "-m", "pip", "freeze"])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    # Keep only plain 'name==version' pins; drop editable / @-url / -e / blank lines
    # (not valid as constraints).
    lines = [ln.strip() for ln in out.stdout.splitlines()
             if "==" in ln and " @ " not in ln and not ln.startswith(("-e", "-", "#"))]
    if not lines:
        return None
    try:
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="lh_con_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception:  # noqa: BLE001
        return None


def _install_cmd(req, index, upgrade, constraints=None):
    uv = _uv_exe()
    up = ["--upgrade"] if upgrade else []
    idx = ["--index-url", index]
    con = ["--constraint", constraints] if constraints else []
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *idx, *up, *con, "-r", req]
    return [sys.executable, "-m", "pip", "install", *idx, *up, *con, "-r", req]


def _install_attempt(req, upgrade, constraints):
    """One install attempt (official index, then China mirror on failure)."""
    index = pick_pypi_index()
    ok, out = _run(_install_cmd(req, index, upgrade, constraints))
    if not ok and index != _PYPI_MIRROR:
        app_logger.info("Install failed on %s — retrying on China mirror", index)
        _emit("⚠ 官方源安装失败，正在切换国内镜像重试… / retrying on China mirror…")
        ok, out2 = _run(_install_cmd(req, _PYPI_MIRROR, upgrade, constraints))
        out = (out + "\n--- retry on mirror ---\n" + out2)[-4000:]
    return ok, out


def _is_constraint_conflict(out):
    s = (out or "").lower()
    return any(k in s for k in ("no solution found", "are incompatible",
                                "cannot be installed", "conflicting"))


def _is_locked_file(out):
    s = (out or "").lower()
    return any(k in s for k in ("os error 5", "拒绝访问", "access is denied",
                                "failed to remove file", "used by another process"))


def _run_install(req, upgrade=False):
    """Install from a requirements file into THIS interpreter (uv with --python, else
    pip). Auto-uses a China PyPI mirror when the official host is unreachable / fails.

    Two robustness guards (Windows portable installs into a RUNNING app):
    - First repair any half-installed package records (missing METADATA) so a prior
      interrupted install doesn't make every future resolve fail (os error 2).
    - Pin the already-installed set as constraints so we only ADD packages and never
      replace a loaded C-extension (os error 5 locked file). If that pin causes a
      real version conflict, retry without it so a legit install still goes through."""
    blocked = _frozen_block()
    if blocked:
        return blocked
    _repair_broken_metadata()
    constraints = None if upgrade else _freeze_constraints()
    try:
        ok, out = _install_attempt(req, upgrade, constraints)
        if not ok and constraints and _is_constraint_conflict(out):
            app_logger.info("Constrained install hit a version conflict — retrying unconstrained")
            ok, out = _install_attempt(req, upgrade, None)
        if not ok and _is_locked_file(out):
            out += ("\n\n⚠ 某个正在使用的组件无法更新（文件被占用）。请完全退出 "
                    "LinguaHaru 后重新安装该插件。/ A component in use couldn't be updated "
                    "— fully close LinguaHaru, then reinstall this plugin.")
        return ok, out
    finally:
        if constraints:
            try:
                os.remove(constraints)
            except Exception:  # noqa: BLE001
                pass


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
    """After an install, persist (now - before) = the dists this plugin pulled in.

    MERGED with any existing delta: a REINSTALL over an already-satisfied env
    adds nothing (now - before = {}), and overwriting would wipe the plugin's
    cleanup ownership — its transitive deps would be orphaned on uninstall.
    Stale names in a delta are harmless (pip skips not-installed packages)."""
    try:
        added = set(_freeze_names() - before)
        try:
            with open(_delta_path(key), encoding="utf-8") as f:
                added |= {str(p) for p in json.load(f)}
        except Exception:  # noqa: BLE001 — no previous delta
            pass
        with open(_delta_path(key), "w", encoding="utf-8") as f:
            json.dump(sorted(added), f, ensure_ascii=False, indent=2)
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


# GPU compute capability -> the PyTorch CUDA wheel channel that first shipped
# kernels for it. Checked high-to-low; the first threshold the GPU meets wins.
# A too-old channel installs fine but then throws "no kernel image is available
# for execution on the device" at runtime (the exact RTX-50-series failure).
#
# ⚠️ UPDATE THIS as new GPU archs / torch channels ship — see the wheel list at
# https://download.pytorch.org/whl/ and PyTorch release notes for which cuNNN
# first carries a new sm_XX:
#   sm_120 (cap 12.0)  Blackwell / RTX 50-series  -> cu128, needs CUDA 12.8 driver
#   sm_80–90 (8.0–9.0) Ampere/Ada/Hopper / RTX 30–40 -> default cu121 (proven,
#                       broadest driver compatibility)
# Each row: (min_compute_cap, min_driver_cuda_version, wheel_index).
_CUDA_CHANNELS_BY_CAP = [
    (12.0, 12.8, "https://download.pytorch.org/whl/cu128"),   # Blackwell / RTX 50xx
]


def _gpu_compute_cap():
    """Highest NVIDIA GPU compute capability as a float (12.0 = Blackwell/RTX 50xx,
    8.9 = Ada/RTX 40xx, 8.6 = Ampere/RTX 30xx), via nvidia-smi. None if unknown.
    Read from nvidia-smi (not torch) so it works BEFORE we've picked/installed the
    right torch — the whole point is to choose torch by the GPU's arch."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        caps = [float(x) for x in out.stdout.split() if x.strip()]
        return max(caps) if caps else None
    except Exception:  # noqa: BLE001 — no nvidia-smi / parse error -> unknown
        return None


def _driver_cuda_version():
    """The max CUDA version the installed NVIDIA driver supports (e.g. 12.8), from
    nvidia-smi's header. A torch cuNNN runtime must be <= this or it won't run.
    None if unknown. Handles both header formats: the classic "CUDA Version: 12.8"
    and the newer 610+ driver "CUDA UMD Version: 13.3"."""
    import re
    try:
        out = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        m = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([0-9]+\.[0-9]+)", out.stdout)
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _pick_cuda_index():
    """The best PyTorch CUDA wheel index for THIS machine's GPU. Newer archs need a
    newer cuNNN channel (Blackwell/sm_120 needs cu128); older cards keep the
    broadly-driver-compatible default. Also honours the DRIVER's CUDA ceiling — a
    channel the driver can't run is skipped (with a "please update the driver"
    warning) so we don't download a 2.5GB torch that then can't execute. Config
    'torch_cuda_index' overrides entirely — a URL to pin, or "" to force CPU."""
    cfg = _config_get("torch_cuda_index", "__auto__")
    if cfg != "__auto__":
        return cfg
    cap = _gpu_compute_cap()
    drv = _driver_cuda_version()
    if cap is not None:
        for min_cap, min_cuda, index in _CUDA_CHANNELS_BY_CAP:
            if cap < min_cap:
                continue
            if drv is not None and drv < min_cuda:
                app_logger.warning(
                    "GPU compute_cap %.1f needs CUDA %.1f torch, but the driver only "
                    "supports CUDA %.1f — update the NVIDIA driver for GPU acceleration; "
                    "STT will run on CPU until then.", cap, min_cuda, drv)
                break   # driver too old for this arch's channel -> CPU fallback at runtime
            app_logger.info("GPU compute_cap %.1f + driver CUDA %s -> %s", cap, drv, index)
            return index
    return _PYTORCH_CUDA_INDEX


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
    """True if the installed torch is a CUDA build (torch.version.cuda set).

    Checked in a FRESH subprocess, not in-process: (a) the running app may have a
    stale import view of a just-swapped torch, and (b) importing torch HERE would
    load its native DLLs into this process — after which _loaded_blockers refuses
    any uninstall of torch ('in use this session') merely because installing some
    other plugin probed it."""
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import torch,sys;sys.exit(0 if getattr(torch.version,'cuda',None) else 1)"],
            cwd=REPO_ROOT, timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        return r.returncode == 0
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
    idx = _pick_cuda_index()   # GPU-arch + driver aware (Blackwell -> cu128, …)
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


# The cv2 import machinery allows exactly ONE OpenCV distribution: every variant
# (opencv-python / -headless / -contrib) ships the SAME cv2/ directory, so
# co-installed variants overwrite each other file-by-file in extraction order.
# The OCR stack pulls THREE at once (paddleocr -> opencv-python 4.x, paddlex ->
# opencv-contrib-python pinned OLDER, our reqs -> opencv-python-headless): a 4.10
# loader mixed with 4.13 binaries dies at import with "partially initialized
# module 'cv2' has no attribute 'gapi_wip_gst_GStreamerPipeline'" — and the first
# failure poisons the whole process (broken module stays in sys.modules), killing
# every later manga/OCR translation until restart. Order-dependent, so it breaks
# only on SOME machines.
_OPENCV_VARIANTS = ["opencv-python", "opencv-python-headless",
                    "opencv-contrib-python", "opencv-contrib-python-headless"]


def _expand_opencv_family(required):
    """OpenCV variants are interchangeable providers of the same cv2 package —
    after _normalize_opencv exactly ONE is installed, possibly under a DIFFERENT
    name than a survivor's metadata requires (babeldoc requires
    opencv-python-headless while the kept dist is opencv-contrib-python). So if
    any surviving distribution requires ANY variant, protect ALL of them, else
    uninstalling the OCR plugin would strip the cv2 that PDF still needs."""
    fam = {_norm(v) for v in _OPENCV_VARIANTS}
    if required & fam:
        required |= fam
    return required


def _normalize_opencv():
    """If more than one OpenCV variant is installed, uninstall them ALL and
    force-reinstall a single one so every cv2/ file comes from ONE wheel.
    Keep contrib when present (superset API, and paddlex pins it strictly),
    else the newest variant. Safe no-op when 0-1 variants are installed."""
    have = {v: _installed_version(v) for v in _OPENCV_VARIANTS}
    have = {v: ver for v, ver in have.items() if ver}
    if len(have) <= 1:
        return
    if "opencv-contrib-python" in have:
        keep, ver = "opencv-contrib-python", have["opencv-contrib-python"]
    else:
        keep, ver = max(have.items(), key=lambda kv: _version_tuple(kv[1]))
    app_logger.info(f"Multiple OpenCV variants {sorted(have)} — keeping {keep}=={ver}")
    _emit("检测到多个 OpenCV 变体，正在归一化(防 cv2 损坏)… / normalizing OpenCV variants…")
    _run([sys.executable, "-m", "pip", "uninstall", "-y", *sorted(have)])
    uv = _uv_exe()
    idx = pick_pypi_index()
    spec = f"{keep}=={ver}"
    if uv:
        ok, _out = _run([uv, "pip", "install", "--python", sys.executable,
                         "--reinstall", "--index-url", idx, spec])
    else:
        ok, _out = _run([sys.executable, "-m", "pip", "install", "--force-reinstall",
                         "--index-url", idx, spec])
    if not ok:   # never leave the env with NO cv2 — retry on the mirror
        _run([sys.executable, "-m", "pip", "install", "--force-reinstall",
              "--index-url", _PYPI_MIRROR, spec])


def install_module(name):
    req = plugins_registry.requirements_path(name)
    if not req:
        return False, f"Unknown module: {name}"
    # Frozen guard FIRST: _maybe_install_cuda_torch runs pip via sys.executable,
    # which in a PyInstaller build is the app exe itself — it would relaunch the
    # app instead of installing. _run_install has its own guard, but the CUDA
    # pre-step must never run frozen either.
    blocked = _frozen_block()
    if blocked:
        return blocked
    m = plugins_registry.get(name)
    before = _freeze_names()
    _maybe_install_cuda_torch(name)   # GPU machines get CUDA torch before the rest
    ok, out = _run_install(req)
    if ok:
        # A single consistent cv2 BEFORE anything imports it (see _normalize_opencv).
        _normalize_opencv()
        # AFTER the reqs (which pull CPU onnxruntime): swap in CUDA onnxruntime for GPU OCR.
        _maybe_install_cuda_onnxruntime(name)
    if ok and m:
        _record_install_delta(m["key"], before)   # for full cleanup on uninstall
        _refresh_after_change()
    return ok, out


def packages_to_uninstall(name):
    """Packages safe to remove when uninstalling ``name``: this plugin's manifest
    packages INCLUDING its ``shared_packages`` MINUS any package still listed by
    another INSTALLED plugin (its packages OR shared_packages). So the shared STT
    stack (torch/torchaudio + engines used by Video/Audio + Real-Time Voice +
    翻译语音输入) is kept while any installed sibling still needs it — but when the
    LAST user of a shared package is uninstalled, the package IS removed (otherwise
    the ~2.5GB CUDA torch would be orphaned forever after all voice plugins are
    gone). Empty list = everything is still shared, remove nothing."""
    m = plugins_registry.get(name)
    if not m:
        return []
    mine = set(m.get("packages", [])) | set(m.get("shared_packages", []))
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
    required_by_survivors = _expand_opencv_family(required_by_survivors)
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
    required_by_survivors = _expand_opencv_family(required_by_survivors)
    return [p for p in pkgs if _norm(p) not in required_by_survivors]


def _rehome_leftover_delta(name, removed):
    """After uninstalling ``name``, any package its install pulled in that was KEPT
    (protected by an installed sibling) would become an untracked orphan once this
    plugin's delta file is deleted — no later uninstall could ever remove it. Merge
    those leftover names into every INSTALLED sibling's delta, so when the LAST
    plugin using the shared stack is uninstalled, it can reclaim the whole stack
    (e.g. the ~2.5GB CUDA torch + its transitive deps)."""
    m = plugins_registry.get(name)
    if not m:
        return
    try:
        with open(_delta_path(m["key"]), encoding="utf-8") as f:
            mine = {_norm(p) for p in json.load(f)}
    except Exception:  # noqa: BLE001 — no delta recorded -> nothing to re-home
        return
    leftover = mine - {_norm(p) for p in removed}
    if not leftover:
        return
    for other in plugins_registry.all_plugins().values():
        if other["name"] == name or not _plugin_installed(other.get("key", "")):
            continue
        path = _delta_path(other["key"])
        try:
            with open(path, encoding="utf-8") as f:
                cur = {_norm(p) for p in json.load(f)}
        except Exception:  # noqa: BLE001
            cur = set()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(cur | leftover), f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Could not re-home delta into {other['name']}: {e}")


def _forget_plugin(name, removed):
    """Post-uninstall bookkeeping: re-home kept transitive deps to surviving
    siblings, delete this plugin's delta (it no longer counts as installed), and
    refresh the in-process view."""
    m = plugins_registry.get(name)
    if not m:
        return
    _rehome_leftover_delta(name, removed)
    try:
        os.remove(_delta_path(m["key"]))
    except OSError:
        pass
    _refresh_after_change()


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
        # All of this plugin's packages are shared with an installed sibling ->
        # removing them would break the sibling. Nothing to uninstall at the pip
        # level, but the plugin itself must STOP counting as installed (delete its
        # delta), else it would protect the shared stack forever.
        _forget_plugin(name, removed=[])
        return True, "All dependencies are shared with another plugin; kept."
    ok, out = _run_uninstall(pkgs)
    if ok:
        _forget_plugin(name, removed=pkgs)
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
