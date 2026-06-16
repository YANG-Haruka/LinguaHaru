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
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")             # WRITABLE — models/temp/result/log

# system_config.json must be writable (model choices, settings). In a frozen
# build keep it next to the exe (persists) and seed it from the bundled default
# on first run; from source it stays in the repo's config/.
if _FROZEN:
    _WRITABLE_CONFIG_DIR = os.path.join(RUNTIME_ROOT, "config")
    SYSTEM_CONFIG = os.path.join(_WRITABLE_CONFIG_DIR, "system_config.json")
    try:
        os.makedirs(_WRITABLE_CONFIG_DIR, exist_ok=True)
        if not os.path.exists(SYSTEM_CONFIG):
            _seed = os.path.join(CONFIG_DIR, "system_config.json")
            if os.path.exists(_seed):
                import shutil
                shutil.copyfile(_seed, SYSTEM_CONFIG)
    except Exception:  # noqa: BLE001 — fall back to bundled path if seeding fails
        SYSTEM_CONFIG = os.path.join(CONFIG_DIR, "system_config.json")
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
else:
    SYSTEM_CONFIG = os.path.join(CONFIG_DIR, "system_config.json")
