"""Startup update check (GitHub Releases) with a mainland-China-friendly mirror
fallback.

The check fetches ``version.json`` from the repo's main branch via
``raw.githubusercontent.com``, trying GitHub directly first and then a list of
proxy mirrors (ghproxy etc.) that work from mainland China. It compares the
remote ``version`` to the local ``__version__`` and reports whether an update is
available — it never downloads or modifies anything by itself; the UI shows a
prompt and the user opens the download page.

To publish an update, bump ``version`` in both config/version.py and the repo's
version.json (and attach the build to the GitHub Release at ``url``).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from core.version import __version__
from core.paths import RUNTIME_ROOT

GITHUB_REPO = "YANG-Haruka/LinguaHaru"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.json"

# Try GitHub directly first, then proxies reachable from mainland China.
_PROXIES = [
    "",
    "https://ghproxy.net/",
    "https://mirror.ghproxy.com/",
    "https://gh-proxy.com/",
]


def _to_tuple(v):
    """'V5.1.2' / '5.1' -> (5, 1, 2) for ordered comparison."""
    nums = re.findall(r"\d+", str(v or ""))[:3]
    nums += ["0"] * (3 - len(nums))
    return tuple(int(n) for n in nums)


def _fetch_remote(timeout=6):
    for proxy in _PROXIES:
        url = f"{proxy}{_VERSION_URL}" if proxy else _VERSION_URL
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            if isinstance(data, dict) and data.get("version"):
                return data
        except Exception:
            continue
    return None


def check_for_update():
    """Return a dict, or None if the check could not reach any source.

    {update: bool, current: str, latest: str, url: str, notes: str}
    """
    data = _fetch_remote()
    if not data:
        return None
    latest = str(data.get("version", "")).strip()
    if not latest:
        return None
    assets = data.get("assets") or {}
    return {
        "update": _to_tuple(latest) > _to_tuple(__version__),
        "current": __version__,
        "latest": latest,
        "url": data.get("url") or RELEASES_PAGE,
        "notes": data.get("notes", ""),
        # Direct zip for THIS flavor (web/desktop), if version.json provides it and
        # this is a portable install -> enables one-click in-app self-update.
        "asset_url": assets.get(flavor()) if portable_root() else None,
    }


# --- Smart self-update (portable builds only) -------------------------------
# The portable build runs a REAL embeddable python next to the app, so we can
# update IN PLACE: download the new release zip and overlay only the SOURCE layer,
# preserving python/ (installed plugins), models/, data/, and the user's
# system_config.json. A frozen (PyInstaller) build can't self-replace its exe, so
# this is portable-only; frozen users use the "open download page" path.
_SOURCE_LAYER = ["core", "webapp", "qt_app", "config", "plugins", "assets",
                 "requirements", "version.json", "app_qt.py",
                 "README.md", "README_ZH.md", "README_JP.md", "LICENSE"]


def portable_root():
    """The portable install root if this is a portable build (embeddable python
    sits next to the app), else None."""
    if getattr(sys, "frozen", False):
        return None
    cand = os.path.join(RUNTIME_ROOT, "python",
                        "python.exe" if os.name == "nt" else "python")
    return RUNTIME_ROOT if os.path.exists(cand) else None


def flavor():
    """'desktop' if the Qt UI package is present, else 'web'."""
    return "desktop" if os.path.isdir(os.path.join(RUNTIME_ROOT, "qt_app")) else "web"


def _download(url, dst, progress_cb=None, base=0.0, span=0.7):
    req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru-Updater"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(dst, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress_cb and total:
                    progress_cb(base + span * got / total, "downloading")


def _find_inner_root(extracted):
    """The zip may wrap everything in a top-level LinguaHaru-<flavor>/ dir; locate
    the dir that actually contains version.json."""
    if os.path.exists(os.path.join(extracted, "version.json")):
        return extracted
    for name in os.listdir(extracted):
        p = os.path.join(extracted, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "version.json")):
            return p
    return extracted


def _sync_base_deps(root):
    """Re-install base deps with the bundled uv (pip fallback) so a new version
    that added a base dependency works without a full python/ replacement."""
    py = os.path.join(root, "python", "python.exe" if os.name == "nt" else "python")
    uv = os.path.join(root, "python", "uv.exe" if os.name == "nt" else "uv")
    reqs = [os.path.join(root, "requirements", "base.txt"),
            os.path.join(root, "requirements", "web.txt" if flavor() == "web" else "qt.txt")]
    args = []
    for r in reqs:
        if os.path.exists(r):
            args += ["-r", r]
    if not args:
        return
    cmd = ([uv, "pip", "install", "--python", py, *args] if os.path.exists(uv)
           else [py, "-m", "pip", "install", *args])
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception:  # noqa: BLE001 — dep sync is best-effort
        pass


def download_and_apply(asset_url, progress_cb=None):
    """Download the release zip and overlay the SOURCE layer onto the portable
    install, preserving python/ + models/ + data/ + the user's system_config.json.
    Returns (ok, message). Caller should prompt the user to restart on success."""
    root = portable_root()
    if not root:
        return False, "Smart update is only available in the portable build."
    if not asset_url:
        return False, "No downloadable package URL for this build."
    tmp = tempfile.mkdtemp(prefix="lh_update_")
    try:
        zpath = os.path.join(tmp, "update.zip")
        _download(asset_url, zpath, progress_cb, base=0.0, span=0.7)
        if progress_cb:
            progress_cb(0.72, "extracting")
        ex = os.path.join(tmp, "x")
        os.makedirs(ex)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ex)
        src_root = _find_inner_root(ex)
        if not os.path.exists(os.path.join(src_root, "version.json")):
            return False, "Downloaded package is not a valid LinguaHaru build."
        # Preserve the user's live config (config/ gets replaced with the default).
        user_cfg = os.path.join(root, "config", "system_config.json")
        backup_cfg = None
        if os.path.exists(user_cfg):
            backup_cfg = os.path.join(tmp, "system_config.json")
            shutil.copy2(user_cfg, backup_cfg)
        if progress_cb:
            progress_cb(0.78, "applying")
        for item in _SOURCE_LAYER:
            s = os.path.join(src_root, item)
            d = os.path.join(root, item)
            if not os.path.exists(s):
                continue
            if os.path.isdir(s):
                shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        if backup_cfg:   # restore the user's settings after replacing config/
            shutil.copy2(backup_cfg, user_cfg)
        if progress_cb:
            progress_cb(0.9, "syncing dependencies")
        _sync_base_deps(root)
        if progress_cb:
            progress_cb(1.0, "done")
        return True, "Update applied. Please restart the app to use the new version."
    except Exception as e:  # noqa: BLE001
        return False, f"Update failed: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
