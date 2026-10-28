"""Real-time voice 'final' commit mode (slow autoregressive STT like Qwen3-ASR):
partials only preview; the COMPLETE utterance recognition is committed, split
only at sentence ends — no text from half-heard audio, no mid-word cuts."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_live_stream_race import _page   # real methods, captured commits
from qt_app.live_page import LivePage


def _final_page():
    p, _lp = _page()
    p._final_mode = True
    p._split_sentences = LivePage._split_sentences
    p._cell, p._SENT_MIN_CELLS = LivePage._cell, LivePage._SENT_MIN_CELLS
    p.interim = []
    p._set_caption_interim = lambda t: p.interim.append(t)
    return p


def test_partials_commit_nothing_final_commits_whole_utterance():
    p = _final_page()
    # Two agreeing partials would commit in stream mode; in final mode they don't.
    p._on_recognized_stream("あのいやえっと一応僕たち一が", "ja", False)
    p._on_recognized_stream("あのいやえっと一応僕たち一が最高って", "ja", False)
    assert p.commits == [] and p.interim[-1].endswith("最高って")
    # The complete recognition corrects it ("一月以降") and is what gets committed.
    p._on_recognized_stream("ああそうですか。あのいやえっと一応僕たち一月以降って言ってたので、"
                            "二層構成だと思いますね。", "ja", True)
    assert p.commits == ["ああそうですか。",
                         "あのいやえっと一応僕たち一月以降って言ってたので、二層構成だと思いますね。"]


def test_unpunctuated_run_is_not_cut_mid_word():
    p = _final_page()
    run = "でえっとやっぱり一つはえっと良くしていくみたいなで機能増やしていくみたいなでえっとそのえっとが画像を入れる"
    p._on_recognized_stream(run, "ja", True)
    assert p.commits == [run]          # stream mode hard-cut this at 88 cells


def test_split_sentences_keeps_decimals_and_latin_spacing():
    s = LivePage._split_sentences
    assert s("The price is 3.5 dollars. OK.") == ["The price is 3.5 dollars. OK."]
    assert s("はい。") == ["はい。"]


def test_recording_compressed_and_history_repointed(tmp_path):
    """A live session's WAV becomes a much smaller .ogg; history follows it."""
    import shutil, wave
    from datetime import datetime
    from core.optional_modules import ffmpeg_exe
    if not ffmpeg_exe():
        import pytest; pytest.skip("ffmpeg not installed")
    from core.translation_history import (save_live_session, compress_live_recording,
                                          TranslationHistoryManager)
    wav = tmp_path / "rec.wav"
    with wave.open(str(wav), "wb") as w:          # 20 s of a 440 Hz tone
        import math
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(int(8000 * math.sin(i / 16000 * 2 * math.pi * 440)).to_bytes(2, "little", signed=True)
                               for i in range(16000 * 20)))
    rec = save_live_session(["[12:00:00] こんにちは"], ["[12:00:00] 你好"], "Auto", "中文", "m", True,
                            str(tmp_path), str(tmp_path), start_time=datetime.now(), audio_path=str(wav))
    old = rec["log_file_path"]
    wav_size = os.path.getsize(old)                # ~640 KB
    new = compress_live_recording(rec["id"], str(tmp_path))
    assert new and new.endswith(".ogg") and not os.path.exists(old)
    assert os.path.getsize(new) < wav_size / 5
    assert TranslationHistoryManager(log_dir=str(tmp_path)).get_record_by_id(rec["id"])["log_file_path"] == new


def test_translation_waits_for_a_complete_sentence():
    """Stream mode shows each line at once but translates per COMPLETE sentence:
    a line cut mid-sentence is held and translated with its continuation."""
    import types
    P = LivePage
    page = types.SimpleNamespace(_SENT_END=P._SENT_END, _HOLD_MAX_CHARS=P._HOLD_MAX_CHARS,
                                 _tr_hold=[], rows={}, sent=[])
    page._set_out_row = lambda k, t: page.rows.__setitem__(k, t)
    page._start_line_translation = lambda k, s: page.sent.append((k, s))
    for name in ("_queue_translation", "_flush_tr_hold"):
        setattr(page, name, types.MethodType(getattr(P, name), page))
    page._queue_translation("k1", "今回作ったモデルをいろんなシーンに")
    assert page.sent == [] and page.rows["k1"] == "…"
    page._queue_translation("k2", "対応させていくのかっていう方法もありますし。")
    assert page.sent == [("k2", "今回作ったモデルをいろんなシーンに対応させていくのかっていう方法もありますし。")]
    assert page.rows["k1"] == "↓"
    page._queue_translation("k3", "the meeting starts")
    page._queue_translation("k4", "at nine", final_piece=True)   # utterance ended
    assert page.sent[-1] == ("k4", "the meeting starts at nine")
    page._queue_translation("k5", "最後の話")
    page._flush_tr_hold()                                          # stop
    assert page.sent[-1] == ("k5", "最後の話")
