# Real-time captions: an utterance's FINAL STT result must be processed against
# THAT utterance's committed text, never the next one's.
#
# Reported: while speaking, a sentence shows up twice, grows as you keep
# talking, then "snaps back" to one. Cause: the next utterance's VAD onset reset
# the committed-text state while the previous utterance's final result was still
# in flight. That final (or a late partial) then aligned against empty state and
# re-committed the WHOLE previous utterance — shown, and translated, a second
# time; the 3-line caption window later scrolled the originals away. Worst when
# continuous speech hits the 30 s cap and the next utterance starts at once.
# Web had the same race and worse: its final bypassed the partial queue.
#
# Run from the repo root:
#   python tests/test_live_stream_race.py
import os
import shutil
import subprocess
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.corpus_common import check, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page():
    """A stand-in LivePage carrying the REAL stream/VAD/dispatch methods; STT
    workers are captured instead of run so the test controls result order."""
    from qt_app import live_page as lp
    P = lp.LivePage
    page = types.SimpleNamespace()
    for name in ("_vad_step", "_dispatch_recognize", "_on_recognized_stream",
                 "_uncommitted_tail", "_note_detected", "_set_caption_interim",
                 "_push_caption"):
        setattr(page, name, types.MethodType(getattr(P, name), page))
    page._split_scored, page._find_boundary = P._split_scored, P._find_boundary
    page._common_prefix, page._WORDLIKE = P._common_prefix, P._WORDLIKE
    page._is_listening = lambda: True
    page._lang = "zh"
    page.status_label = types.SimpleNamespace(setText=lambda *_: None)
    page._caption_bar = None
    page._vad_preroll = bytearray(); page._vad_buf = bytearray()
    page._vad_on = False; page._vad_voice_ms = 0.0; page._vad_sil_ms = 0.0
    page._partial_ms = 0.0
    page._stream_committed = ""; page._stream_last = ""
    page._stream_detected = "auto"; page._det_hist = []
    page._recog_busy = False; page._recog_pending = []
    page._local_workers = []
    page.commits = []
    page._commit_stream_sentence = lambda s: page.commits.append(s.strip())

    page.dispatched = []
    class _Worker:
        def __init__(self, pcm, rate, is_final):
            page.dispatched.append((pcm, is_final))
            self.done = types.SimpleNamespace(connect=lambda f: None)
            self.finished = types.SimpleNamespace(connect=lambda f: None)
        def start(self):
            pass
    lp.LiveRecognizeWorker = _Worker
    return page, lp


def _frames(page, lp, n, speech, fill):
    import numpy as np
    page._frame_is_speech = lambda _f: speech
    frame = bytes([fill]) * (lp._VAD_FRAME * 2)
    for _ in range(n):
        page._vad_step(frame, np.frombuffer(frame, dtype=np.int16))


def test_final_after_next_onset_is_not_recommitted():
    print("Live: a slow final that lands after the next utterance started commits only its tail")
    page, lp = _page()
    deliver = lambda text, fin: page._on_recognized_stream(text, "zh", fin)

    # Utterance 1: partials come back normally (LocalAgreement-2 commits).
    deliver("今天天气很好。", False)
    deliver("今天天气很好。我们去公园", False)
    deliver("今天天气很好。我们去公园吧。明天", False)
    deliver("今天天气很好。我们去公园吧。明天见", False)
    check("utterance 1 committed its stable sentences",
          page.commits == ["今天天气很好。", "我们去公园吧。"], str(page.commits))

    # Its final is in flight... and the speaker starts utterance 2 at once:
    # run the REAL VAD onset (this is where the state used to be reset).
    _frames(page, lp, 8, speech=True, fill=2)
    check("the onset really fired", page._vad_on)
    check("onset keeps utterance 1's committed text (its final is pending)",
          page._stream_committed.startswith("今天天气很好。"), repr(page._stream_committed))

    deliver("今天天气很好。我们去公园吧。明天见。", True)
    check("the final committed ONLY the uncommitted tail",
          page.commits == ["今天天气很好。", "我们去公园吧。", "明天见。"], str(page.commits))
    check("processing the final closed utterance 1's state",
          page._stream_committed == "" and page._stream_last == "" and page._det_hist == [])

    # Utterance 2 then streams from a clean slate.
    deliver("好的。我们", False)
    deliver("好的。我们出发吧", False)
    deliver("好的。我们出发吧。", True)
    check("utterance 2 committed once, nothing duplicated",
          page.commits == ["今天天气很好。", "我们去公园吧。", "明天见。", "好的。", "我们出发吧。"],
          str(page.commits))


def test_queue_keeps_order_and_never_drops_a_final():
    print("Live: while STT is busy, requests queue in order and a final is never dropped")
    page, _lp = _page()
    page._recog_busy = True                       # a partial is in flight
    page._dispatch_recognize(b"u1-partial", False)
    page._dispatch_recognize(b"u1-final", True)   # supersedes the queued partial
    page._dispatch_recognize(b"u2-partial", False)
    page._dispatch_recognize(b"u2-final", True)   # the OLD single slot overwrote u1-final here
    check("both finals kept, in order; stale partials dropped",
          page._recog_pending == [(b"u1-final", True), (b"u2-final", True)],
          str(page._recog_pending))

    page._on_recognized_stream("", "zh", False)  # in-flight partial returns
    check("the next queued request is dispatched first-in-first-out",
          page.dispatched == [(b"u1-final", True)], str(page.dispatched))


def test_hard_cap_does_not_resend_the_previous_tail():
    print("Live: after the 30 s cap the next utterance's pre-roll holds no old audio")
    page, lp = _page()
    page._max_seg_ms = 1000                        # a small cap for the test
    _frames(page, lp, 8, speech=True, fill=1)      # onset
    for _ in range(200):                           # keep talking until the cap cuts
        _frames(page, lp, 1, speech=True, fill=1)
        if not page._vad_on:
            break
    check("the cap ended utterance 1 with a final (sent or queued behind a partial)",
          not page._vad_on and any(f for _, f in page.dispatched + page._recog_pending))
    _frames(page, lp, 8, speech=True, fill=2)      # utterance 2 starts at once
    check("utterance 2 started", page._vad_on)
    check("its buffer has none of utterance 1's audio (no seam duplicate)",
          1 not in set(page._vad_buf), f"{sorted(set(page._vad_buf))}")


def test_web_twin_has_the_same_fix():
    print("Live (web): the real app.js code, run against a slow-final race, commits cleanly")
    node = shutil.which("node")
    if not node:
        print("  [SKIP] node not installed")
        return
    r = subprocess.run([node, os.path.join(ROOT, "tests", "web_live_race.mjs"),
                        os.path.join(ROOT, "webapp", "static", "app.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    check("web app.js commits each sentence exactly once", "RESULT: CLEAN" in r.stdout,
          (r.stdout + r.stderr)[-600:])
    worklet = open(os.path.join(ROOT, "webapp", "static", "vad-worklet.js"), encoding="utf-8").read()
    flush = worklet[worklet.index("_flush(durMs) {"):worklet.index("process(inputs)")]
    check("web worklet restarts the pre-roll ring on flush",
          "this.ringPos = 0; this.ringFull = false;" in flush)


if __name__ == "__main__":
    run([test_final_after_next_onset_is_not_recommitted,
         test_queue_keeps_order_and_never_drops_a_final,
         test_hard_cap_does_not_resend_the_previous_tail,
         test_web_twin_has_the_same_fix])
