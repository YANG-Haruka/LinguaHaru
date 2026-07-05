# Tests that the i18n label table is complete:
#   For every key in LABEL_TRANSLATIONS["en"] (the source of truth), every other
#   language must define that key with a non-empty value. Guards against keys
#   being added for only some languages (silent English fallback).
#
# Run from the repo root:
#   python tests/test_i18n_complete.py
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

# Console-safe output on Windows (CJK text)
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core.languages_config import LABEL_TRANSLATIONS


def test_no_missing_or_empty_keys():
    en_keys = list(LABEL_TRANSLATIONS["en"])
    gaps = []
    for lang, labels in LABEL_TRANSLATIONS.items():
        if lang == "en":
            continue
        for key in en_keys:
            if key not in labels:
                gaps.append((lang, key, "missing"))
            elif not str(labels[key]).strip():
                gaps.append((lang, key, "empty"))
    if gaps:
        for lang, key, why in gaps:
            print(f"  [{why}] {lang}: {key}")
    assert not gaps, f"{len(gaps)} i18n gap(s) found"
    print(f"OK: {len(en_keys)} keys complete across "
          f"{len(LABEL_TRANSLATIONS)} languages, 0 gaps")


if __name__ == "__main__":
    test_no_missing_or_empty_keys()
    print("All i18n completeness tests passed.")
