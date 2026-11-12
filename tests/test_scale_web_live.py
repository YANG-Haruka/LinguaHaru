# A real-time voice session of 24-48 hours, web side: what the host must do for
# it. (The page's own long-session logic — one node per line, the recognition
# timeout and bounded queue, the stamps, the neural VAD's cap — runs in
# tests/web_live_hold.mjs, driven by tests/test_live_stream_race.py.)
#   - keep-awake was asked for by whichever thread came first and died with it
#   - a browser's live session did not keep the host awake at all
#   - the new-terms report read the first 3000 lines (7.5 hours) only
import os
import sys
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core import backend, power

AWAKE = power._ES_CONTINUOUS | power._ES_SYSTEM_REQUIRED
IDLE = power._ES_CONTINUOUS


@pytest.fixture
def exec_state(monkeypatch):
    """The Windows call, recorded: [(flags, thread that made it)]."""
    calls = []
    monkeypatch.setattr(power, "_set_exec_state",
                        lambda flags: calls.append((flags, threading.current_thread())))

    def idle():                    # whatever ran before (or the test) left held: let go
        with power._lock:
            power._busy, power._lease_until = 0, 0.0
        power._changed()
        end = time.monotonic() + 5.0
        while power._held and time.monotonic() < end:
            time.sleep(0.01)
    idle()
    calls.clear()

    def wait_for(flags, timeout=5.0):
        end = time.monotonic() + timeout
        while [f for f, _ in calls] != flags and time.monotonic() < end:
            time.sleep(0.01)
        assert [f for f, _ in calls] == flags
    yield calls, wait_for
    idle()


def test_keep_awake_outlives_the_thread_that_asked_for_it(exec_state):
    """SetThreadExecutionState is per thread: asked for by a worker that then
    finished, it was gone while a session started later still counted."""
    calls, wait_for = exec_state
    worker = threading.Thread(target=power.begin_activity)     # a file task's worker ...
    worker.start(); worker.join()
    wait_for([AWAKE])
    power.begin_activity()                                      # ... a live session starts
    power.end_activity()                                        # ... the file task is done
    time.sleep(0.1)
    assert [f for f, _ in calls] == [AWAKE]                     # still one in progress
    holder = calls[0][1]
    assert holder is not worker and holder is not threading.current_thread()
    assert holder.is_alive() and holder.daemon
    power.end_activity()
    wait_for([AWAKE, IDLE])
    assert calls[1][1] is holder                                # every call from the one thread
    power.end_activity()                                        # one too many: no harm
    with power.keep_awake():
        wait_for([AWAKE, IDLE, AWAKE])
    wait_for([AWAKE, IDLE, AWAKE, IDLE])


def test_a_lease_holds_until_it_runs_out(exec_state):
    calls, wait_for = exec_state
    power.touch_activity(1.0)
    wait_for([AWAKE])
    power.begin_activity()
    power.end_activity()               # the lease still runs
    power.touch_activity(0.01)         # a shorter one does not cut it short
    time.sleep(0.2)
    assert [f for f, _ in calls] == [AWAKE]
    wait_for([AWAKE, IDLE])            # ... and lets go by itself
    power.touch_activity(0.3)
    wait_for([AWAKE, IDLE, AWAKE, IDLE])


def test_no_windows_no_call(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert power._kernel32() is None
    power._set_exec_state(AWAKE)       # nothing to call, nothing raised


def _client(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from webapp import server
    monkeypatch.setattr(server, "realtime_voice_available", lambda: True)
    monkeypatch.setattr(server, "_feature_hidden", lambda name: False)
    return TestClient(server.app)


def test_web_live_session_keeps_the_host_awake(monkeypatch):
    """Only file tasks held keep_awake(): a host serving a browser's live
    session could sleep mid-meeting. Each recognition extends a lease."""
    import base64
    from core.pipelines import video_translation_pipeline as vp
    leases = []
    monkeypatch.setattr(power, "touch_activity", lambda seconds=120.0: leases.append(seconds))
    monkeypatch.setattr(vp, "recognize_utterance", lambda pcm, **kw: ("はい", "ja"))
    monkeypatch.setattr(vp, "live_preview_model", lambda: None)
    monkeypatch.setattr(vp, "live_commit_mode", lambda: "stream")
    client = _client(monkeypatch)
    audio = base64.b64encode(b"\x10\x00" * 1600).decode()
    assert client.post("/api/live-recognize", json={"audio_b64": audio}).json()["source"] == "はい"
    client.post("/api/live-recognize", json={"audio_b64": audio, "final": True})
    assert leases == [120, 120]


def test_web_new_terms_read_a_two_day_session(monkeypatch):
    seen = []
    monkeypatch.setattr(backend, "live_new_terms", lambda pairs, dst, m, o, k, glossary=None, main_lang="auto":
                        (seen.append(pairs), ([], 0))[1])
    monkeypatch.setattr(backend, "read_config", lambda: {"default_online": False})
    monkeypatch.setattr(backend, "get_active_model", lambda use_online=True: "m")
    client = _client(monkeypatch)
    pairs = [["x" * 700, "译"]] + [[f"line {i}", "译"] for i in range(1, 30500)]
    assert client.post("/api/live-terms", json={"pairs": pairs, "dst_lang": "zh"}).status_code == 200
    assert len(seen[0]) == 30000                          # 20,000 lines fit; 3000 was 7.5 hours
    assert seen[0][-1][0] == "line 29999" and len(seen[0][0][0]) == 600


def test_a_quiet_web_session_keeps_the_host_awake_and_its_models(monkeypatch):
    """Silence sends no audio: neither the keep-awake lease nor the models' "in
    use" mark was renewed during a quiet stretch of a session."""
    import pytest
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from core import power
    from core.pipelines import video_translation_pipeline as vp
    from webapp import server
    leases, touched = [], []
    monkeypatch.setattr(power, "touch_activity", lambda seconds=120: leases.append(seconds))
    monkeypatch.setattr(vp, "stt_touch_live", lambda: touched.append(1))
    monkeypatch.setattr(server, "_feature_hidden", lambda name: False)
    client = TestClient(server.app)
    assert client.post("/api/live-keepalive").json() == {"ok": True}
    assert leases == [120] and touched == [1]
    monkeypatch.setattr(server, "_feature_hidden", lambda name: True)
    assert client.post("/api/live-keepalive").status_code == 403
