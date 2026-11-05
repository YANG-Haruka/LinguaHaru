"""Real-time voice 'final' commit mode (slow autoregressive STT like Qwen3-ASR):
partials only preview; the COMPLETE utterance recognition is committed, split
only at sentence ends — no text from half-heard audio, no mid-word cuts."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_live_stream_race import _page   # real methods, captured commits
from qt_app.live_page import LivePage


def _final_page():
    p, _lp = _page()
    p._final_mode = True
    p._split_sentences = LivePage._split_sentences
    p._split_clause_tail, p._line_end = LivePage._split_clause_tail, LivePage._line_end
    p._age_tr_hold = types.MethodType(LivePage._age_tr_hold, p)
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


def _live_recording(tmp_path, seconds=20):
    """A saved live session whose recording is a WAV (a 440 Hz tone)."""
    import math, wave
    from datetime import datetime
    from core.translation_history import save_live_session
    wav = tmp_path / "rec.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(int(8000 * math.sin(i / 16000 * 2 * math.pi * 440)).to_bytes(2, "little", signed=True)
                               for i in range(16000 * seconds)))
    return save_live_session(["[12:00:00] こんにちは"], ["[12:00:00] 你好"], "Auto", "中文", "m", True,
                             str(tmp_path), str(tmp_path), start_time=datetime.now(), audio_path=str(wav))


def test_recording_compressed_and_history_repointed(tmp_path):
    """A live session's WAV becomes a much smaller .ogg; history follows it."""
    from core.optional_modules import ffmpeg_exe
    if not ffmpeg_exe():
        import pytest; pytest.skip("ffmpeg not installed")
    from core.translation_history import compress_live_recording, TranslationHistoryManager
    rec = _live_recording(tmp_path)
    old = rec["log_file_path"]
    wav_size = os.path.getsize(old)                # ~640 KB
    new = compress_live_recording(rec["id"], str(tmp_path))
    assert new and new.endswith(".ogg") and not os.path.exists(old)
    assert os.path.getsize(new) < wav_size / 5
    assert TranslationHistoryManager(log_dir=str(tmp_path)).get_record_by_id(rec["id"])["log_file_path"] == new


def test_leftover_recording_compressed_at_next_start(tmp_path):
    """The app was closed while ffmpeg compressed a recording: ffmpeg finished on
    its own, but the thread that renames its .part and repoints history died
    with the app — WAV + stale .part stay. The next start finishes the job."""
    import glob
    from core.optional_modules import ffmpeg_exe
    if not ffmpeg_exe():
        import pytest; pytest.skip("ffmpeg not installed")
    from core.translation_history import compress_leftover_recordings, TranslationHistoryManager
    rec = _live_recording(tmp_path)
    wav = rec["log_file_path"]
    wav_size = os.path.getsize(wav)
    with open(wav[:-4] + ".ogg.part", "wb") as f:          # the closed app's leftover
        f.write(b"OggS" + b"\0" * 1000)
    compress_leftover_recordings(str(tmp_path))
    new = TranslationHistoryManager(log_dir=str(tmp_path)).get_record_by_id(rec["id"])["log_file_path"]
    assert new == wav[:-4] + ".ogg" and os.path.getsize(new) < wav_size / 5
    assert not os.path.exists(wav) and not glob.glob(str(tmp_path / "*.part"))


def test_failed_compression_never_deletes_a_finished_recording(tmp_path, monkeypatch):
    """Two attempts on one recording (a restart while the closed app's ffmpeg
    still runs, or Qt and its LAN web server both healing): one that fails must
    not delete the .ogg the other finished, once that one removed the WAV."""
    import glob, subprocess
    from core import optional_modules
    from core.translation_history import compress_live_recording, TranslationHistoryManager
    rec = _live_recording(tmp_path, seconds=1)
    wav = rec["log_file_path"]
    ogg = wav[:-4] + ".ogg"
    mgr = TranslationHistoryManager(log_dir=str(tmp_path))

    def other_attempt_wins(cmd, **kw):
        with open(cmd[-1], "wb") as f:                   # our partial output
            f.write(b"OggS")
        with open(ogg, "wb") as f:                       # the other attempt: done,
            f.write(b"finished")
        mgr.add_record({**rec, "log_file_path": ogg})    # history repointed,
        os.remove(wav)                                   # WAV deleted
        raise subprocess.CalledProcessError(1, cmd)      # -> ours fails
    monkeypatch.setattr(optional_modules, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", other_attempt_wins)
    assert compress_live_recording(rec["id"], str(tmp_path)) is None
    assert os.path.exists(ogg) and mgr.get_record_by_id(rec["id"])["log_file_path"] == ogg
    assert not glob.glob(str(tmp_path / "*.part"))


def test_translation_waits_for_a_complete_sentence():
    """Stream mode shows each line at once but translates per COMPLETE sentence:
    a line cut mid-sentence is held and translated with its continuation."""
    import types
    P = LivePage
    page = types.SimpleNamespace(_HOLD_MAX_CHARS=P._HOLD_MAX_CHARS, _line_end=P._line_end,
                                 _join_held=P._join_held, _tr_hold=[], _tr_hold_seen=0,
                                 rows={}, sent=[])
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
    page._queue_translation("k4", "at nine")
    page._flush_tr_hold()                         # the silence reached the full hang
    assert page.sent[-1] == ("k4", "the meeting starts at nine")
    page._queue_translation("k5", "最後の話")
    page._flush_tr_hold()                                          # stop
    assert page.sent[-1] == ("k5", "最後の話")
    # The STT ends ITS audio with "。" even mid-sentence (real meeting cuts):
    # a particle / te-form / filler ending is still unfinished -> held.
    page.sent.clear()
    page._queue_translation("k6", "ちなみにそこってあの。")
    page._queue_translation("k7", "前にいただいた資料の中で。")
    assert page.sent == [] and page.rows["k7"] == "…"
    page._queue_translation("k8", "十月十日って書いてありました。")
    assert page.sent == [("k8", "ちなみにそこってあの。前にいただいた資料の中で。十月十日って書いてありました。")]
    for k, s in (("k9", "それで大丈夫です。"), ("k10", "わかりました。"), ("k11", "どうしますか？"),
                 ("k12", "これは決まっていない。"), ("k13", "那我们明天再聊吧。")):
        page._queue_translation(k, s)              # real sentence ends translate alone
        assert page.sent[-1] == (k, s), s
    # A trailing filler is a fast speaker running on, not an unfinished line
    # (the top cause of 20 s+ holds in a real meeting): judge what precedes it.
    page._queue_translation("k14", "要件はもう決まっていますけど、えっと。")
    assert page.sent[-1] == ("k14", "要件はもう決まっていますけど、えっと。")
    page._queue_translation("k15", "三菱自動車からのえっと。")   # "からの" still dangles
    assert page.sent[-1][0] == "k14" and page.rows["k15"] == "…"
    # Stream mode has no segments: a held line waits for at most two more lines.
    page._queue_translation("k16", "その秘密保持のレベルと")
    assert page.sent[-1][0] == "k14"
    page._queue_translation("k17", "ネクストムーブとの")
    assert page.sent[-1] == ("k17", "三菱自動車からのえっと。その秘密保持のレベルとネクストムーブとの")
    # Final mode waits for the next SEGMENT instead (_age_tr_hold), not a line count.
    page._final_mode = True
    for k, s in (("k18", "その秘密保持のレベルと"), ("k19", "ネクストムーブとの"), ("k20", "三菱自動車からの")):
        page._queue_translation(k, s)
    assert page.sent[-1][0] == "k17" and len(page._tr_hold) == 3


def test_line_end_reads_the_last_words_not_the_stt_full_stop():
    """The STT ends every segment with "。" — even mid-sentence — so how a line
    ends is read from its last words (twin of web lineEnd, tests/web_live_hold.mjs)."""
    cases = {
        "それで大丈夫です。": "S", "どうしますか？": "S", "わかりました。": "S",
        "要件はもう決まっていますけど、えっと。": "C",     # the trailing filler is dropped
        "そちらで対応していただいて。": "C",                 # te-form: a whole clause
        "今日追加でいきなり来たんですが。": "C",
        "前にいただいた資料の中で。": "M",                   # particle: mid-phrase
        "三菱自動車からのえっと。": "M",
        "ちなみにそこって。": "M",                          # topic って, not te-form
        "一応ご留意。": "N",                                # bare noun: cut off
        "the meeting starts at the.": "M", "We can decide it later.": "S",
        "那我们明天再聊吧。": "S", "我觉得这个方案": "M",
        "えっとあの。": "F",
    }
    for s, want in cases.items():
        assert LivePage._line_end(s) == want, (s, LivePage._line_end(s), want)


def test_split_clause_tail_translates_up_to_the_last_clause_end():
    s = LivePage._split_clause_tail
    assert s(["はい。", "お知らせがあるかもしれないですけど、えっと三菱自動車からの。"]) == \
        ["はい。", "お知らせがあるかもしれないですけど、", "えっと三菱自動車からの。"]
    assert s(["決まっています、はい。"]) == ["決まっています、はい。"]            # finished: as is
    assert s(["三菱自動車からコピーに対しての、"]) == ["三菱自動車からコピーに対しての、"]  # no clause end


def _translating_page():
    """Final-mode page with the REAL commit -> hold -> translate path; records
    what would be sent to the LLM."""
    import types
    P = LivePage
    p = _final_page()
    for name in ("_commit_stream_sentence", "_queue_translation", "_age_tr_hold", "_flush_tr_hold",
                 "_request_provisional", "_on_provisional", "_final_replaces_provisional",
                 "_on_translated_stream", "_clear_caption_prov"):
        setattr(p, name, types.MethodType(getattr(P, name), p))
    for name in ("_split_clause_tail", "_line_end", "_join_held", "_HOLD_MAX_CHARS",
                 "_TAIL_ENDINGS", "_BACKCHANNELS", "_SENT_END"):
        setattr(p, name, getattr(P, name))
    p._ctx_history, p._held, p._out_rows, p._tr_hold, p._tr_hold_seen = [], "", [], [], 0
    p._prov_seq, p._prov_text, p._prov_key, p._prov_clear = 0, "", None, {}
    p._session_tokens = 0
    p.input_text = types.SimpleNamespace(insertPlainText=lambda s: None, ensureCursorVisible=lambda: None)
    p._render_output = lambda: None
    p.rows, p.sent, p.prov_asked = {}, [], []
    p._set_out_row = lambda k, t: p.rows.__setitem__(k, t)
    p._start_line_translation = lambda k, s: (p.sent.append(s), p._ctx_history.append(s))
    p._start_provisional = lambda seq, k, s: p.prov_asked.append((seq, k, s))
    return p


def test_held_text_gets_a_provisional_translation_replaced_by_the_whole_one():
    """Two-layer captions: held text is translated provisionally at once (dim,
    "…"); the whole sentence's translation replaces it; a provisional result
    that comes back after that is stale and dropped."""
    p = _translating_page()
    caps = []
    p._push_caption = lambda source=None, translated=None, key=None, provisional=False: (
        caps.append((key, translated, provisional)) if translated else None)
    p._caption_bar = types.SimpleNamespace(clear_provisional=lambda k: caps.append((k, None, "cleared")))
    p._on_recognized_stream("三菱自動車からコピーに対してのその秘密保持のレベルと、えっとネクストムーブとの", "ja", True)
    assert p.sent == [] and len(p.prov_asked) == 1
    seq, key1, text = p.prov_asked[0]
    assert key1 == p._out_rows[0][0] and text.endswith("ネクストムーブとの")
    p._on_recognized_stream("機密保持", "ja", False)          # a preview: nothing new held
    assert len(p.prov_asked) == 1
    p._on_provisional(seq, key1, "来自三菱汽车的……与NextMove的", 30)
    assert p.rows[key1] == "… 来自三菱汽车的……与NextMove的"
    assert caps[-1] == (key1, "来自三菱汽车的……与NextMove的", True)
    p._on_recognized_stream("機密保持NDAを巻き直しています。", "ja", True)   # the sentence ends
    assert len(p.sent) == 1 and p.rows[key1] == "↓" and len(p.prov_asked) == 1
    p._on_provisional(seq, key1, "late", 5)                # came back after the flush: stale
    assert p.rows[key1] == "↓"
    key2 = p._out_rows[1][0]
    p._on_translated_stream(key2, "整句译文", 50)
    assert caps[-2:] == [(key2, "整句译文", False), (key1, None, "cleared")]
    assert p._session_tokens == 85                          # provisional calls cost too
    # Stream mode: the hold grows -> the newest provisional replaces the older one.
    p._final_mode = False
    p._queue_translation("s1", "その秘密保持のレベルと")
    p._request_provisional()
    p._on_provisional(p.prov_asked[-1][0], "s1", "那个保密级别", 0)
    p._queue_translation("s2", "ネクストムーブとの")
    p._request_provisional()
    assert p.prov_asked[-1][1:] == ("s2", "その秘密保持のレベルとネクストムーブとの")
    p._on_provisional(p.prov_asked[-1][0], "s2", "那个保密级别与NextMove的", 0)
    assert p.rows["s1"] == "…" and p.rows["s2"] == "… 那个保密级别与NextMove的"
    assert ("s1", None, "cleared") in caps


def test_held_line_waits_for_the_next_segment_not_a_clock():
    """A sentence longer than one segment (a fast speaker, 10 s+ without a pause)
    is translated whole: its first part waits for the next segment however long
    that takes. Two segments without any sentence/clause end -> sent anyway; a
    segment cut after a clause end sends the clause at once, only the tail waits."""
    p = _translating_page()
    p._on_recognized_stream("三菱自動車からコピーに対してのその秘密保持のレベルと、えっとネクストムーブとの", "ja", True)
    assert p.sent == []
    p._on_recognized_stream("機密保持NDAを巻き直しています。", "ja", True)
    assert p.sent == ["三菱自動車からコピーに対してのその秘密保持のレベルと、えっとネクストムーブとの"
                      "機密保持NDAを巻き直しています。"]
    p.sent.clear()
    p._on_recognized_stream("その秘密保持のレベルと", "ja", True)
    assert p.sent == []
    p._on_recognized_stream("ネクストムーブとの", "ja", True)
    assert p.sent == ["その秘密保持のレベルとネクストムーブとの"]
    p.sent.clear()
    p._on_recognized_stream("お知らせがあるかもしれないですけど、えっと三菱自動車からのえっと。", "ja", True)
    assert p.sent == ["お知らせがあるかもしれないですけど、"]
    p._on_recognized_stream("案件である。", "ja", True)
    assert p.sent[-1] == "えっと三菱自動車からのえっと。案件である。"


def test_live_default_model_follows_hardware_without_surprise_downloads(monkeypatch):
    """Never-picked real-time model: Qwen3-ASR 1.7B where it keeps up (MLX, or an
    NVIDIA GPU >= 8 GB with CUDA torch), else SenseVoice — and an install that
    already has SenseVoice but not Qwen keeps SenseVoice (no 4.7 GB download)."""
    from core import model_store
    from core.pipelines import video_translation_pipeline as v
    monkeypatch.setattr(v, "_mlx_qwen_ok", lambda: False)
    disk = {}
    monkeypatch.setattr(model_store, "find_model_dirs", lambda subs: dict(disk))

    def pick(hw, on_disk):
        v._live_default.update(t=-1e9, id=None, hw=hw)
        disk.clear(); disk.update({k: ["x"] for k in on_disk})
        return v.live_stt_default()
    assert pick(True, []) == "qwen3-asr-1.7b"                       # fresh install
    assert pick(True, ["qwen3-asr-1.7b", "sensevoicesmall"]) == "qwen3-asr-1.7b"
    assert pick(True, ["sensevoicesmall"]) == "sensevoice-small"     # keep, no download
    assert pick(False, []) == "sensevoice-small"                     # CPU / small GPU
    from core import sysmon
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", lambda _p: "2.7.0+cu126")
    monkeypatch.setattr(sysmon, "_gpu_mem", lambda: (100, 8188))
    assert v._live_qwen_hw_ok()
    monkeypatch.setattr(sysmon, "_gpu_mem", lambda: (100, 6144))
    assert not v._live_qwen_hw_ok()                                  # 6 GB card
    monkeypatch.setattr(sysmon, "_gpu_mem", lambda: (100, 24564))
    monkeypatch.setattr(importlib.metadata, "version", lambda _p: "2.7.0+cpu")
    assert not v._live_qwen_hw_ok()                                  # CPU torch build
    v._live_default.update(t=-1e9, id=None, hw=None)
