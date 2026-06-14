# Tests for the public-deploy "server mode" gating of the FastAPI web app:
#   - bootstrap advertises server_mode
#   - admin endpoints (config / apikey / module install) are forbidden (403)
#   - read endpoints still work
#   - the server binds externally (0.0.0.0) in server mode
#
# Skips cleanly if FastAPI's TestClient dependency (httpx) is unavailable.
#
# Run from the repo root:
#   python tests/test_server_mode.py
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def main():
    try:
        from fastapi.testclient import TestClient
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: TestClient unavailable ({e})")
        return

    import core.backend as backend
    from webapp import server

    orig_get = backend.get_config

    def force_server_mode(on):
        if on:
            backend.get_config = lambda k, d=None: (True if k == "server_mode" else orig_get(k, d))
        else:
            backend.get_config = orig_get

    try:
        # --- server mode OFF (default) ---
        force_server_mode(False)
        # RENDER would force it on; make sure the test env doesn't have it.
        had_render = os.environ.pop("RENDER", None)
        c = TestClient(server.app)
        boot = c.get("/api/bootstrap").json()
        check("default: bootstrap server_mode is False", boot.get("server_mode") is False, str(boot.get("server_mode")))
        check("default: host is loopback", server.server_host() == "127.0.0.1", server.server_host())
        # Guard is a no-op when off (checked directly to avoid writing real config).
        guard_ok = True
        try:
            server._block_in_server_mode()
        except Exception:
            guard_ok = False
        check("default: admin guard does not block", guard_ok)

        # --- server mode ON ---
        force_server_mode(True)
        c2 = TestClient(server.app)
        boot2 = c2.get("/api/bootstrap").json()
        check("on: bootstrap server_mode is True", boot2.get("server_mode") is True)
        check("on: host binds 0.0.0.0", server.server_host() == "0.0.0.0", server.server_host())
        check("on: /api/config forbidden", c2.post("/api/config", json={"x": 1}).status_code == 403)
        check("on: /api/apikey forbidden",
              c2.post("/api/apikey", json={"model": "m", "api_key": "k"}).status_code == 403)
        check("on: module install forbidden",
              c2.post("/api/modules/install", json={"name": "PDF"}).status_code == 403)
        check("on: read endpoint still works (proofread docs)",
              c2.get("/api/proofread/docs").status_code == 200)
    finally:
        backend.get_config = orig_get
        if 'had_render' in dir() and had_render is not None:
            os.environ["RENDER"] = had_render

    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for n in FAILED:
        print(f"  FAIL: {n}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
