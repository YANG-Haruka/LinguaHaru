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
    req = plugins_registry.requirements_path(name)
    if not req:
        return False, f"Unknown module: {name}"
    return _run_pip(["install", "-r", req])


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


def uninstall_module(name):
    if not plugins_registry.get(name):
        return False, f"Unknown module: {name}"
    pkgs = packages_to_uninstall(name)
    if not pkgs:
        # All of this plugin's packages are shared with another plugin -> removing
        # them would break the sibling. Nothing to uninstall at the pip level.
        return True, "All dependencies are shared with another plugin; kept."
    return _run_pip(["uninstall", "-y", *pkgs])


def upgrade_module(name):
    """Upgrade a module's packages to the latest from its requirements file."""
    req = plugins_registry.requirements_path(name)
    if not req:
        return False, f"Unknown module: {name}"
    return _run_pip(["install", "-U", "-r", req])


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
