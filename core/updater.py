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
import re
import urllib.request

from core.version import __version__

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
    return {
        "update": _to_tuple(latest) > _to_tuple(__version__),
        "current": __version__,
        "latest": latest,
        "url": data.get("url") or RELEASES_PAGE,
        "notes": data.get("notes", ""),
    }
