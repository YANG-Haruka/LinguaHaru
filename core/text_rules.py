# User-defined text rules, AiNiee-style: pre-translation replacements,
# post-translation replacements, and a do-not-translate list.
#
# Edit config/text_rules.json (created with an empty template on first use):
# {
#     "replace_before": [{"from": "原文写法", "to": "送译写法"}],
#     "replace_after":  [{"from": "误译写法", "to": "修正写法"}],
#     "no_translate":   ["BrandName", "社内用語"]
# }
import json
import os
import threading
import time

# Writable, next to system_config.json (repo config/ from source; the persistent
# config dir next to the exe in a frozen build).
from core.paths import SYSTEM_CONFIG as _SYSTEM_CONFIG
RULES_PATH = os.path.join(os.path.dirname(_SYSTEM_CONFIG), "text_rules.json")

_lock = threading.Lock()
_cache = {"mtime": None, "rules": None, "checked": 0.0}
_RECHECK_S = 2.0   # re-stat the file at most this often (it's called per segment)

_EMPTY = {"replace_before": [], "replace_after": [], "no_translate": []}


def load_rules():
    """Load rules with mtime-based caching; missing/invalid file = no rules.
    The mtime stat is throttled to once per _RECHECK_S so per-segment calls on a
    large document don't stat the file tens of thousands of times."""
    now = time.time()
    with _lock:
        if _cache["rules"] is not None and (now - _cache["checked"]) < _RECHECK_S:
            return _cache["rules"]
    try:
        mtime = os.path.getmtime(RULES_PATH)
    except OSError:
        with _lock:
            _cache["rules"] = _EMPTY
            _cache["checked"] = now
        return _EMPTY
    with _lock:
        _cache["checked"] = now
        if _cache["mtime"] == mtime and _cache["rules"] is not None:
            return _cache["rules"]
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            rules = {
                "replace_before": [r for r in data.get("replace_before", [])
                                   if isinstance(r, dict) and r.get("from")],
                "replace_after": [r for r in data.get("replace_after", [])
                                  if isinstance(r, dict) and r.get("from")],
                "no_translate": {str(t).strip() for t in data.get("no_translate", [])
                                 if str(t).strip()},
            }
        except (json.JSONDecodeError, OSError):
            rules = _EMPTY
        _cache["mtime"] = mtime
        _cache["rules"] = rules
        return rules


def apply_replace_before(text):
    for rule in load_rules()["replace_before"]:
        text = text.replace(rule["from"], rule.get("to", ""))
    return text


def apply_replace_after(text):
    for rule in load_rules()["replace_after"]:
        text = text.replace(rule["from"], rule.get("to", ""))
    return text


def is_no_translate(text):
    return text.strip() in load_rules()["no_translate"]
