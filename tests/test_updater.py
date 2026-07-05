# Tests for the self-update pipeline (core/updater.py): version compare, asset
# candidate ordering (China proxies), zip-slip protection, and a full FAKE-PORTABLE
# end-to-end download_and_apply — applied overlay, preserved user config/data,
# checksum refusal, and transactional rollback when the post-update dep sync fails.
#
# No network is used: the "release zip" is served via a file:// URL.
#
# Run from the repo root:
#   python tests/test_updater.py
import io
import os
import sys
import json
import shutil
import zipfile
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core import updater

PASS = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def test_version_tuple_compare():
    t = updater._to_tuple
    assert t("5.1.0") == (5, 1, 0)
    assert t("V5.1.2") > t("5.1.0")
    assert t("5.10.0") > t("5.9.9")
    assert t("5.1") == (5, 1, 0)
    assert t("") == (0, 0, 0) and t(None) == (0, 0, 0)
    ok("version tuple compare")


def test_asset_candidates_order_and_dedup():
    url = "https://github.com/X/Y/releases/download/v1/a.zip"
    c = updater._asset_candidates(url, ["https://cdn.example.com/a.zip", url])
    # explicit mirrors first, then the direct URL, then proxied variants
    assert c[0] == "https://cdn.example.com/a.zip"
    assert c[1] == url
    assert any(u.startswith("https://ghproxy.net/") for u in c[2:])
    assert len(c) == len(set(c)), "must be de-duplicated"
    # non-GitHub URL gets no proxy variants
    c2 = updater._asset_candidates("https://oss.example.com/a.zip", None)
    assert c2 == ["https://oss.example.com/a.zip"]
    ok("asset candidates: mirrors -> direct -> proxies, deduped")


def test_safe_extract_rejects_zip_slip():
    tmp = tempfile.mkdtemp()
    try:
        bad = os.path.join(tmp, "evil.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../escape.txt", "x")
        try:
            updater._safe_extract(bad, os.path.join(tmp, "out"))
            assert False, "zip slip must be rejected"
        except ValueError:
            pass
        bad2 = os.path.join(tmp, "abs.zip")
        with zipfile.ZipFile(bad2, "w") as z:
            z.writestr("/abs.txt", "x")
        try:
            updater._safe_extract(bad2, os.path.join(tmp, "out2"))
            assert False, "absolute path must be rejected"
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("zip-slip / absolute-path entries rejected")


def test_find_inner_root():
    tmp = tempfile.mkdtemp()
    try:
        flat = os.path.join(tmp, "flat")
        os.makedirs(flat)
        io.open(os.path.join(flat, "version.json"), "w").write("{}")
        assert updater._find_inner_root(flat) == flat
        wrapped = os.path.join(tmp, "wrapped")
        inner = os.path.join(wrapped, "LinguaHaru-web")
        os.makedirs(inner)
        io.open(os.path.join(inner, "version.json"), "w").write("{}")
        assert updater._find_inner_root(wrapped) == inner
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok("inner-root detection (flat + wrapped)")


def test_check_for_update_logic():
    real_fetch = updater._fetch_remote
    try:
        updater._fetch_remote = lambda timeout=6: {"version": "99.0.0", "url": "u"}
        r = updater.check_for_update()
        assert r and r["update"] is True and r["latest"] == "99.0.0"
        updater._fetch_remote = lambda timeout=6: {"version": "0.0.1"}
        assert updater.check_for_update()["update"] is False
        updater._fetch_remote = lambda timeout=6: None
        assert updater.check_for_update() is None
    finally:
        updater._fetch_remote = real_fetch
    ok("check_for_update: newer/older/unreachable")


def _make_fake_portable(tmp):
    """A minimal portable root: python marker + old source + user data."""
    root = os.path.join(tmp, "portable")
    os.makedirs(os.path.join(root, "python"))
    io.open(os.path.join(root, "python",
                         "python.exe" if os.name == "nt" else "python"), "w").write("")
    os.makedirs(os.path.join(root, "core"))
    io.open(os.path.join(root, "core", "old.py"), "w").write("OLD")
    os.makedirs(os.path.join(root, "config", "api_config"))
    io.open(os.path.join(root, "config", "system_config.json"), "w").write('{"mine": 1}')
    io.open(os.path.join(root, "config", "api_config", "Custom.json"), "w").write("{}")
    io.open(os.path.join(root, "config", "locales.json"), "w").write("OLD-LOCALES")
    os.makedirs(os.path.join(root, "data"))
    io.open(os.path.join(root, "data", "user.db"), "w").write("USERDATA")
    io.open(os.path.join(root, "version.json"), "w").write('{"version": "5.0.0"}')
    return root


def _make_release_zip(tmp, version="5.2.0"):
    """A fake newer release zip (wrapped in an inner dir like the real ones)."""
    src = os.path.join(tmp, "newsrc", "LinguaHaru-web")
    os.makedirs(os.path.join(src, "core"))
    io.open(os.path.join(src, "core", "new.py"), "w").write("NEW")
    os.makedirs(os.path.join(src, "config"))
    io.open(os.path.join(src, "config", "system_config.json"), "w").write('{"shipped": 1}')
    io.open(os.path.join(src, "config", "locales.json"), "w").write("NEW-LOCALES")
    io.open(os.path.join(src, "version.json"), "w").write(json.dumps({"version": version}))
    zpath = os.path.join(tmp, "release.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fns in os.walk(os.path.dirname(src)):
            for fn in fns:
                fp = os.path.join(dp, fn)
                z.write(fp, os.path.relpath(fp, os.path.dirname(src)))
    return zpath


def _file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def test_download_and_apply_e2e():
    tmp = tempfile.mkdtemp()
    real_root, real_sync = updater.portable_root, updater._sync_base_deps
    try:
        root = _make_fake_portable(tmp)
        zpath = _make_release_zip(tmp)
        sha = updater._sha256(zpath)
        updater.portable_root = lambda: root
        updater._sync_base_deps = lambda r: True   # exercised separately below

        # (1) refuse without a checksum
        okk, msg = updater.download_and_apply(_file_url(zpath), None)
        assert not okk and "checksum" in msg.lower(), msg
        # (2) refuse a wrong checksum
        okk, msg = updater.download_and_apply(_file_url(zpath), "0" * 64)
        assert not okk, msg
        assert os.path.exists(os.path.join(root, "core", "old.py")), "must be untouched"
        ok("refuses missing/wrong checksum, leaves install untouched")

        # (3) real apply
        okk, msg = updater.download_and_apply(_file_url(zpath), sha)
        assert okk, msg
        assert os.path.exists(os.path.join(root, "core", "new.py"))
        assert not os.path.exists(os.path.join(root, "core", "old.py")), \
            "source layer must be REPLACED, not merged"
        # user-owned config survives; shipped config is refreshed
        assert io.open(os.path.join(root, "config", "system_config.json")).read() == '{"mine": 1}'
        assert os.path.exists(os.path.join(root, "config", "api_config", "Custom.json"))
        assert io.open(os.path.join(root, "config", "locales.json")).read() == "NEW-LOCALES"
        # data/ + python/ untouched
        assert io.open(os.path.join(root, "data", "user.db")).read() == "USERDATA"
        assert json.load(io.open(os.path.join(root, "version.json")))["version"] == "5.2.0"
        ok("apply: overlay replaced, user config/data preserved, version bumped")

        # (4) rollback when the dependency sync fails
        root2 = _make_fake_portable(os.path.join(tmp, "second"))
        updater.portable_root = lambda: root2
        updater._sync_base_deps = lambda r: False
        okk, msg = updater.download_and_apply(_file_url(zpath), sha)
        assert not okk and "rolled back" in msg.lower(), msg
        assert os.path.exists(os.path.join(root2, "core", "old.py")), "rollback lost old.py"
        assert not os.path.exists(os.path.join(root2, "core", "new.py"))
        assert json.load(io.open(os.path.join(root2, "version.json")))["version"] == "5.0.0"
        ok("failed dep-sync rolls back to the pre-update state")
    finally:
        updater.portable_root, updater._sync_base_deps = real_root, real_sync
        shutil.rmtree(tmp, ignore_errors=True)


def test_plugin_update_check_china_fallback():
    # The plugin update check consults PyPI JSON with a Tsinghua-mirror fallback.
    from core.module_manager import _PYPI_JSON
    assert any("tuna.tsinghua" in u for u in _PYPI_JSON), _PYPI_JSON
    from core import module_manager as mm
    assert "tuna.tsinghua" in mm._PYPI_MIRROR
    ok("plugin update check + installs have a China mirror")


if __name__ == "__main__":
    test_version_tuple_compare()
    test_asset_candidates_order_and_dedup()
    test_safe_extract_rejects_zip_slip()
    test_find_inner_root()
    test_check_for_update_logic()
    test_download_and_apply_e2e()
    test_plugin_update_check_china_fallback()
    print(f"{PASS} passed, 0 failed")
