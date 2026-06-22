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
# job runs, so the UI can show "what pip/uv is doing now" instead of a dead
# spinner. Jobs run serially (single worker), so a module global is safe.
_progress_cb = None


def set_progress_callback(cb):
    """Set (or clear with None) the per-line progress sink for the next job."""
    global _progress_cb
    _progress_cb = cb


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
    try:
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
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


def install_module(name):
    req = plugins_registry.requirements_path(name)
    if not req:
        return False, f"Unknown module: {name}"
    m = plugins_registry.get(name)
    before = _freeze_names()
    ok, out = _run_install(req)
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
        if other["name"] != name:
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


def _loaded_blockers(pkgs):
    """Candidate packages whose import is ALREADY loaded in this process. On
    Windows a loaded native extension (.pyd/.dll) can't be deleted, and uv/pip
    abort mid-batch leaving a corrupted dist-info. So if a plugin was used this
    session (its modules imported), refuse uninstall and ask for a restart instead
    of corrupting the env."""
    loaded = set()
    mods = set(sys.modules)
    for p in pkgs:
        cand = {_norm(p).replace("-", "_"), _norm(p).replace("-", "")}
        # common import-name aliases
        if _norm(p) == "pymupdf":
            cand |= {"fitz", "pymupdf"}
        if _norm(p) == "opencv-python-headless" or _norm(p) == "opencv-python":
            cand |= {"cv2"}
        if _norm(p) == "pillow":
            cand |= {"PIL"}
        if cand & mods:
            loaded.add(p)
    return loaded


def uninstall_module(name):
    m = plugins_registry.get(name)
    if not m:
        return False, f"Unknown module: {name}"
    # This plugin's own top-level packages (shared-aware) + the transitive deps its
    # install pulled in (freeze-delta, gated so siblings/base are never touched).
    pkgs = set(packages_to_uninstall(name)) | set(_transitive_to_remove(name))
    pkgs = sorted(pkgs)
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
