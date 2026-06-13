# Shared scaffolding for the test_corpus_* files.
#
# Each corpus file is standalone:
#   python tests/test_corpus_<format>.py
# This module only provides the check()/fake_translate() pattern shared by
# the existing round-trip suites, plus per-format work directories.
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)  # prompts and config are loaded via relative paths

# Console-safe output on Windows (CJK text, ␊ markers)
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

T = "[T]"
CHECKS = []


def work_dirs(format_name):
    """(work, temp, result) directories for one corpus file, freshly reset."""
    import shutil
    work = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "corpus", format_name)
    temp = os.path.join(work, "temp")
    result = os.path.join(work, "result")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(temp, exist_ok=True)
    os.makedirs(result, exist_ok=True)
    return work, temp, result


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"\n         -> {detail}" if detail and not cond else ""))
    CHECKS.append((name, bool(cond)))
    return bool(cond)


def fake_translate(src_json_path):
    """dst_translated.json next to src.json: prefix every value with [T]."""
    with open(src_json_path, encoding="utf-8") as f:
        data = json.load(f)
    out = [{"count_src": i["count_src"], "type": i.get("type", "text"),
            "original": i["value"], "translated": T + i["value"]} for i in data]
    dst = os.path.join(os.path.dirname(src_json_path), "dst_translated.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return dst


def run(test_functions):
    """Run tests, print the summary, exit non-zero on any failure."""
    for fn in test_functions:
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            CHECKS.append((fn.__name__ + " (crashed)", False))
        print()

    passed = sum(1 for _, ok in CHECKS if ok)
    print("=" * 60)
    print(f"{passed}/{len(CHECKS)} checks passed")
    for name, ok in CHECKS:
        if not ok:
            print(f"  FAIL: {name}")
    sys.exit(0 if passed == len(CHECKS) else 1)
