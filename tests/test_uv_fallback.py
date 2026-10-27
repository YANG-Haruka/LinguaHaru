"""uv refuses a Python whose stdout gets extra lines from injected software
("Failed to inspect Python interpreter ... trailing characters"); the install
must then fall back to pip instead of failing."""
import sys
from core import module_manager as mm


def test_uv_to_pip_translation():
    py = sys.executable
    assert mm._uv_to_pip(["uv.exe", "pip", "install", "--python", py, "--index-url", "I", "-r", "r.txt"]) \
        == [py, "-m", "pip", "install", "--index-url", "I", "-r", "r.txt"]
    assert mm._uv_to_pip(["uv.exe", "pip", "uninstall", "--python", py, "a", "b"]) \
        == [py, "-m", "pip", "uninstall", "-y", "a", "b"]
    assert "--force-reinstall" in mm._uv_to_pip(["uv.exe", "pip", "install", "--python", py, "--reinstall", "x"])


def test_inspect_failure_retries_with_pip_and_disables_uv(monkeypatch):
    monkeypatch.setattr(mm, "_UV_UNUSABLE", False)
    calls = []

    def fake_once(cmd):
        calls.append(cmd)
        if cmd[0].endswith("uv.exe"):
            return False, "error: Failed to inspect Python interpreter from provided path"
        return True, "Successfully installed x"

    monkeypatch.setattr(mm, "_run_once", fake_once)
    ok, out = mm._run(["C:/p/uv.exe", "pip", "install", "--python", sys.executable, "x"])
    assert ok and calls[1][1:4] == ["-m", "pip", "install"]
    assert mm._UV_UNUSABLE and mm._uv_exe() is None      # rest of the session uses pip


def test_other_uv_failures_are_not_retried(monkeypatch):
    monkeypatch.setattr(mm, "_UV_UNUSABLE", False)
    calls = []
    monkeypatch.setattr(mm, "_run_once", lambda cmd: calls.append(cmd) or (False, "No solution found"))
    ok, _ = mm._run(["C:/p/uv.exe", "pip", "install", "--python", sys.executable, "x"])
    assert not ok and len(calls) == 1 and not mm._UV_UNUSABLE
