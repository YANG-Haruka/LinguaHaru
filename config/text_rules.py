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

RULES_PATH = os.path.join("config", "text_rules.json")

_lock = threading.Lock()
_cache = {"mtime": None, "rules": None}

_EMPTY = {"replace_before": [], "replace_after": [], "no_translate": []}


def load_rules():
    """Load rules with mtime-based caching; missing/invalid file = no rules."""
    try:
        mtime = os.path.getmtime(RULES_PATH)
    except OSError:
        return _EMPTY
    with _lock:
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
