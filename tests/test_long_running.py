"""An app left open for days: settings that survive, clients that are reused."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import backend
from core.llm import online_translation as ot


def test_settings_are_never_read_half_written(tmp_path, monkeypatch):
    """Written in place, the file was caught half written by a reader (live voice
    reads it a dozen times a line): "no settings", and the next set_config wrote
    the defaults back over everything."""
    path = tmp_path / "system_config.json"
    monkeypatch.setattr(backend, "CONFIG_PATH", str(path))
    big = {f"key{i}": "x" * 200 for i in range(400)}
    backend.write_config(dict(big, marker=0))
    bad, stop = [], threading.Event()

    def read():
        while not stop.is_set():
            cfg = backend.read_config()
            if "marker" not in cfg or len(cfg) != 401:
                bad.append(len(cfg))
            time.sleep(0.001)
    readers = [threading.Thread(target=read) for _ in range(4)]
    for t in readers:
        t.start()
    try:
        for i in range(60):                    # 50 writes a second: far more than the app ever does
            backend.write_config(dict(big, marker=i))
            time.sleep(0.02)
    finally:
        stop.set()
        for t in readers:
            t.join()
    assert bad == [] and backend.read_config()["marker"] == 59
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_one_client_for_an_interface_and_key():
    made = []

    class Client:
        def __init__(self, **kw):
            made.append(kw)
    a = ot._shared_client(Client, "key-1", "https://api.example.test/v1", 90.0, 0)
    assert ot._shared_client(Client, "key-1", "https://api.example.test/v1", 90.0, 0) is a
    assert ot._shared_client(Client, "key-2", "https://api.example.test/v1", 90.0, 0) is not a
    assert ot._shared_client(Client, "key-1", "https://api.example.test/v1") is not a      # the stream path's own
    assert made[0] == {"api_key": "key-1", "base_url": "https://api.example.test/v1", "timeout": 90.0, "max_retries": 0}
    assert made[2] == {"api_key": "key-1", "base_url": "https://api.example.test/v1"} and len(made) == 3


def _file(path, mb, age_h):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(int(mb * 1024 * 1024))
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))


def test_the_result_cap_only_takes_the_apps_own_run_folders(tmp_path):
    """The cap (5 GB by default, applied at every start) deleted the oldest
    entries of the result folder until it fit, whatever they were — and the
    recording of a two-day meeting is 5.5 GB by itself: everything older went,
    then the recording too. So did the user's own files, in a result folder set
    to one that holds other things."""
    from core import retention
    d = str(tmp_path)

    def run(name, age_h, root=d):
        _file(os.path.join(root, name, "report_en2zh.docx"), 1, age_h)
        t = time.time() - age_h * 3600
        os.utime(os.path.join(root, name), (t, t))
    for i in range(6):                                       # runs, oldest first
        run(f"2026-11-0{i + 1}_10-00-00", age_h=100 - i)
    run("2026-11-12_23-00-00", age_h=0.2)                    # a task that ended minutes ago
    _file(os.path.join(d, "realtime_20261101_090000.txt"), 0.1, age_h=300)
    _file(os.path.join(d, "realtime_20261101_090000.ogg"), 2, age_h=300)
    _file(os.path.join(d, "realtime_20261112_173139.wav"), 9, age_h=30)     # far over the cap by itself
    _file(os.path.join(d, "thesis_final.docx"), 4, age_h=900)                # the user's own
    _file(os.path.join(d, "Photos 2019", "img.jpg"), 4, age_h=900)
    os.utime(os.path.join(d, "Photos 2019"), (time.time() - 900 * 3600,) * 2)
    assert retention.prune_results(d, max_size_mb=3) == 4
    assert sorted(os.listdir(d)) == [
        "2026-11-05_10-00-00", "2026-11-06_10-00-00", "2026-11-12_23-00-00", "Photos 2019",
        "realtime_20261101_090000.ogg", "realtime_20261101_090000.txt", "realtime_20261112_173139.wav",
        "thesis_final.docx"]
    # on the web the entries are a session's runs, never the session's whole folder
    w = str(tmp_path / "web")
    session = os.path.join(w, "4d2dd579c374abc72192d3ebcb63e3b7")
    for i in range(6):
        run(f"2026-10-1{i}_09-00-00_8c9cc{i}", age_h=100 - i, root=session)
    _file(os.path.join(session, "realtime_20261112_205140.txt"), 0.1, age_h=200)
    assert retention.prune_results(w, max_size_mb=3) == 3
    assert sorted(os.listdir(session)) == ["2026-10-13_09-00-00_8c9cc3", "2026-10-14_09-00-00_8c9cc4",
                                           "2026-10-15_09-00-00_8c9cc5", "realtime_20261112_205140.txt"]
    assert retention.prune_results(w, max_size_mb=0) == 0                   # off: nothing


def test_settings_are_saved_where_the_file_cannot_be_swapped(tmp_path, monkeypatch):
    """A settings file mounted into a container (the compose file's own advice)
    cannot be replaced, only written: every save failed and left a .tmp behind."""
    path = tmp_path / "system_config.json"
    monkeypatch.setattr(backend, "CONFIG_PATH", str(path))
    backend.write_config({"a": 1})

    def busy(src, dst):
        raise OSError(16, "Device or resource busy")
    monkeypatch.setattr(backend.os, "replace", busy)
    backend.write_config({"a": 2, "b": "x"})
    assert backend.read_config() == {"a": 2, "b": "x"}
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []

    def broken(obj, f, **kw):
        raise ValueError("not serializable")
    monkeypatch.setattr(backend.json, "dump", broken)
    try:
        backend.write_config({"a": 3})
    except ValueError:
        pass
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []      # ... nor when the write itself fails


def test_settings_that_cannot_be_read_are_an_error_not_the_defaults(tmp_path, monkeypatch):
    """A file held by another program came back as "the defaults" after the
    retries — and the next set_config wrote those over the real settings."""
    import builtins
    import pytest
    path = tmp_path / "system_config.json"
    path.write_text('{"marker": 1}', encoding="utf-8")
    monkeypatch.setattr(backend, "CONFIG_PATH", str(path))
    real = builtins.open

    def held(p, *a, **k):
        if str(p) == str(path):
            raise PermissionError(13, "held")
        return real(p, *a, **k)
    monkeypatch.setattr(builtins, "open", held)
    with pytest.raises(PermissionError):
        backend.read_config()


def test_a_client_follows_the_systems_proxy(monkeypatch):
    """A client reads the proxy settings when it is built. Kept for the app's
    life, it went on using a proxy that had been switched off (or none, after
    one was switched on): every request failed until the app was restarted."""
    import urllib.request

    class Client:
        def __init__(self, **kw):
            pass
    proxies = {}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(proxies))
    a = ot._shared_client(Client, "key-p", "https://api.example.test/v1", 90.0, 0)
    assert ot._shared_client(Client, "key-p", "https://api.example.test/v1", 90.0, 0) is a
    proxies["https"] = "http://127.0.0.1:7890"                # the proxy is switched on
    b = ot._shared_client(Client, "key-p", "https://api.example.test/v1", 90.0, 0)
    assert b is not a
    proxies.clear()                                           # ... and off again: the first one will do
    assert ot._shared_client(Client, "key-p", "https://api.example.test/v1", 90.0, 0) is a
