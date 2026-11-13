"""Real-time voice over a day or two: what must not depend on the session's length."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

import qt_app.live_page as lp


def _page():
    page = lp.LivePage(lang="zh")
    page._session_tokens, page._stream_text, page._out_rows = 0, {}, []
    return page


def test_a_new_line_goes_to_the_end_whatever_the_user_selected():
    """insertPlainText goes where the user's cursor is: with text selected in the
    pane (to copy it) the next recognized line REPLACED the selection, and after
    a single click every later line was inserted at that spot. The transcript is
    saved from the panes."""
    page = _page()
    for i in range(6):
        lp.LivePage._append_line(page.input_text, f"[10:00:0{i}] line {i}")
    c = page.input_text.textCursor()
    c.setPosition(5)
    c.setPosition(40, QTextCursor.KeepAnchor)           # the user selects across two lines
    page.input_text.setTextCursor(c)
    lp.LivePage._append_line(page.input_text, "[10:00:07] said\nlater")
    lines = page.input_text.toPlainText().splitlines()
    assert lines == [f"[10:00:0{i}] line {i}" for i in range(6)] + ["[10:00:07] said later"]
    assert page.input_text.textCursor().hasSelection()   # ... and the selection is the user's still
    assert not page.input_text.isUndoRedoEnabled() and not page.output_text.isUndoRedoEnabled()


def test_the_translation_pane_changes_one_row_at_a_time():
    """Every line and every streamed token rebuilt the whole pane (380 ms at the
    20,000 lines of a two-day session). One row is one text block now; what is
    shown must equal a full rebuild at every step."""
    page = _page()
    full = lambda: "".join(lp.LivePage._out_line(r) + "\n" for r in page._out_rows)
    calls = []
    real = page._render_output
    page._render_output = lambda: (calls.append(1), real())[1]
    for i in range(40):
        page._out_rows.append([f"10:00:{i:02d}#{i}", ""])
        page._draw_out_row(i)
        if i % 3 == 0:
            page._set_out_row(f"10:00:{i:02d}#{i}", "…")
        if i >= 5:                                        # replies come back out of order
            page._set_out_row(f"10:00:{i - 5:02d}#{i - 5}", f"译文 {i - 5}\n第二行")
        assert page.output_text.toPlainText() == full(), i
    assert calls == []                                    # never the whole pane
    assert "[10:00:03] 译文 3 第二行" in page.output_text.toPlainText()   # one line a row
    page._set_out_row("no such key", "x")
    assert page.output_text.toPlainText() == full()
    page.output_text.setPlainText("out of step")          # pane and rows disagree: rebuilt
    page._set_out_row("10:00:01#1", "again")
    assert calls == [1] and page.output_text.toPlainText() == full()
    # the cost of a row does not grow with the session
    page._out_rows = [[f"k#{i}", "译文" * 12] for i in range(20000)]
    real()
    t = time.perf_counter()
    for i in range(200):
        page._out_rows.append([f"n#{i}", ""])
        page._draw_out_row(len(page._out_rows) - 1)
        page._set_out_row(f"n#{i}", "好的，我想那样没问题。")
    per_row = (time.perf_counter() - t) / 200
    assert per_row < 0.02, per_row                        # was 0.38 s a rebuild at this size
    assert page.output_text.toPlainText() == full()


def test_the_recording_goes_past_four_gigabytes(tmp_path):
    """Python's wave raises when the data passes 4 GB (37 hours at 16 kHz): the
    recording of a two-day session stopped there without a word."""
    import wave
    path = str(tmp_path / "rec.wav")
    w = lp._WavWriter(path, 16000)
    w.writeframes(bytes(32000))
    with open(path, "rb") as f:                           # valid before it is closed (the app may die)
        head = f.read(44)
    assert struct.unpack("<I", head[40:44])[0] == 32000 and struct.unpack("<I", head[4:8])[0] == 36 + 32000
    w.writeframes(bytes(32000))
    w.close()
    with wave.open(path, "rb") as r:
        assert (r.getnchannels(), r.getsampwidth(), r.getframerate(), r.getnframes()) == (1, 2, 16000, 32000)
    w = lp._WavWriter(path, 16000)
    w._n = 0xFFFFFFFF - 100                               # as if 37 hours had been written
    w.writeframes(bytes(32000))                           # wave: struct.error here
    w.close()
    with open(path, "rb") as f:
        head = f.read(44)
    assert struct.unpack("<I", head[4:8])[0] == struct.unpack("<I", head[40:44])[0] == 0xFFFFFFFF


def test_a_header_that_says_to_the_end_is_read_to_the_end(tmp_path):
    """... and ffmpeg (the compression at the session's end) takes that size as
    "until the file ends"."""
    import subprocess
    from core.optional_modules import ffmpeg_exe
    if not ffmpeg_exe():
        import pytest
        pytest.skip("ffmpeg not installed")
    path = str(tmp_path / "long.wav")
    w = lp._WavWriter(path, 16000)
    w.writeframes(bytes(32000 * 3))
    w._n = 0x1FFFFFFFF                                    # the header of a file past 4 GB
    w.writeframes(bytes(32000 * 2))
    w.close()
    out = subprocess.run([ffmpeg_exe(), "-i", path, "-f", "null", "-"], capture_output=True, text=True).stderr
    assert "time=00:00:05" in out, out[-400:]


def test_a_recording_that_stopped_early_still_belongs_to_its_session(tmp_path, monkeypatch):
    """A full disk closed the recording, and the stop then found "no recording":
    the file was left out of the session's record (and nothing was said)."""
    page = _page()
    said = []
    monkeypatch.setattr(page, "_info", lambda text, error=False, duration=4000: said.append((text, error)))
    monkeypatch.setattr(lp.backend, "get_custom_paths", lambda *a, **k: (str(tmp_path),) * 3)
    page.save_rec_cb.setChecked(True)
    page._start_recording()
    page._autosave_timer.stop()
    page._record(bytes(3200))
    path = page._rec_path

    def full(_pcm):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(page._rec, "writeframes", full)
    page._record(bytes(3200))
    assert page._rec is None and len(said) == 1 and said[0][1] and "No space left" in said[0][0]
    page._record(bytes(3200))                             # no second notice, no error
    assert len(said) == 1
    assert page._stop_recording() == path and os.path.getsize(path) == 44 + 3200
    assert page._stop_recording() == ""                   # once


def test_a_recognizer_slower_than_the_speech_does_not_queue_without_end():
    """Finals are never dropped (each closes its utterance), so with a model that
    runs slower than real time they waited in a queue that only grew: 320-720 KB
    a segment, hours behind after a day. The oldest keep their place and give up
    their audio."""
    from tests.test_live_stream_race import _page as stub_page
    page, _lp = stub_page()
    said = []
    page._info = lambda text, error=False, duration=4000: said.append(text)
    page._recog_busy = True                               # one recognition is out and does not come back
    for i in range(30):
        page._dispatch_recognize(bytes([i + 1]) * 3200, True)
        if i % 3 == 0:
            page._dispatch_recognize(bytes(1600), False)  # partials in between: latest wins, as before
    finals = [q for q in page._recog_pending if q[1]]
    assert len(finals) == 30                              # every one still gets its turn, in order
    assert [bool(q[0]) for q in finals] == [False] * 18 + [True] * 12
    assert [q[0][0] for q in finals[18:]] == list(range(19, 31))
    assert len(said) == 1 and said[0]                     # told once
    assert page.dispatched == []                          # nothing started while the lane is busy


def test_the_translation_pane_keeps_following_after_a_long_translation():
    """A row is added as "[time] " and the view follows; its translation then
    makes the row taller (it wraps) — and the view, no longer "at the end", was
    never moved again: the newest translations stayed out of sight from the
    first long one on."""
    page = _page()
    pane = page.output_text
    pane.setParent(None)
    pane.resize(260, 120)
    pane.show()
    app.processEvents()
    bar = pane.verticalScrollBar()
    for i in range(30):
        page._out_rows.append([f"10:00:{i:02d}#{i}", ""])
        page._draw_out_row(i)
    app.processEvents()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    page._set_out_row("10:00:29#29", "这是一句很长的译文，" * 12)          # wraps over several lines
    app.processEvents()
    assert bar.value() == bar.maximum()
    for i in range(30, 40):
        page._out_rows.append([f"10:01:{i:02d}#{i}", ""])
        page._draw_out_row(i)
        page._set_out_row(f"10:01:{i:02d}#{i}", "又一句会折行的译文，" * 6)
    app.processEvents()
    assert bar.value() == bar.maximum()
    bar.setValue(0)                                           # the user reads back: left alone
    page._set_out_row("10:01:39#39", "改写最后一行，" * 10)
    page._out_rows.append(["10:02:00#40", ""])
    page._draw_out_row(40)
    app.processEvents()
    assert bar.value() == 0
    pane.close()


def test_a_row_is_one_text_block_whatever_the_model_returns():
    """Only \\n was taken out of a translation: \\r, U+2028 and U+2029 start a
    text block too, and every row after it was drawn over another row."""
    page = _page()
    full = lambda: "".join(lp.LivePage._out_line(r) + "\n" for r in page._out_rows)
    odd = ["first\r\nsecond", "a\rb", "para\u2029graph", "line\u2028sep", "tab\tstays"]
    for i, text in enumerate(odd * 2):
        page._out_rows.append([f"10:00:{i:02d}#{i}", ""])
        page._draw_out_row(i)
        page._set_out_row(f"10:00:{i:02d}#{i}", text)
    assert page.output_text.document().blockCount() == len(page._out_rows) + 1
    shown = page.output_text.toPlainText()
    assert shown == full() and len(shown.splitlines()) == 10
    assert "[10:00:00] first second" in shown and "para graph" in shown and "line sep" in shown and "tab\tstays" in shown
    lp.LivePage._append_line(page.input_text, "[10:00:00] said\r\nthen\u2028more")
    assert page.input_text.toPlainText() == "[10:00:00] said then more\n"


def test_a_stop_is_not_entered_twice(tmp_path, monkeypatch):
    """Results are delivered by hand while a session stops; one of them (a system
    audio capture that just failed) calls on_stop again — the session was saved
    twice, the second time without its recording."""
    page = _page()
    saved = []
    monkeypatch.setattr(page, "_save_live_history", lambda *a, **k: saved.append(a))
    page._stopping = True
    page._source = object()
    page.on_stop()
    assert saved == [] and page._source is not None          # nothing of a stop was done


def test_a_quiet_session_still_uses_its_models(monkeypatch):
    from core.pipelines import video_translation_pipeline as vp
    monkeypatch.setattr(vp, "_stt_used", {})
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "whisper-large-v3-turbo")
    monkeypatch.setattr(vp, "live_preview_model", lambda: "sensevoice-small")
    vp.stt_touch_live()
    assert {k[0] for k in vp._stt_used} == {"whisper", "sensevoice"}
    page = _page()
    monkeypatch.setattr(vp, "_stt_used", {})
    page._source = object()
    page._live_model, page._preview_model = "whisper-large-v3-turbo", "sensevoice-small"   # as the session started
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "qwen3-asr-1.7b")       # picked in Settings since
    page._autosave()                                          # the minute's tick of a running session
    assert {k[0] for k in vp._stt_used} == {"whisper", "sensevoice"}                       # the session's own


def test_a_session_goes_on_when_another_page_is_opened(monkeypatch):
    """Leaving the page stopped the session: one click on the navigation ended a
    day-long meeting, and starting again cleared both panes. It goes on now (as
    on the web page) and says that the microphone is still on."""
    from PySide6.QtGui import QHideEvent, QShowEvent
    page = _page()
    stops, said, mics, preloads = [], [], [], []
    monkeypatch.setattr(page, "on_stop", lambda: stops.append(1))
    monkeypatch.setattr(page, "_info", lambda text, error=False, duration=4000: said.append(text))
    monkeypatch.setattr(page, "_populate_mics", lambda: mics.append(1))
    monkeypatch.setattr(page, "_start_preload", lambda: preloads.append(1))
    monkeypatch.setattr(lp, "realtime_voice_available", lambda: True)
    from core.pipelines import video_translation_pipeline as vp
    monkeypatch.setattr(vp, "recognizer_ready", lambda *a, **k: False)   # e.g. another model picked in Settings
    closed = []
    page._caption_bar = type("Bar", (), {"close": lambda self: closed.append(1)})()
    page._source = object()                                   # a session is running
    page.hideEvent(QHideEvent())                              # another page is opened
    assert stops == [] and closed == [] and page._caption_bar is not None
    assert said == [lp.tr("Live Still Running", page._lang)]
    page.showEvent(QShowEvent())                              # ... and the user comes back
    assert mics == [] and preloads == []                      # nothing of the session is touched
    # no session: as before, the caption bar goes with the page
    page._source = None
    page.hideEvent(QHideEvent())
    assert closed == [1] and page._caption_bar is None and stops == [] and len(said) == 1
    page.showEvent(QShowEvent())
    assert mics == [1] and preloads == [1]


def test_a_session_keeps_the_model_it_started_with(monkeypatch):
    """Other pages can be opened while a session runs — Settings among them. A
    model picked there is for the next session: this one's commit mode was
    chosen for the model it started with."""
    from core.pipelines import video_translation_pipeline as vp
    made = []

    class Worker:
        def __init__(self, pcm, rate, is_final, model_id=None, lang_memory=None):
            made.append((is_final, model_id))
            self.done = self.finished = type("Sig", (), {"connect": lambda self, f: None})()

        def start(self):
            pass
    monkeypatch.setattr(lp, "LiveRecognizeWorker", Worker)
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "whisper-large-v3-turbo")
    monkeypatch.setattr(vp, "live_commit_mode", lambda: "final")
    monkeypatch.setattr(vp, "live_preview_model", lambda: "sensevoice-small")
    page = _page()
    page._reset_vad()                                         # the session starts
    page._source = object()
    monkeypatch.setattr(vp, "get_selected_live_stt_model", lambda: "sensevoice-small")    # changed in Settings
    page._dispatch_recognize(bytes(3200), False)              # a preview
    page._preview_busy = False
    page._dispatch_recognize(bytes(3200), True)               # the segment's final
    assert (True, "whisper-large-v3-turbo") in made and all(m in ("whisper-large-v3-turbo", "sensevoice-small") for _, m in made)
    assert [m for f, m in made if not f] and set(m for f, m in made if not f) == {"sensevoice-small"}
