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
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",   # flaky/often dead — keep last
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
    a = assets.get(flavor())
    # An asset may be a bare URL string (no integrity) or {url, sha256}. Self-update
    # requires a sha256 — without a checksum we will NOT auto-apply downloaded code
    # (the user can still use the manual download link).
    asset_url = asset_sha = None
    asset_urls = None
    if portable_root() and isinstance(a, dict) and a.get("url") and a.get("sha256"):
        asset_url, asset_sha = a["url"], a["sha256"]
        # Optional explicit mirror list (e.g. a mainland-China OSS/CDN first, then
        # GitHub) — all must point at the SAME zip (verified by the one sha256).
        if isinstance(a.get("urls"), list):
            asset_urls = [u for u in a["urls"] if isinstance(u, str) and u]
    return {
        "update": _to_tuple(latest) > _to_tuple(__version__),
        "current": __version__,
        "latest": latest,
        "url": data.get("url") or RELEASES_PAGE,
        "notes": data.get("notes", ""),
        "asset_url": asset_url,
        "asset_sha256": asset_sha,
        "asset_urls": asset_urls,
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
    """Re-install base deps with the bundled uv (pip fallback). Returns True only
    if the install actually succeeded (checked via returncode) — a new version that
    added a base dependency must not be reported as a success if deps didn't land.

    Uses the same China-aware index selection as plugin installs (official PyPI
    when its wheel host is reachable, Tsinghua mirror otherwise, with a mirror
    retry on failure) — otherwise a mainland user's UPDATE would stall on the
    dependency sync and roll back a perfectly good update."""
    exe = "uv.exe" if os.name == "nt" else "uv"
    py = os.path.join(root, "python", "python.exe" if os.name == "nt" else "python")
    # The portable bundles uv under python/Scripts/ (pip-installed); accept the
    # bare python/ location too for manual installs.
    uv = next((c for c in (os.path.join(root, "python", "Scripts", exe),
                           os.path.join(root, "python", exe)) if os.path.exists(c)),
              None)
    reqs = [os.path.join(root, "requirements", "base.txt"),
            os.path.join(root, "requirements", "web.txt" if flavor() == "web" else "qt.txt")]
    args = []
    for r in reqs:
        if os.path.exists(r):
            args += ["-r", r]
    if not args:
        return True   # nothing to sync

    from core.module_manager import pick_pypi_index, _PYPI_MIRROR

    def _run_sync(index):
        cmd = ([uv, "pip", "install", "--python", py, "--index-url", index, *args]
               if uv else [py, "-m", "pip", "install", "--index-url", index, *args])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    index = pick_pypi_index()
    ok = _run_sync(index)
    if not ok and index != _PYPI_MIRROR:
        ok = _run_sync(_PYPI_MIRROR)
    return ok


def _safe_extract(zpath, ex):
    """Extract a zip rejecting zip-slip / absolute paths / symlinks (it's
    downloaded code that overwrites program files)."""
    base = os.path.realpath(ex)
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise ValueError(f"unsafe zip entry: {name}")
            target = os.path.realpath(os.path.join(ex, name))
            if target != base and not target.startswith(base + os.sep):
                raise ValueError(f"zip slip: {name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symlink in zip: {name}")
        z.extractall(ex)


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _asset_candidates(asset_url, asset_urls):
    """Ordered, de-duplicated list of URLs to try for the release zip: any
    explicit mirrors first (manifest ``urls`` — put a China OSS/CDN there), then
    the direct URL, then the same GitHub URL via the China-friendly proxies (so a
    GitHub Release download has the SAME fallback the manifest check already has).
    Every candidate is the SAME file, verified by the one published sha256."""
    cands = list(asset_urls or [])
    if asset_url:
        cands.append(asset_url)
        if "github.com" in asset_url or "githubusercontent.com" in asset_url:
            for p in _PROXIES:
                if p:
                    cands.append(p + asset_url)
    seen, out = set(), []
    for u in cands:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# Config files the user OWNS — preserved across an update (the rest of config/,
# e.g. prompts/locales/default template, is replaced with the new version's).
_PRESERVE_IN_CONFIG = ["api_config", "system_config.json", "text_rules.json"]


def download_and_apply(asset_url, sha256=None, progress_cb=None, asset_urls=None):
    """Download the release zip and overlay the SOURCE layer onto the portable
    install — verified, transactional, and preserving user data. Preserves python/
    (installed plugins), models/, data/, AND the user's config (api_config/,
    system_config.json, text_rules.json). Verifies a SHA-256, applies via a
    backup-and-rollback so a mid-way failure can't leave a half-updated install,
    and fails (with rollback) if the post-update dependency sync fails.

    Tries multiple download URLs (explicit mirrors + GitHub via China proxies),
    each verified against the same sha256. Returns (ok, message)."""
    root = portable_root()
    if not root:
        return False, "Smart update is only available in the portable build."
    if not asset_url and not asset_urls:
        return False, "No downloadable package URL for this build."
    if not sha256:
        # Refuse to apply downloaded code without an integrity checksum.
        return False, "No checksum published for this update; use the download page."
    tmp = tempfile.mkdtemp(prefix="lh_update_")
    backup = os.path.join(tmp, "backup")
    os.makedirs(backup)
    applied = []   # (item, had_backup) for rollback
    try:
        zpath = os.path.join(tmp, "update.zip")
        # Try each candidate (mirrors first, then GitHub direct + via proxies)
        # until one downloads AND matches the published checksum.
        candidates = _asset_candidates(asset_url, asset_urls)
        ok_dl, last_err = False, "no URL"
        for u in candidates:
            try:
                _download(u, zpath, progress_cb, base=0.0, span=0.6)
                if _sha256(zpath).lower() == str(sha256).lower():
                    ok_dl = True
                    break
                last_err = "checksum mismatch"
            except Exception as e:  # noqa: BLE001 — try the next mirror
                last_err = f"{type(e).__name__}: {e}"
        if not ok_dl:
            return False, f"Could not download a valid update package ({last_err})."
        if progress_cb:
            progress_cb(0.66, "extracting")
        ex = os.path.join(tmp, "x")
        os.makedirs(ex)
        _safe_extract(zpath, ex)   # zip-slip safe
        src_root = _find_inner_root(ex)
        if not os.path.exists(os.path.join(src_root, "version.json")):
            return False, "Downloaded package is not a valid LinguaHaru build."

        # Stage the NEW config with the user's owned files merged in, so replacing
        # config/ never loses custom interfaces / settings / rules.
        new_cfg = os.path.join(src_root, "config")
        if os.path.isdir(new_cfg):
            for keep in _PRESERVE_IN_CONFIG:
                old = os.path.join(root, "config", keep)
                dst = os.path.join(new_cfg, keep)
                if os.path.exists(old):
                    if os.path.isdir(old):
                        shutil.rmtree(dst, ignore_errors=True)
                        shutil.copytree(old, dst)
                    else:
                        shutil.copy2(old, dst)

        if progress_cb:
            progress_cb(0.7, "applying")
        # Transactional apply: back up each item, then replace; rollback on error.
        for item in _SOURCE_LAYER:
            s = os.path.join(src_root, item)
            d = os.path.join(root, item)
            if not os.path.exists(s):
                continue
            had = os.path.exists(d)
            if had:
                bdst = os.path.join(backup, item)
                os.makedirs(os.path.dirname(bdst), exist_ok=True)
                shutil.move(d, bdst)
            applied.append((item, had))
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)

        if progress_cb:
            progress_cb(0.85, "syncing dependencies")
        if not _sync_base_deps(root):
            raise RuntimeError("dependency sync failed")
        if progress_cb:
            progress_cb(1.0, "done")
        return True, "Update applied. Please restart the app to use the new version."
    except Exception as e:  # noqa: BLE001 — roll back to the pre-update state
        for item, had in reversed(applied):
            d = os.path.join(root, item)
            try:
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
                elif os.path.exists(d):
                    os.remove(d)
                if had:
                    shutil.move(os.path.join(backup, item), d)
            except Exception:  # noqa: BLE001
                pass
        return False, f"Update failed (rolled back): {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
