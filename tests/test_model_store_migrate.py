# Standalone tests for core.model_store.migrate_legacy_caches().
#
# Uses a TEMP HOME and a TEMP models_dir (config) so it NEVER touches the real
# ~/.paddlex or ~/.cache/babeldoc. Prints check results; exits nonzero on fail.
#
# Run from the repo root:
#   python tests/test_model_store_migrate.py
import json
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import core.model_store as ms

_checks = {"pass": 0, "fail": 0}


def check(cond, msg):
    if cond:
        _checks["pass"] += 1
        print(f"  PASS: {msg}")
    else:
        _checks["fail"] += 1
        print(f"  FAIL: {msg}")


def _write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _patch(home, md):
    """Point expanduser('~') at `home` and current_dir() at `md`."""
    real_expand = os.path.expanduser

    def fake_expand(p):
        if p == "~" or p.startswith("~" + os.sep) or p == "~/" or p.startswith("~/"):
            return p.replace("~", home, 1)
        return real_expand(p)

    os.path.expanduser = fake_expand
    ms.current_dir = lambda: md
    return real_expand


def _restore(real_expand, real_current_dir):
    os.path.expanduser = real_expand
    ms.current_dir = real_current_dir


def main():
    real_current_dir = ms.current_dir
    root = tempfile.mkdtemp(prefix="ms_migrate_")
    try:
        home = os.path.join(root, "home")
        md = os.path.join(root, "models")
        os.makedirs(md, exist_ok=True)

        # Legacy layout under temp HOME.
        paddle_dummy = os.path.join(home, ".paddlex", "official_models",
                                    "PP-OCRv6", "dummy.bin")
        babel_dummy = os.path.join(home, ".cache", "babeldoc", "models",
                                   "dummy.onnx")
        _write(paddle_dummy, "paddle")
        _write(babel_dummy, "babel")

        # Pre-existing dst entry that must NOT be clobbered.
        preexisting = os.path.join(md, "paddlex", "official_models",
                                   "PP-OCRv6", "dummy.bin")
        _write(preexisting, "ORIGINAL")

        real_expand = _patch(home, md)
        try:
            summary = ms.migrate_legacy_caches()
        finally:
            _restore(real_expand, real_current_dir)

        # (1) Files end up under the unified dir.
        check(os.path.exists(babel_dummy.replace(
            os.path.join(home, ".cache", "babeldoc"),
            os.path.join(md, "babeldoc"))),
            "babeldoc dummy moved under <md>/babeldoc/models/")
        # paddlex official_models dir already existed in dst, so moving its
        # *contents* applies recursively-by-entry: the conflicting file is kept.

        # (4) Pre-existing dst entry NOT clobbered.
        with open(preexisting, encoding="utf-8") as f:
            check(f.read() == "ORIGINAL",
                  "pre-existing dst file not clobbered (PP-OCRv6 kept)")

        # (2) Marker created.
        marker = os.path.join(md, ".legacy_migrated")
        check(os.path.exists(marker), "marker file created at <md>/.legacy_migrated")

        # (3) Second call is a no-op (idempotent).
        _write(paddle_dummy, "paddle2")  # re-create legacy; should NOT move now
        real_expand = _patch(home, md)
        try:
            summary2 = ms.migrate_legacy_caches()
        finally:
            _restore(real_expand, real_current_dir)
        check(summary2["moved"] == [],
              "second call moves nothing (idempotent via marker)")
        check(os.path.exists(paddle_dummy),
              "second call leaves re-created legacy file untouched")

        print(f"  summary(first call): moved={len(summary['moved'])} "
              f"skipped={len(summary['skipped'])}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # (5) Never raises when old dirs are absent.
    root2 = tempfile.mkdtemp(prefix="ms_migrate_empty_")
    try:
        home2 = os.path.join(root2, "home")
        md2 = os.path.join(root2, "models")
        os.makedirs(home2, exist_ok=True)
        os.makedirs(md2, exist_ok=True)
        real_expand = _patch(home2, md2)
        try:
            s = ms.migrate_legacy_caches()
            check(s["moved"] == [], "no-op when legacy dirs absent (no moves)")
            check(os.path.exists(os.path.join(md2, ".legacy_migrated")),
                  "marker still written when legacy dirs absent")
        except Exception as e:  # noqa: BLE001
            check(False, f"raised when old dirs absent: {e}")
        finally:
            _restore(real_expand, real_current_dir)
    finally:
        shutil.rmtree(root2, ignore_errors=True)

    print(f"\n{_checks['pass']} passed, {_checks['fail']} failed")
    sys.exit(0 if _checks["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
