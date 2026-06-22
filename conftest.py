"""Pytest config: make optional-dependency tests SKIP (not FAIL) when the heavy
ML deps aren't installed, so a bare `pytest` is one-click-green in any environment
(the full dev/build env has all deps and skips nothing)."""
import os

# Isolate the config BEFORE any core import: redirect the writable
# system_config.json to a throwaway file under the test work dir, so a test that
# changes settings (temp/result/log dirs, model choices…) — or is killed mid-run
# before its restore — can NEVER corrupt the user's real config. core.paths reads
# this env var at import time.
os.environ.setdefault(
    "LINGUAHARU_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "tests", "_roundtrip_work", "_test_system_config.json"))

import pytest

# Optional plugin deps — a test that hits one of these in an env that doesn't have
# it should skip, not fail (it's an optional feature, tested fully where installed).
_OPTIONAL = {
    "cv2", "torch", "torchaudio", "PIL", "paddleocr", "paddle", "rapidocr",
    "onnxruntime", "funasr", "qwen_asr", "ten_vad", "soundcard", "edge_tts",
    "modelscope", "babeldoc", "ctranslate2", "faster_whisper",
}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    info = outcome.excinfo
    if info and issubclass(info[0], ModuleNotFoundError):
        name = (getattr(info[1], "name", "") or "").split(".")[0]
        if name in _OPTIONAL:
            outcome.force_exception(
                pytest.skip.Exception(f"optional dependency '{name}' not installed"))
