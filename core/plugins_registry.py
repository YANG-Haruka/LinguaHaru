"""Single source of truth for the optional-plugin catalog.

Each plugin is a self-contained folder under ``plugins/<key>/`` with:
  - ``plugin.json``    — manifest (name, key, packages, shared_packages, …)
  - ``requirements.txt`` — its pip dependencies (may ``-r ../<other>/requirements.txt``
                           to reuse a shared stack, e.g. live/speechio reuse video)

``shared_packages`` (e.g. torch/torchaudio) are libraries used by MORE than one
plugin; an uninstall keeps them while another INSTALLED plugin lists them, and
removes them once their last user is uninstalled (module_manager.packages_to_uninstall).

This replaces the old hard-coded MODULE_SPECS / OPTIONAL_REQUIREMENTS dicts so a
plugin can be added/changed by dropping a folder in plugins/ — no code edits.
"""
import os
import io
import re
import sys
import json
import zipfile
import urllib.request

from core.paths import PLUGINS_DIR, DATA_DIR
from core.log_config import app_logger

_cache = None

# A plugin key must be a safe slug (used as a folder name + download key).
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}$")

# Built-in plugins ship under PLUGINS_DIR (read-only bundle). Plugins DOWNLOADED
# from the remote market land in this writable dir so they survive app updates
# (the smart updater preserves data/).
USER_PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")

# Remote plugin index (a JSON you host on GitHub). Lets you publish NEW
# self-contained plugins that users install without updating the main app.
GITHUB_REPO = "YANG-Haruka/LinguaHaru"
_INDEX_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/plugins-index.json"
_PROXIES = ["", "https://ghproxy.net/", "https://mirror.ghproxy.com/", "https://gh-proxy.com/"]


def _load_dir(base, source):
    """Load every <base>/<key>/plugin.json, tagging each with its source."""
    out = {}
    try:
        keys = sorted(os.listdir(base))
    except OSError:
        return out
    for entry in keys:
        manifest = os.path.join(base, entry, "plugin.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Bad plugin manifest {manifest}: {e}")
            continue
        # Validate the manifest: must be a dict with a name + a safe slug key, and
        # (for downloaded plugins) the key MUST match the folder it lives in — so a
        # crafted manifest can't impersonate another plugin or carry a bad key.
        if not isinstance(m, dict) or not isinstance(m.get("name"), str) \
                or not _KEY_RE.match(str(m.get("key") or "")):
            app_logger.warning(f"Invalid plugin manifest (name/key): {manifest}")
            continue
        if source == "downloaded" and m["key"] != entry:
            app_logger.warning(f"Plugin key '{m['key']}' != folder '{entry}'; skipping")
            continue
        m["dir"] = os.path.join(base, entry)
        m["source"] = source
        req = m.get("requirements", "requirements.txt")
        m["requirements_path"] = os.path.join(base, entry, req)
        out[m["name"]] = m
    return out


def _load():
    # Built-in first, then downloaded (downloaded can't shadow a built-in name).
    plugins = _load_dir(PLUGINS_DIR, "builtin")
    for name, m in _load_dir(USER_PLUGINS_DIR, "downloaded").items():
        plugins.setdefault(name, m)
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


def _invalidate():
    global _cache
    _cache = None


# --- Remote plugin market ---------------------------------------------------
def fetch_remote_index(timeout=6):
    """Fetch the published plugin index (list of available downloadable plugins).
    Returns [] on any failure. Each entry: {key,name,detail,version,url}."""
    for proxy in _PROXIES:
        url = f"{proxy}{_INDEX_URL}" if proxy else _INDEX_URL
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            items = data.get("plugins", []) if isinstance(data, dict) else []
            return [p for p in items if isinstance(p, dict) and p.get("key") and p.get("url")]
        except Exception:  # noqa: BLE001 — offline / not published yet
            continue
    return []


def remote_available():
    """Remote plugins NOT already present locally (downloadable). Keyed by key."""
    have = {m["key"] for m in all_plugins().values()}
    return [p for p in fetch_remote_index() if p["key"] not in have]


# Zip-bomb guards for downloaded plugin packages (a plugin is manifest + small
# code + requirements — never huge).
_MAX_MEMBERS = 2000
_MAX_FILE_BYTES = 50 * 1024 * 1024        # 50 MB per file
_MAX_TOTAL_BYTES = 200 * 1024 * 1024      # 200 MB uncompressed total


def _safe_extract(z, ex):
    """Extract a zip rejecting path-traversal (zip slip), absolute paths, symlink
    entries, and zip bombs — the zip is downloaded code, so it must neither escape
    `ex` nor blow up disk."""
    base = os.path.realpath(ex)
    infos = z.infolist()
    if len(infos) > _MAX_MEMBERS:
        raise ValueError(f"too many entries ({len(infos)})")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise ValueError(f"unsafe zip entry: {name}")
        target = os.path.realpath(os.path.join(ex, name))
        if target != base and not target.startswith(base + os.sep):
            raise ValueError(f"zip slip: {name}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
            raise ValueError(f"symlink in zip: {name}")
        if info.file_size > _MAX_FILE_BYTES:
            raise ValueError(f"zip entry too large: {name}")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("zip uncompressed size exceeds limit")
    z.extractall(ex)


def download_remote_plugin(key, url=None):
    """Download a self-contained plugin into USER_PLUGINS_DIR/<key>/ and register
    it. SECURITY: `key` must be a safe slug, and the download URL is taken from the
    TRUSTED server-side market index (never from the caller) — so a request can
    only ever install a plugin you actually published. Returns (ok, message)."""
    import shutil
    import tempfile
    if not _KEY_RE.match(key or ""):
        return False, "Invalid plugin key."
    # URL ALWAYS comes from the trusted index, not the caller (prevents RCE via an
    # arbitrary URL). Verify the key is published and use the index's https URL.
    entry = next((p for p in fetch_remote_index() if p.get("key") == key), None)
    if not entry:
        return False, f"Plugin '{key}' is not in the market index."
    url = entry.get("url", "")
    if not url.lower().startswith("https://"):
        return False, "Plugin URL must be https."
    # A plugin install runs downloaded CODE, so REQUIRE an integrity checksum in
    # the trusted index (mirrors the self-updater). Without it we refuse rather
    # than execute unverified code — even a small ASCII sha256 raises the bar.
    expected_sha = str(entry.get("sha256", "")).strip().lower()
    if not expected_sha:
        return False, f"Plugin '{key}' has no published checksum; refusing to install."
    dest = os.path.join(USER_PLUGINS_DIR, key)
    # Containment: dest must stay inside USER_PLUGINS_DIR (defense in depth on top
    # of the key regex).
    if os.path.realpath(dest) != os.path.realpath(os.path.join(USER_PLUGINS_DIR, key)) or \
            not os.path.realpath(dest).startswith(os.path.realpath(USER_PLUGINS_DIR) + os.sep):
        return False, "Invalid plugin path."
    tmp = tempfile.mkdtemp(prefix="lh_plugin_")
    try:
        last = None
        blob = None
        for proxy in _PROXIES:
            try:
                u = f"{proxy}{url}" if proxy else url
                req = urllib.request.Request(u, headers={"User-Agent": "LinguaHaru"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    blob = r.read()
                break
            except Exception as e:  # noqa: BLE001
                last = e
                blob = None
        if blob is None:
            return False, f"Download failed: {last}"
        import hashlib
        got_sha = hashlib.sha256(blob).hexdigest()
        if got_sha != expected_sha:
            return False, (f"Plugin checksum mismatch "
                           f"(expected {expected_sha[:12]}…, got {got_sha[:12]}…).")
        ex = os.path.join(tmp, "x")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            _safe_extract(z, ex)
        # locate plugin.json (root or single wrapper dir)
        root = ex
        if not os.path.exists(os.path.join(root, "plugin.json")):
            subs = [d for d in os.listdir(ex) if os.path.isdir(os.path.join(ex, d))]
            for d in subs:
                if os.path.exists(os.path.join(ex, d, "plugin.json")):
                    root = os.path.join(ex, d)
                    break
        if not os.path.exists(os.path.join(root, "plugin.json")):
            return False, "Invalid plugin package (no plugin.json)."
        os.makedirs(USER_PLUGINS_DIR, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(root, dest)
        _invalidate()
        return True, f"Downloaded plugin '{key}'."
    except Exception as e:  # noqa: BLE001
        return False, f"Download failed: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _PluginAPI:
    """Minimal API passed to a downloaded plugin's entry register() so its code can
    extend the app (e.g. add a translator for a new file extension)."""
    def register_translator(self, ext, dotted_class):
        from core import backend
        backend.TRANSLATOR_MODULES[ext.lower()] = dotted_class


def activate_downloaded_plugins():
    """Import each DOWNLOADED plugin's `entry` module and call its register(api),
    so plugin code actually hooks into the app. Best-effort: a plugin whose deps
    aren't installed yet (needs restart) is skipped. Built-in plugins are
    integrated in core/ and have no entry. Call once at startup."""
    api = _PluginAPI()
    for m in all_plugins().values():
        entry = m.get("entry")
        if not entry or m.get("source") != "downloaded":
            continue
        try:
            if m["dir"] not in sys.path:
                sys.path.insert(0, m["dir"])
            mod_name, _, fn_name = entry.partition(":")
            import importlib
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name or "register", None)
            if callable(fn):
                fn(api)
                app_logger.info(f"Activated downloaded plugin: {m['name']}")
        except Exception as e:  # noqa: BLE001 — deps missing / needs restart
            app_logger.warning(f"Plugin '{m.get('name')}' entry not activated: {e}")
