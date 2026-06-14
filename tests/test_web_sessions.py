# HTTP-level multi-user isolation tests for the FastAPI web app, driven through
# the real ASGI stack with TestClient:
#   - every client receives its own session cookie
#   - proofread listing/loading is scoped to the caller's session (no IDOR)
#   - path traversal and foreign task downloads are rejected
#
# Complements tests/test_multiuser.py (which unit-tests webapp.sessions directly).
# Skips cleanly if FastAPI's TestClient dependency (httpx) is unavailable.
#
# Run from the repo root:
#   python tests/test_web_sessions.py
import json
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

SAND = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "websess")
TEMP = os.path.join(SAND, "temp")
RESULT = os.path.join(SAND, "result")
LOG = os.path.join(SAND, "log")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def _make_doc(sid, doc):
    folder = os.path.join(TEMP, sid, doc)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "dst_translated.json"), "w", encoding="utf-8") as f:
        json.dump([{"count_src": 1, "original": "x", "translated": "y"}], f)
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"file_extension": ".txt"}, f)


def main():
    try:
        from fastapi.testclient import TestClient
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: TestClient unavailable ({e})")
        return

    shutil.rmtree(SAND, ignore_errors=True)
    for d in (TEMP, RESULT, LOG):
        os.makedirs(d, exist_ok=True)

    import core.backend as backend
    backend.get_custom_paths = lambda: (TEMP, RESULT, LOG)
    from webapp import server, sessions

    ca = TestClient(server.app)
    ca.get("/api/bootstrap")
    sid_a = ca.cookies.get(sessions.SESSION_COOKIE)
    cb = TestClient(server.app)
    cb.get("/api/bootstrap")
    sid_b = cb.cookies.get(sessions.SESSION_COOKIE)

    check("client A gets a valid session cookie", sessions.valid_session_id(sid_a), sid_a)
    check("clients get distinct sessions", sid_a and sid_b and sid_a != sid_b)

    _make_doc(sid_a, "docA")
    _make_doc(sid_b, "docB")

    docs_a = ca.get("/api/proofread/docs").json()["docs"]
    check("A lists only its own doc", docs_a == [f"{sid_a}/docA"], str(docs_a))
    check("A cannot load B's doc (IDOR)",
          ca.get("/api/proofread", params={"name": f"{sid_b}/docB"}).status_code == 404)
    check("A can load its own doc",
          ca.get("/api/proofread", params={"name": f"{sid_a}/docA"}).status_code == 200)
    check("path traversal rejected",
          ca.get("/api/proofread", params={"name": "../config/system_config"}).status_code == 404)
    check("foreign/unknown task download rejected",
          ca.get("/api/download/deadbeefdead").status_code == 404)

    shutil.rmtree(SAND, ignore_errors=True)

    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for n in FAILED:
        print(f"  FAIL: {n}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
