"""Release-integrity guards (no network):
  - a plugin install is refused unless the trusted index entry has a sha256
    (it runs downloaded code);
  - the self-updater builds multi-URL download candidates (explicit mirrors
    first, then GitHub direct + via China-friendly proxies) so a mainland-China
    user isn't stuck on a single GitHub URL.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def test_plugin_download_requires_checksum():
    from core import plugins_registry as pr
    # A published entry with a valid https url but NO sha256 must be refused
    # BEFORE any download (security: don't run unverified code).
    orig = pr.fetch_remote_index
    pr.fetch_remote_index = lambda *a, **k: [
        {"key": "demo", "url": "https://example.com/demo.zip"}]
    try:
        ok, msg = pr.download_remote_plugin("demo")
    finally:
        pr.fetch_remote_index = orig
    assert ok is False
    assert "checksum" in msg.lower()


def test_asset_candidates_ordering_and_proxies():
    from core.updater import _asset_candidates, _PROXIES
    gh = "https://github.com/o/r/releases/download/v1/LinguaHaru-web.zip"
    mirror = "https://oss.example.cn/LinguaHaru-web.zip"

    # Explicit mirror comes first, then the GitHub direct URL, then proxied ones.
    cands = _asset_candidates(gh, [mirror])
    assert cands[0] == mirror
    assert gh in cands
    proxied = [p + gh for p in _PROXIES if p]
    assert all(pc in cands for pc in proxied), cands
    # No duplicates, and the direct URL precedes its proxied variants.
    assert len(cands) == len(set(cands))
    assert cands.index(gh) < min(cands.index(pc) for pc in proxied)

    # A non-GitHub URL gets no proxy variants (proxies only help github.com).
    assert _asset_candidates(mirror, None) == [mirror]


if __name__ == "__main__":
    test_plugin_download_requires_checksum()
    test_asset_candidates_ordering_and_proxies()
    print("OK")
