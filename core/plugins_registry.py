"""Single source of truth for the optional-plugin catalog.

Each plugin is a self-contained folder under ``plugins/<key>/`` with:
  - ``plugin.json``    — manifest (name, key, packages, shared_packages, …)
  - ``requirements.txt`` — its pip dependencies (may ``-r ../<other>/requirements.txt``
                           to reuse a shared stack, e.g. live/speechio reuse video)

``shared_packages`` (e.g. torch/torchaudio) are libraries used by MORE than one
plugin; they are listed for documentation and are never auto-removed on uninstall.

This replaces the old hard-coded MODULE_SPECS / OPTIONAL_REQUIREMENTS dicts so a
plugin can be added/changed by dropping a folder in plugins/ — no code edits.
"""
import os
import json

from core.paths import PLUGINS_DIR
from core.log_config import app_logger

_cache = None


def _load():
    plugins = {}
    try:
        keys = sorted(os.listdir(PLUGINS_DIR))
    except OSError:
        return plugins
    for entry in keys:
        manifest = os.path.join(PLUGINS_DIR, entry, "plugin.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Bad plugin manifest {manifest}: {e}")
            continue
        m["dir"] = os.path.join(PLUGINS_DIR, entry)
        req = m.get("requirements", "requirements.txt")
        m["requirements_path"] = os.path.join(PLUGINS_DIR, entry, req)
        plugins[m["name"]] = m
    return plugins


def all_plugins():
    """{name: manifest} for every plugin, cached. Manifest has dir/requirements_path
    resolved to absolute paths plus the raw JSON fields."""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def get(name):
    return all_plugins().get(name)


def ordered_names():
    """Plugin names sorted by their manifest ``order`` (then name)."""
    return [m["name"] for m in sorted(all_plugins().values(),
                                      key=lambda m: (m.get("order", 99), m["name"]))]


def requirements_path(name):
    m = get(name)
    return m["requirements_path"] if m else None


def install_hint(name):
    """A human-facing 'pip install -r plugins/<key>/requirements.txt' string."""
    m = get(name)
    if not m:
        return None
    return f"pip install -r plugins/{m['key']}/requirements.txt"


def removable_packages(name):
    """This plugin's OWN packages (safe to uninstall), excluding shared_packages."""
    m = get(name)
    if not m:
        return []
    shared = set(m.get("shared_packages", []))
    return [p for p in m.get("packages", []) if p not in shared]
