"""Speech recognition on long files and in a process that stays open for days:
memory that does not follow the file's length, models that do not pile up."""
import json
import os
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.pipelines import video_translation_pipeline as vp


def _wav(path, seconds, sr=16000):
    """Each second's samples carry its number: a fake model can tell where it is."""
    a = np.repeat(np.arange(seconds, dtype=np.int16) + 1, sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(a.tobytes())


class _Whisper:
    """Speech from second 3 of every 10, seven seconds long: one segment each."""

    def __init__(self):
        self.calls = []                      # (seconds given, language asked)

    def transcribe(self, audio, **kw):
        assert isinstance(audio, np.ndarray), "the whole file was handed over"
        sr = 16000
        self.calls.append((len(audio) / sr, kw.get("language")))
        first = int(round(float(audio[0]) * 32768)) - 1          # the file's second this window starts at

        def segments():
            for k in range(0, int(len(audio) / sr) + 10, 10):
                s = k + 3 - first % 10 if first % 10 <= 3 else k + 13 - first % 10
                e = s + 7
                if s >= len(audio) / sr:
                    return
                e = min(e, len(audio) / sr)                         # cut by the window's end
                at = first + int(s)
                yield types.SimpleNamespace(start=float(s), end=float(e), text=f" sec{at} ", words=None)
        return segments(), types.SimpleNamespace(language="ja", duration=len(audio) / sr)


def test_whisper_decodes_a_long_file_a_window_at_a_time(tmp_path, monkeypatch):
    """Given the file, faster-whisper computes the features of all of it first:
    3.4 GB for every hour of speech — a three-hour video failed on 16 GB."""
    model = _Whisper()
    monkeypatch.setattr(vp, "_get_whisper_model", lambda size: model)
    monkeypatch.setattr(vp, "_WHISPER_WINDOW_S", 100.0)
    wav = tmp_path / "long.wav"
    _wav(wav, 335)
    ckpt = str(tmp_path / "ck.jsonl")
    seen = []
    out = vp._transcribe_whisper(str(wav), "small", "auto", lambda p, desc="": seen.append(p), checkpoint_path=ckpt)
    assert max(sec for sec, _ in model.calls) <= 100.0 and len(model.calls) >= 4      # never the whole file
    assert [lang for _, lang in model.calls][0] is None and set(l for _, l in model.calls[1:]) == {"ja"}
    want = [(float(s), float(min(s + 7, 335)), f"sec{s}") for s in range(3, 335, 10)]
    assert [(s, e, t) for s, e, t, _w in out] == want                                  # each once, whole, in place
    assert seen and seen[-1] == pytest.approx(1.0, abs=0.02) and seen == sorted(seen)
    # a stop in the middle, then a resume: from the last finished segment, nothing twice
    model.calls.clear()
    lines = [json.loads(l) for l in open(ckpt, encoding="utf-8")]
    with open(ckpt, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(l) + "\n" for l in lines[:12]))
    out2 = vp._transcribe_whisper(str(wav), "small", "auto", None, checkpoint_path=ckpt)
    assert [(s, e, t) for s, e, t, _w in out2] == want
    assert sum(sec for sec, _ in model.calls) < 335 - 100                             # the done part was not decoded again


def test_a_files_audio_is_kept_once():
    """int16 and, beside it, the whole of it as float32: 3.2 GB resident and
    5.4 GB at the peak for a 10-hour video."""
    i16 = (np.arange(48000) % 2000 - 1000).astype(np.int16)
    audio = vp._FloatAudio(i16)
    assert len(audio) == 48000
    piece = audio[16000:32000]
    assert piece.dtype == np.float32 and np.array_equal(piece, i16[16000:32000].astype(np.float32) / 32768.0)
    assert audio[47990:60000].size == 10 and audio[50000:60000].size == 0


def _reset(monkeypatch, **models):
    monkeypatch.setattr(vp, "_whisper_models", dict(models.get("whisper", {})))
    monkeypatch.setattr(vp, "_qwen_models", dict(models.get("qwen", {})))
    monkeypatch.setattr(vp, "_animewhisper_models", dict(models.get("anime", {})))
    monkeypatch.setattr(vp, "_sensevoice", models.get("sensevoice"))
    monkeypatch.setattr(vp, "_stt_used", {})
    monkeypatch.setattr(vp, "_stt_pinned", {})
    monkeypatch.setattr(vp, "_STT_FORCE_CPU", False)
    monkeypatch.setattr(vp, "_STT_CPU_UNTIL", 0.0)
    monkeypatch.setattr(vp, "_STT_OOM_AT", 0.0)
    monkeypatch.setattr(vp, "_empty_gpu_cache", lambda: None)


def test_idle_models_are_let_go_before_another_is_loaded(monkeypatch):
    """Every selected model stayed in memory from its first use until the app
    closed: live voice, its preview, video, quick voice — 13-15 GB of GPU memory
    by their own figures."""
    _reset(monkeypatch, whisper={"large-v3-turbo": "W"}, qwen={"Qwen/Qwen3-ASR-1.7B": "Q"}, sensevoice=("S", None))
    now = [1000.0]
    monkeypatch.setattr(vp.time, "time", lambda: now[0])
    vp._stt_touch("whisper", "large-v3-turbo")
    vp._stt_touch("sensevoice", "iic/SenseVoiceSmall")           # a live session: used a moment ago
    vp._stt_used[("qwen3asr", "Qwen/Qwen3-ASR-1.7B")] = 1000.0 - 3600        # the video model, an hour ago
    loaded = []
    fw = types.SimpleNamespace(WhisperModel=lambda size, **kw: loaded.append(size) or f"model:{size}")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    assert vp._get_whisper_model("small") == "model:small" and loaded == ["small"]
    assert vp._qwen_models == {} and vp._sensevoice == ("S", None)          # idle: gone; in use: kept
    assert set(vp._whisper_models) == {"large-v3-turbo", "small"}
    # a model that is in memory is handed out without letting anything go
    now[0] += 4000
    assert vp._get_whisper_model("small") == "model:small" and vp._sensevoice == ("S", None)
    # a file being transcribed keeps its model however long it takes
    vp._stt_pinned[("sensevoice", "iic/SenseVoiceSmall")] = 1
    vp._whisper_models.pop("small")
    vp._get_whisper_model("small")
    assert vp._sensevoice == ("S", None) and "large-v3-turbo" not in vp._whisper_models
    del vp._stt_pinned[("sensevoice", "iic/SenseVoiceSmall")]
    now[0] += 4000
    vp._whisper_models.pop("small")
    vp._get_whisper_model("small")
    assert vp._sensevoice is None


def test_out_of_memory_is_not_a_gpu_that_cannot_work(monkeypatch):
    """One out-of-memory error pinned speech recognition to the CPU until the
    app was restarted, with one log line to say so."""
    _reset(monkeypatch, whisper={"large-v3-turbo": "W"}, qwen={"Qwen/Qwen3-ASR-1.7B": "Q"})
    now = [5000.0]
    monkeypatch.setattr(vp.time, "time", lambda: now[0])
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    vp._stt_pinned[("qwen3asr", "Qwen/Qwen3-ASR-1.7B")] = 1                   # the file being transcribed
    vp._fall_back_to_cpu_stt("CUDA out of memory. Tried to allocate 2.00 GiB")
    assert vp._stt_device() == "cuda"                                         # room made, the GPU again
    assert vp._whisper_models == {} and vp._qwen_models == {"Qwen/Qwen3-ASR-1.7B": "Q"}
    now[0] += 5
    vp._fall_back_to_cpu_stt("CUDA failed with error out of memory")          # still: the CPU, for a while
    assert vp._stt_device() == "cpu" and vp._qwen_models == {}
    vp._whisper_models["small"] = "cpu model"
    now[0] += vp._STT_CPU_PAUSE_S + 1
    assert vp._stt_device() == "cuda" and vp._whisper_models == {}            # ... and back, rebuilt
    vp._fall_back_to_cpu_stt("no kernel image is available for execution on the device")
    now[0] += 10 * vp._STT_CPU_PAUSE_S
    assert vp._stt_device() == "cpu"                                          # a GPU torch cannot drive: for good


def test_a_file_pins_its_model_and_an_oom_is_tried_again(monkeypatch):
    _reset(monkeypatch)
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(vp, "get_stt_params", lambda model_id: {})
    tries = []

    def fake(wav, size, *a):
        tries.append((vp._stt_device(), dict(vp._stt_pinned)))
        if len(tries) < 3:
            raise RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB")
        return [(0.0, 1.0, "ok", None)]
    monkeypatch.setattr(vp, "_transcribe_whisper", fake)
    out = vp._run_transcription("whisper", "small", "x.wav", "ja", None, "en", "whisper-small")
    assert out == [(0.0, 1.0, "ok", None)]
    assert [d for d, _ in tries] == ["cuda", "cuda", "cpu"]                   # room made once, then the CPU
    assert all(p == {("whisper", "small"): 1} for _, p in tries) and vp._stt_pinned == {}


def test_ctranslate2_out_of_memory_is_recognized():
    """faster-whisper's wording was not among the CUDA errors: a Whisper file
    that ran out of GPU memory simply failed, the retry never started."""
    assert vp._is_cuda_runtime_error(RuntimeError("CUDA failed with error out of memory"))
    assert vp._is_cuda_runtime_error(RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB"))
    assert not vp._is_cuda_runtime_error(MemoryError("Unable to allocate 3.44 GiB for an array"))
    assert not vp._is_cuda_runtime_error(ValueError("language"))


def test_a_ct2_oom_goes_through_the_retry(monkeypatch):
    _reset(monkeypatch)
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(vp, "get_stt_params", lambda model_id: {})
    tries = []

    def fake(wav, size, *a):
        tries.append(vp._stt_device())
        if len(tries) == 1:
            raise RuntimeError("CUDA failed with error out of memory")
        return [(0.0, 1.0, "ok", None)]
    monkeypatch.setattr(vp, "_transcribe_whisper", fake)
    assert vp._run_transcription("whisper", "small", "x.wav", "ja", None, "en", "whisper-small") == [(0.0, 1.0, "ok", None)]
    assert tries == ["cuda", "cuda"]


def test_the_files_language_is_that_of_the_first_window_with_speech(tmp_path, monkeypatch):
    """The language was pinned from the first window whatever was in it: fifteen
    minutes of a waiting screen, and the guess for silence was the whole file's."""
    class Quiet(_Whisper):
        def transcribe(self, audio, **kw):
            first = int(round(float(audio[0]) * 32768)) - 1
            if first < 150:                                   # nothing said in the first 150 s
                self.calls.append((len(audio) / 16000, kw.get("language")))
                return iter(()), types.SimpleNamespace(language="en", duration=len(audio) / 16000)
            return super().transcribe(audio, **kw)
    model = Quiet()
    monkeypatch.setattr(vp, "_get_whisper_model", lambda size: model)
    monkeypatch.setattr(vp, "_WHISPER_WINDOW_S", 100.0)
    wav = tmp_path / "wait.wav"
    _wav(wav, 420)
    out = vp._transcribe_whisper(str(wav), "small", "auto", None)
    langs = [lang for _, lang in model.calls]
    assert langs[:3] == [None, None, None] and langs[3:] and set(langs[3:]) == {"ja"}    # two silent windows, the first with speech, then pinned
    assert out and out[0][0] >= 200.0
