# Real-time voice quality, found by replaying real meetings against their
# offline subtitles (7.1):
#   - the live prompt named a source language and DeepSeek answered in the wrong
#     one (English lines came out in Japanese) -> only the target is named
#   - names the recognizer writes as sound-alikes -> a meeting vocabulary
#   - the web endpoint called the plain translator (no speech prompt, no retry)
#   - accented speech auto-detected as Hindi / Malay / Spanish -> recognized
#     again in the session's own languages
#   - lines in a script outside a fixed list were dropped
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core import backend
from core.llm import llm_wrapper
from core.pipelines import video_translation_pipeline as vp


# --- the prompt --------------------------------------------------------------
def test_live_prompt_names_the_target_language_only():
    system = llm_wrapper._simple_messages(
        "And to be honest, Oishi-san.", "en", "zh", "前の行です。", speech=True)[0]["content"]
    assert "from en" not in system and "to zh" not in system
    assert system.count("中文 (zh)") >= 3
    assert "Always answer in 中文 (zh)" in system
    assert "Names and terms of this meeting" not in system


def test_live_prompt_carries_the_meeting_vocabulary():
    msgs = llm_wrapper._simple_messages("コピーの責任ではなくて", "ja", "zh", speech=True,
                                        vocabulary="Corpy,  Rahul")
    assert "Names and terms of this meeting, correctly spelled: Corpy, Rahul." in msgs[0]["content"]
    assert msgs[1]["content"] == "コピーの責任ではなくて"       # no context -> the bare line


def test_document_prompt_is_unchanged():
    assert "from ja to zh" in llm_wrapper._simple_messages("こんにちは", "ja", "zh")[0]["content"]
    plain = llm_wrapper._simple_messages("Hello", "auto", "zh")[0]["content"]
    assert "from auto" not in plain and "into 中文 (zh)" in plain


def test_vocabulary_is_cleaned_and_capped():
    assert backend.clean_live_vocabulary("Corpy,  Rahul\nJizhou；楠、 CAN ,corpy,, ") == \
        "Corpy, Rahul, Jizhou, 楠, CAN"
    assert backend.clean_live_vocabulary(None) == ""
    assert len(backend.clean_live_vocabulary(",".join(f"term{i}" for i in range(500)))) <= 400


# --- one line, both frontends -------------------------------------------------
def _fake_translator(monkeypatch, replies):
    calls = []

    def fake(text, src, dst, model, online, key, context="", speech=False, vocabulary=""):
        calls.append({"text": text, "src": src, "context": context, "speech": speech,
                      "vocabulary": vocabulary})
        return replies[min(len(calls), len(replies)) - 1]
    monkeypatch.setattr(llm_wrapper, "translate_text_simple", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def test_live_line_uses_the_speech_prompt_and_vocabulary(monkeypatch):
    calls = _fake_translator(monkeypatch, [("不是Corpy的责任", True, {"total_tokens": 40})])
    monkeypatch.setattr(backend, "live_glossary_hint", lambda *a: "Fixed terminology: A => B")
    out = backend.live_translate_line("コピーの責任ではなくて", "ja", "zh", "m", True, "k",
                                      context="前の行", vocabulary="Corpy")
    assert out == ("不是Corpy的责任", 40)
    assert calls == [{"text": "コピーの責任ではなくて", "src": "ja", "speech": True,
                      "context": "前の行 Fixed terminology: A => B", "vocabulary": "Corpy"}]


def test_live_line_retries_once_then_gives_up(monkeypatch):
    monkeypatch.setattr(backend, "live_glossary_hint", lambda *a: "")
    calls = _fake_translator(monkeypatch, [("rate limited", False, None), ("好的", True, {"total_tokens": 9})])
    assert backend.live_translate_line("はい", None, "zh", "m", True, "k") == ("好的", 9)
    assert len(calls) == 2 and calls[0]["src"] == "auto"
    calls = _fake_translator(monkeypatch, [("boom", False, None)])
    assert backend.live_translate_line("はい", "ja", "zh", "m", True, "k") == ("", 0)
    assert len(calls) == 2


def test_live_line_redoes_a_reply_that_translated_the_context(monkeypatch):
    monkeypatch.setattr(backend, "live_glossary_hint", lambda *a: "")
    calls = _fake_translator(monkeypatch, [("前面几句话也被翻进来了。" * 8, True, {"total_tokens": 90}),
                                           ("是的。", True, {"total_tokens": 20})])
    assert backend.live_translate_line("そうですね。", "ja", "zh", "m", True, "k",
                                       context="前の三行の内容") == ("是的。", 110)
    assert [c["context"] for c in calls] == ["前の三行の内容", ""]


def test_web_endpoints_translate_like_the_desktop(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from webapp import server
    seen = {}

    def fake_line(source, src, dst, model, online, key, context="", vocabulary="", provisional=False):
        seen.update(source=source, context=context, vocabulary=vocabulary, provisional=provisional)
        return "不是Corpy的责任", 33
    monkeypatch.setattr(backend, "live_translate_line", fake_line)
    monkeypatch.setattr(server, "_feature_hidden", lambda name: False)
    client = TestClient(server.app)
    r = client.post("/api/live-translate-text", json={
        "source": "コピーの責任ではなくて", "src_lang": "ja", "dst_lang": "zh",
        "context": "前の行", "vocabulary": "Corpy,\nRahul"})
    assert r.status_code == 200 and r.json() == {"translated": "不是Corpy的责任", "tokens": 33}
    assert seen == {"source": "コピーの責任ではなくて", "context": "前の行", "vocabulary": "Corpy, Rahul",
                    "provisional": False}
    client.post("/api/live-translate-text", json={"source": "コピーの", "dst_lang": "zh", "provisional": True})
    assert seen["provisional"] is True

    stream = {}

    def fake_stream(text, src, dst, model, online, key, usage_sink=None, context="",
                    speech=False, vocabulary=""):
        stream.update(speech=speech, vocabulary=vocabulary)
        yield "好"
    monkeypatch.setattr(llm_wrapper, "translate_text_simple_stream", fake_stream)
    r = client.post("/api/live-translate-stream", json={
        "source": "はい", "dst_lang": "zh", "vocabulary": "Corpy"})
    assert r.status_code == 200 and '"\\u597d"' in r.text
    assert stream == {"speech": True, "vocabulary": "Corpy"}


# --- language guard -----------------------------------------------------------
HINDI = "साइकिन दिस ने वतक्षी फुटारी उत्तो तो करा जना कुते"
JA = "最近ですね、私二人兄弟からじゃなくて三兄弟になった。"


def _session(**counts):
    return dict(counts)


def test_script_checks():
    assert vp._written_in(JA, "ja")
    assert not vp._written_in("オッケー、オッケー、ハードル。", "ja")          # English forced to Japanese
    assert not vp._written_in("Yes, yes. I just want to clarify.", "ja")
    assert vp._written_in("Okay, go ahead, please. Thank you for this.", "en")
    assert not vp._written_in("Saikin disne watakshi futari kara jana kute", "en")   # Japanese forced to English
    assert vp._written_in("我觉得这个方案可以。", "zh") and not vp._written_in(JA, "zh")
    assert vp._written_in("안녕하세요 반갑습니다", "ko")
    assert not vp._written_in("", "ja") and not vp._written_in(JA, "fr")


def test_unexpected_language_is_recognized_again_in_the_sessions_languages():
    mem = _session(ja=40, en=12)
    asked = []

    def redo(lang):
        asked.append(lang)
        return {"ja": (JA, "ja"), "en": ("Saikin disne watakshi futari", "en")}[lang]
    assert vp._guard_language(mem, HINDI, "hi", redo) == (JA, "ja")
    assert asked == ["ja"]                      # the first fit wins, no second pass
    assert mem == {"ja": 41, "en": 12, "~hi": 1}


def test_english_speech_detected_as_malay_falls_through_to_english():
    mem = _session(ja=40, en=12)

    def redo(lang):
        return {"ja": ("オッケー、オッケー、ハードル。", "ja"), "en": ("Okay. How do we do it?", "en")}[lang]
    assert vp._guard_language(mem, "Okey, okey. Kalau itu.", "auto", redo) == ("Okay. How do we do it?", "en")


def test_guard_leaves_alone_what_it_should():
    never = lambda lang: pytest.fail("recognized again")
    # nothing known yet: the first result is all there is — and proves nothing:
    # an accented speaker opening the meeting must not make Hindi a session language
    mem = {}
    for _ in range(5):
        assert vp._guard_language(mem, HINDI, "hi", never) == (HINDI, "hi")
    assert mem == {} and vp._session_langs(mem) == []
    # the big four are never second-guessed (forcing another language loses content)
    mem = _session(ja=40)
    assert vp._guard_language(mem, "Yes, I think so.", "en", never) == ("Yes, I think so.", "en")
    assert vp._guard_language(mem, "嗯。", "zh", never) == ("嗯。", "zh")
    # a language of this session
    mem = _session(ja=40, fr=5)
    assert vp._guard_language(mem, "Bonjour à tous.", "fr", never)[1] == "fr"
    # silence
    mem = _session(ja=40)
    assert vp._guard_language(mem, "", "hi", never) == ("", "hi") and mem == {"ja": 40}


def test_a_real_new_language_joins_the_session():
    mem = _session(ja=40, en=12)

    def redo(lang):      # French forced to Japanese / English: neither is written in it
        return {"ja": ("ボンジュール、コマンタレヴ", "ja"),
                "en": ("Bonjour, comment allez-vous aujourd'hui", "en")}[lang]
    line = "Bonjour, comment allez-vous aujourd'hui ?"
    for _ in range(3):
        assert vp._guard_language(mem, line, "fr", redo) == (line, "fr")
    assert vp._session_langs(mem) == ["ja", "en", "fr"]
    assert vp._guard_language(mem, line, "fr", lambda lang: pytest.fail("recognized again"))[1] == "fr"


def test_a_misdetected_language_never_joins_the_session():
    mem = _session(ja=40, en=12)
    for n in range(30):      # a few come back unusable, most are rescued
        again = ("ジャスコルテン", "ja") if n % 10 == 0 else (JA, "ja")
        vp._guard_language(mem, HINDI, "hi", lambda lang: again if lang == "ja" else ("x", "en"))
    assert "hi" not in vp._session_langs(mem)          # kept 3 times, corrected 27
    # "auto" (a language with no code) is never a session language either
    assert vp._session_langs({"auto": 50, "ja": 3}) == ["ja"]


def test_a_failed_second_pass_keeps_the_first_result():
    mem = _session(ja=40)

    def redo(lang):
        raise RuntimeError("CUDA out of memory")
    assert vp._guard_language(mem, HINDI, "hi", redo) == (HINDI, "hi")


def test_recognize_utterance_runs_the_guard_only_with_a_memory(monkeypatch):
    asked = []

    def fake_qwen(audio, lang, size, sample_rate=16000):
        asked.append(lang)
        return (JA, "ja") if lang == "ja" else (HINDI, "hi")
    monkeypatch.setattr(vp, "get_stt_model", lambda model_id: {"id": model_id, "size": "x"})
    monkeypatch.setattr(vp, "_resolve_stt_engine", lambda model_def: ("qwen3asr", "x"))
    monkeypatch.setattr(vp, "_recognize_qwen", fake_qwen)
    pcm = b"\x10\x00" * 16000
    assert vp.recognize_utterance(pcm, model_id="qwen3-asr-1.7b") == (HINDI, "hi")
    mem = {"ja": 40}
    assert vp.recognize_utterance(pcm, model_id="qwen3-asr-1.7b", lang_memory=mem) == (JA, "ja")
    assert asked == [None, None, "ja"] and mem == {"ja": 41, "~hi": 1}
    # a language the user picked is never second-guessed
    asked.clear()
    vp.recognize_utterance(pcm, src_lang="hi", model_id="qwen3-asr-1.7b", lang_memory=mem)
    assert asked == ["hi"]


def test_every_qwen_language_has_a_code():
    assert vp._QWEN_LANG_CODE["Hindi"] == "hi" and vp._QWEN_LANG_CODE["Spanish"] == "es"
    assert vp._QWEN_LANG_CODE["Chinese"] == "zh" and vp._QWEN_LANG_CODE["Cantonese"] == "zh"


def test_web_keeps_one_language_memory_per_browser_session(monkeypatch):
    pytest.importorskip("httpx")
    import base64
    from fastapi.testclient import TestClient
    from webapp import server
    got = []

    def fake_recognize(pcm, sample_rate=16000, model_id=None, lang_memory=None):
        got.append(lang_memory)
        if lang_memory is not None:
            lang_memory["ja"] = lang_memory.get("ja", 0) + 1
        return "はい", "ja"
    mode = {"commit": "final"}
    monkeypatch.setattr(vp, "recognize_utterance", fake_recognize)
    monkeypatch.setattr(vp, "live_preview_model", lambda: None)
    monkeypatch.setattr(vp, "live_commit_mode", lambda: mode["commit"])
    monkeypatch.setattr(server, "realtime_voice_available", lambda: True)
    monkeypatch.setattr(server, "_feature_hidden", lambda name: False)
    server._LIVE_LANGS.clear()
    audio = base64.b64encode(b"\x10\x00" * 1600).decode()
    a, b = TestClient(server.app), TestClient(server.app)
    a.post("/api/live-recognize", json={"audio_b64": audio, "final": True})
    a.post("/api/live-recognize", json={"audio_b64": audio, "final": True})
    a.post("/api/live-recognize", json={"audio_b64": audio, "final": False})   # a partial: no memory
    b.post("/api/live-recognize", json={"audio_b64": audio, "final": True})
    assert got[0] is got[1] and got[2] is None and got[3] is not got[0]
    assert sorted(m["ja"] for m in server._LIVE_LANGS.values()) == [1, 2]
    # stream mode commits lines from partials: a final recognized again in another
    # language would not line up with them, so no guard there
    mode["commit"] = "stream"
    a.post("/api/live-recognize", json={"audio_b64": audio, "final": True})
    assert got[4] is None


# --- no script is dropped ------------------------------------------------------
@pytest.mark.parametrize("line", ["साइकिन दिस ने", "مرحبا بالجميع", "Γεια σας", "こんにちは", "ok", "１２"])
def test_any_script_is_a_line(line):
    from qt_app.live_page import LivePage
    assert LivePage._WORDLIKE.search(line) and vp._WORDLIKE.search(line)


@pytest.mark.parametrize("noise", ["。", "?", "…", "__", "😊"])
def test_bare_punctuation_is_not_a_line(noise):
    from qt_app.live_page import LivePage
    assert not LivePage._WORDLIKE.search(noise) and not vp._WORDLIKE.search(noise)


def test_web_twin_accepts_any_script():
    import re
    import subprocess
    js = open(os.path.join(REPO_ROOT, "webapp", "static", "app.js"), encoding="utf-8").read()
    wordlike = re.search(r"const WORDLIKE = (/.+/u);", js).group(1)
    code = (f"const W = {wordlike};"
            "console.log(['साइकिन', 'مرحبا', 'こんにちは', 'ok', '。', '?', '😊'].map((s) => +W.test(s)).join(''))")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert out.stdout.strip() == "1111000", out.stderr


def test_offline_subtitles_use_the_same_guard(monkeypatch, tmp_path):
    import wave
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(bytes([16, 0]) * 16000 * 8)

    class Result:
        def __init__(self, text, language):
            self.text, self.language = text, language

    class Model:
        def transcribe(self, batch, **kw):
            assert "language" not in kw            # auto-detect
            return [Result("はい、そうですね。", "Japanese")] * 3 + [Result(HINDI, "Hindi")]
    monkeypatch.setattr(vp, "_get_qwen", lambda name: Model())
    monkeypatch.setattr(vp, "_segment_speech",
                        lambda *a, **k: [(i * 2000.0, i * 2000.0 + 1500.0) for i in range(4)])
    monkeypatch.setattr(vp, "_recognize_qwen", lambda audio, lang, name, sr=16000: (JA, "ja"))
    cues = vp._transcribe_qwen(str(wav), "x", None, None)
    assert [text for _, _, text in cues] == ["はい、そうですね。"] * 3 + [JA]


def test_live_line_redoes_a_reply_that_stayed_japanese(monkeypatch):
    monkeypatch.setattr(backend, "live_glossary_hint", lambda *a: "")
    line = "ただし急ブレーキや急ハンドルで避けなければ。"
    calls = _fake_translator(monkeypatch, [(line[:-1] + "なりません。", True, {"total_tokens": 50}),
                                           ("不过，必须靠急刹车或急打方向来避让。", True, {"total_tokens": 48})])
    assert backend.live_translate_line(line, "ja", "zh", "m", True, "k", context="前の行") == \
        ("不过，必须靠急刹车或急打方向来避让。", 98)
    assert [c["context"] for c in calls] == ["前の行", ""]
    # into Japanese, kana is the answer; a name left in kana is not a Japanese reply
    calls = _fake_translator(monkeypatch, [("はい、そうです。", True, None)])
    assert backend.live_translate_line("Yes, that's right.", "en", "ja", "m", True, "k")[0] == "はい、そうです。"
    calls = _fake_translator(monkeypatch, [("我想请大重さん安排一次会议。", True, None)])
    backend.live_translate_line("おおしげさんに会議を設定してほしい。", "ja", "zh", "m", True, "k")
    assert len(calls) == 1


def test_qt_guards_only_whole_segment_recognition(monkeypatch):
    """Final mode hands the session memory to its final recognitions; stream
    mode (lines committed from partials) and previews never do."""
    import qt_app.live_page as lp
    made = []

    class Worker:
        def __init__(self, pcm, rate, is_final, parent=None, model_id=None, lang_memory=None):
            made.append((is_final, lang_memory))
            self.done = self.finished = type("S", (), {"connect": lambda self, f: None})()

        def start(self):
            pass
    monkeypatch.setattr(lp, "LiveRecognizeWorker", Worker)
    page = type("P", (), {})()
    page._recog_busy, page._recog_pending, page._local_workers = False, [], []
    page._lang_memory = {"ja": 9}
    page._on_recognized_stream = page._retire_local = lambda *a: None
    page._live_preview = lambda: False
    page._is_listening = lambda: True
    for final_mode in (True, False):
        page._final_mode = final_mode
        for is_final in (True, False):
            page._recog_busy = False
            lp.LivePage._dispatch_recognize(page, bytes(320), is_final)
    assert made == [(True, {"ja": 9}), (False, None), (True, None), (False, None)]


# --- review fixes --------------------------------------------------------------
def test_a_language_without_a_code_can_still_join_the_session(monkeypatch):
    """Qwen names 30 languages, the app has codes for 19: Swedish, Malay, Greek...
    came back as "auto", which can never be a session language — every segment of
    a Swedish meeting with a little English in it was recognized two more times."""
    assert vp._qwen_lang_code("Swedish") == "swedish" and vp._qwen_lang_code("Japanese") == "ja"
    assert vp._qwen_lang_code(None) == "auto" and vp._qwen_lang_code("", "ja") == "ja"
    mem, asked = _session(en=12), []

    def redo(lang):
        asked.append(lang)
        return "Hej allihopa, vi börjar mötet nu", lang       # Swedish stays Swedish
    line = "Hej allihopa, vi börjar mötet nu."
    for _ in range(6):
        assert vp._guard_language(mem, line, "swedish", redo) == (line, "swedish")
    assert len(asked) == 3 and vp._session_langs(mem) == ["en", "swedish"]


def test_guard_asks_again_only_in_languages_it_can_check():
    """A second recognition in French or Spanish cannot be verified, so it is not
    made at all (it only cost time); nothing to check against, nothing counted."""
    mem = _session(fr=40, es=9)
    assert vp._guard_language(mem, HINDI, "hi", lambda lang: pytest.fail("recognized again")) == (HINDI, "hi")
    assert "hi" not in mem
    mem, asked = _session(fr=40, es=9, en=5), []
    vp._guard_language(mem, HINDI, "hi", lambda lang: (asked.append(lang), ("x", lang))[1])
    assert asked == []                 # English is the third language: only the two main ones are tried
    mem, asked = _session(fr=40, en=9), []
    vp._guard_language(mem, HINDI, "hi", lambda lang: (asked.append(lang), ("नमस्ते", lang))[1])
    assert asked == ["en"] and mem["hi"] == 1


def test_qt_preview_does_not_wait_for_a_final_of_another_model(monkeypatch):
    """One lock for every recognition made the fast preview model wait for each
    slow final decode — the live line froze exactly when it should move."""
    import threading
    import time
    from qt_app import live_worker
    assert live_worker.stt_lock("qwen3-asr-1.7b") is live_worker.stt_lock("qwen3-asr-1.7b")
    assert live_worker.stt_lock("qwen3-asr-1.7b") is not live_worker.stt_lock("sensevoice-small")
    log, t0 = [], time.time()

    def fake(pcm, sample_rate=16000, model_id=None, lang_memory=None):
        log.append((model_id or "final", "start", time.time() - t0))
        time.sleep(0.02 if model_id else 0.5)
        log.append((model_id or "final", "end", time.time() - t0))
        return "x", "ja"
    monkeypatch.setattr(vp, "recognize_utterance", fake)
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "qwen3-asr-1.7b")
    final = live_worker.LiveRecognizeWorker(bytes(2), 16000, True)
    preview = live_worker.LiveRecognizeWorker(bytes(2), 16000, False, model_id="sensevoice-small")
    a = threading.Thread(target=final.run)
    a.start()
    time.sleep(0.1)
    preview.run()
    a.join()
    ended = {name: t for name, what, t in log if what == "end"}
    assert ended["sensevoice-small"] < ended["final"]
    # the same model is still one at a time
    log.clear()
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "sensevoice-small")
    a = threading.Thread(target=live_worker.LiveRecognizeWorker(bytes(2), 16000, True).run)
    a.start()
    time.sleep(0.1)
    preview.run()
    a.join()
    assert [what for _, what, _ in log] == ["start", "end", "start", "end"]


def test_provisional_translations_leave_room_for_the_final_ones(monkeypatch):
    """About 38 provisional + 10 final requests a minute fill a 50-60 rpm window
    (Claude, Gemini, any model without an "rpm"); the finals then waited behind
    the provisionals in the limiter. A provisional one is skipped instead."""
    from core.llm import online_translation as ot
    limiter = ot._RpmLimiter()
    limiter.limit = 60
    monkeypatch.setattr(ot, "_rpm_limiter", limiter)
    monkeypatch.setattr(ot, "load_model_config", lambda model: {"rpm": 50} if model == "claude" else {})
    monkeypatch.setattr(backend, "live_glossary_hint", lambda *a: "")
    calls = _fake_translator(monkeypatch, [("好的", True, {"total_tokens": 5})])
    for _ in range(29):
        limiter.wait(key="claude", limit_override=50)
    assert backend.live_translate_line("はい", "ja", "zh", "claude", True, "k", provisional=True) == ("好的", 5)
    limiter.wait(key="claude", limit_override=50)                       # 30 of 50: 60% full
    assert backend.live_translate_line("はい", "ja", "zh", "claude", True, "k", provisional=True) == ("", 0)
    assert backend.live_translate_line("はい", "ja", "zh", "claude", True, "k") == ("好的", 5)   # a final goes
    assert len(calls) == 2
    # a model without its own rpm shares the global window (default 60)
    for _ in range(36):
        limiter.wait()
    assert not ot.rpm_room("some-local-model", 0.6) and ot.rpm_room("some-local-model", 1.0)
    limiter.limit = None                                               # "rpm_limit": 0 = unlimited
    assert ot.rpm_room("some-local-model", 0.6)


def test_glossary_hint_survives_a_language_without_a_column(tmp_path, monkeypatch):
    """Spanish used to be "auto" (first non-target column); now it is "es", and a
    glossary without an es column must not silently stop working."""
    path = tmp_path / "g.csv"
    path.write_text("en,zh" + chr(10) + "Acme,艾克美" + chr(10), encoding="utf-8")
    monkeypatch.setattr(backend, "glossary_path", lambda name: str(path))
    monkeypatch.setattr(backend, "get_config", lambda key, default=None: "g" if key == "default_glossary" else default)
    backend._live_gloss_cache["key"] = None
    assert "Acme => 艾克美" in backend.live_glossary_hint("Hola, soy de Acme.", "es", "zh")
    backend._live_gloss_cache["key"] = None
