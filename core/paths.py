"""Absolute filesystem anchors for the project.

Everything under config/ and assets/ is resolved relative to the repository
root (the parent of this ``core/`` package) instead of the current working
directory, so the app works no matter where it's launched from — e.g. ``uvicorn``
started from another cwd, or a packaged build. Mutable runtime dirs
(temp/result/log) stay configurable via system_config and are handled in
``core.backend``; this module is for the static, code-relative locations.
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR = os.path.join(REPO_ROOT, "config")
PROMPTS_DIR = os.path.join(CONFIG_DIR, "prompts")
API_CONFIG_DIR = os.path.join(CONFIG_DIR, "api_config")
LOCALES_DIR = os.path.join(CONFIG_DIR, "locales")
SYSTEM_CONFIG = os.path.join(CONFIG_DIR, "system_config.json")

ASSETS_DIR = os.path.join(REPO_ROOT, "assets")
DATA_DIR = os.path.join(REPO_ROOT, "data")
