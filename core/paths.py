"""Absolute filesystem anchors for the project.

Everything under config/ and assets/ is resolved relative to the repository
root (the parent of this ``core/`` package) instead of the current working
directory, so the app works no matter where it's launched from — e.g. ``uvicorn``
started from another cwd, or a packaged build. Mutable runtime dirs
(temp/result/log) stay configurable via system_config and are handled in
``core.backend``; this module is for the static, code-relative locations.
"""

import os
import sys

_FROZEN = getattr(sys, "frozen", False)

# In a PyInstaller build, bundled resources are unpacked to a read-only, EPHEMERAL
# temp dir (sys._MEIPASS) that is deleted on exit. So we split two roots:
#   BUNDLE_ROOT  — read-only shipped resources (config templates, assets, static)
#   RUNTIME_ROOT — persistent, writable, next to the executable (models, data,
#                  user-edited system_config) so downloads/settings survive restarts.
# When running from source both are just the repository root.
if _FROZEN:
    BUNDLE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    RUNTIME_ROOT = os.path.dirname(sys.executable)
else:
    BUNDLE_ROOT = RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Back-compat alias (was the repo root); used for bundled, read-only reads.
REPO_ROOT = BUNDLE_ROOT

CONFIG_DIR = os.path.join(BUNDLE_ROOT, "config")          # bundled prompts/locales/api_config
PROMPTS_DIR = os.path.join(CONFIG_DIR, "prompts")
API_CONFIG_DIR = os.path.join(CONFIG_DIR, "api_config")
LOCALES_DIR = os.path.join(CONFIG_DIR, "locales")

ASSETS_DIR = os.path.join(BUNDLE_ROOT, "assets")
PLUGINS_DIR = os.path.join(BUNDLE_ROOT, "plugins")        # per-plugin manifests + requirements
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")             # WRITABLE — models/temp/result/log

# system_config.json is WRITTEN at runtime (model choices, settings, theme), so
# the repo only tracks a conservative template (system_config.default.json); the
# live system_config.json is gitignored and seeded from the template on first
# run — both from source and frozen — so local/test state never leaks into the
# repo or the release seed.
_CONFIG_TEMPLATE = os.path.join(CONFIG_DIR, "system_config.default.json")


def _seed_file(src, dst):
    if src and dst and os.path.exists(src) and not os.path.exists(dst):
        import shutil
        shutil.copyfile(src, dst)


if _FROZEN:
    _WRITABLE_CONFIG_DIR = os.path.join(RUNTIME_ROOT, "config")
    SYSTEM_CONFIG = os.path.join(_WRITABLE_CONFIG_DIR, "system_config.json")
    try:
        os.makedirs(_WRITABLE_CONFIG_DIR, exist_ok=True)
        _seed_file(_CONFIG_TEMPLATE, SYSTEM_CONFIG)
    except Exception:  # noqa: BLE001 — fall back to bundled template if seeding fails
        SYSTEM_CONFIG = _CONFIG_TEMPLATE
    # api_config is WRITTEN at runtime (interfaces, API keys), so it must live in
    # the writable config dir too — seeded from the bundled templates on 1st run.
    _writable_ac = os.path.join(_WRITABLE_CONFIG_DIR, "api_config")
    try:
        os.makedirs(_writable_ac, exist_ok=True)
        _seed_ac = os.path.join(CONFIG_DIR, "api_config")
        if os.path.isdir(_seed_ac):
            import shutil
            for _f in os.listdir(_seed_ac):
                if _f.endswith(".json"):
                    _dst = os.path.join(_writable_ac, _f)
                    if not os.path.exists(_dst):
                        shutil.copyfile(os.path.join(_seed_ac, _f), _dst)
        API_CONFIG_DIR = _writable_ac
    except Exception:  # noqa: BLE001 — fall back to bundled (read-only) path
        API_CONFIG_DIR = os.path.join(CONFIG_DIR, "api_config")
    # Example glossaries ship in the (read-only) bundle but the app reads from
    # the writable top-level glossary/ — seed them on first run so new users see
    # Default.csv instead of an empty list.
    try:
        _seed_gl = os.path.join(BUNDLE_ROOT, "glossary")
        _dst_gl = os.path.join(RUNTIME_ROOT, "glossary")
        if os.path.isdir(_seed_gl):
            os.makedirs(_dst_gl, exist_ok=True)
            import shutil
            for _f in os.listdir(_seed_gl):
                if _f.endswith(".csv") and not os.path.exists(os.path.join(_dst_gl, _f)):
                    shutil.copyfile(os.path.join(_seed_gl, _f), os.path.join(_dst_gl, _f))
    except Exception:  # noqa: BLE001 — non-fatal; glossary list just stays empty
        pass
else:
    SYSTEM_CONFIG = os.path.join(CONFIG_DIR, "system_config.json")
    _seed_file(_CONFIG_TEMPLATE, SYSTEM_CONFIG)   # first run / fresh clone

# Explicit override (set by the test suite via conftest.py): redirect the
# writable config to a throwaway file so a test run NEVER mutates the user's real
# system_config.json. A killed/interrupted test used to leave the live config
# pointing at tests/_roundtrip_work/* (so real translations wrote into the test
# tree). Seeded from the template when absent.
_cfg_override = os.environ.get("LINGUAHARU_CONFIG")
if _cfg_override:
    SYSTEM_CONFIG = _cfg_override
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_cfg_override)), exist_ok=True)
        _seed_file(_CONFIG_TEMPLATE, SYSTEM_CONFIG)
    except Exception:  # noqa: BLE001
        pass


def _migrate_data_layout():
    """One-time move from the old all-in-data/ layout to the split layout:
    user content (result/, glossary/, log/) at the top level; internals stay in
    data/ (uploads, keys, history, …). Moves only the default locations, only
    when the destination doesn't already exist, and never merges/clobbers — so a
    custom path or an already-migrated install is left untouched. Best-effort."""
    marker = os.path.join(DATA_DIR, ".layout_v2")
    if os.path.exists(marker):
        return
    import shutil
    moves = [
        (os.path.join(DATA_DIR, "result"),      os.path.join(RUNTIME_ROOT, "result")),
        (os.path.join(DATA_DIR, "glossary"),    os.path.join(RUNTIME_ROOT, "glossary")),
        (os.path.join(DATA_DIR, "web_uploads"), os.path.join(DATA_DIR, "uploads")),
        (os.path.join(DATA_DIR, "mykeys"),      os.path.join(DATA_DIR, "keys")),
    ]
    try:
        for src, dst in moves:
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.move(src, dst)
        # The global system.log moved from data/ to log/.
        new_log = os.path.join(RUNTIME_ROOT, "log")
        for name in ("system.log", "system.log.1", "system.log.2", "system.log.3"):
            s = os.path.join(DATA_DIR, name)
            if os.path.isfile(s):
                os.makedirs(new_log, exist_ok=True)
                d = os.path.join(new_log, name)
                if not os.path.exists(d):
                    shutil.move(s, d)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:  # noqa: BLE001 — migration is best-effort, never fatal
        pass


_migrate_data_layout()
