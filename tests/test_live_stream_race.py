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
    page._quiet_cut = P._quiet_cut
    page._norm_map, page._NORM_SKIP = P._norm_map, P._NORM_SKIP
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
          # "好的。" alone is too short to end a unit -> rides with its sentence
          page.commits == ["今天天气很好。", "我们去公园吧。", "明天见。", "好的。我们出发吧。"],
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


def test_hard_cap_cuts_at_a_pause_without_loss_or_overlap():
    print("Live: the length cap cuts at the quietest point; the tail carries over, nothing lost or repeated")
    import numpy as np
    page, lp = _page()
    page._hang_ms, page._max_seg_ms = 900.0, 3000.0
    page._frame_is_speech = lambda _f: True        # continuous speech, no pause
    rng = np.random.default_rng(0)
    fed = bytearray()
    for i in range(400):                           # ~6.4 s, a quiet dip at frame 150
        amp = 50 if 148 <= i <= 152 else 8000
        fr = (rng.standard_normal(lp._VAD_FRAME) * amp).astype(np.int16).tobytes()
        fed += fr
        page._vad_step(fr, np.frombuffer(fr, dtype=np.int16))
        if any(f for _, f in page.dispatched + page._recog_pending):
            break
    finals = [p for p, f in page.dispatched + page._recog_pending if f]
    check("the cap sent utterance 1 as a final", len(finals) == 1, str(len(finals)))
    check("still listening (speech continues)", page._vad_on)
    onset = len(fed) - len(finals[0]) - len(page._vad_buf)
    check("final + carried tail == the audio after onset (no loss, no overlap)",
          onset >= 0 and finals[0] + bytes(page._vad_buf) == bytes(fed[onset:]))
    cut_frame = (onset + len(finals[0])) // (lp._VAD_FRAME * 2)
    check("cut landed in the quiet dip, not mid-word", 146 <= cut_frame <= 154, str(cut_frame))


def test_redecode_spacing_neither_repeats_nor_drops():
    print("Live: a re-decode that adds spaces/punctuation aligns on content, not raw length")
    page, _lp = _page()
    # Seen in a real session: committed, then the final re-decoded WITH spaces
    # re-emitted the tail as a new line "ら 完全 に。".
    page._stream_committed = "すいません別の作業してたら完全に。"
    check("spaced re-decode adds nothing new",
          page._uncommitted_tail("すいません 別の作業 してたら 完全 に。") == "",
          repr(page._uncommitted_tail("すいません 別の作業 してたら 完全 に。")))
    check("…and only the genuinely new words come through",
          page._uncommitted_tail("すいません、別の作業 してたら 完全に 忘れてました。") == "忘れてました。",
          repr(page._uncommitted_tail("すいません、別の作業 してたら 完全に 忘れてました。")))
    # Re-decode dropped the commas the partial had: the old raw-length skip ate
    # the start of the new words.
    page._stream_committed = "えっと、今、作業して"
    check("fewer punctuation marks don't swallow new words",
          page._uncommitted_tail("えっと今作業していました。") == "いました。",
          repr(page._uncommitted_tail("えっと今作業していました。")))


def test_fragments_and_soft_cap():
    print("Live: no fragments from SenseVoice's trailing 。, fillers dropped, no cut without a pause")
    import numpy as np
    page, lp = _page()
    deliver = lambda text, fin: page._on_recognized_stream(text, "ja", fin)
    # Every partial ends with 。 — agreeing on "…多分。" must NOT commit it yet.
    deliver("データベースの設定とか多分。", False)
    deliver("データベースの設定とか多分。", False)
    check("a full stop at the end of the partial isn't a boundary yet", page.commits == [],
          str(page.commits))
    deliver("データベースの設定とか多分、あのアプリの開発も半分ぐらい。", True)
    check("the sentence commits whole once it's complete",
          page.commits == ["データベースの設定とか多分、あのアプリの開発も半分ぐらい。"], str(page.commits))
    # Filler / noise units are not committed (real commit path, stubbed sink).
    P = lp.LivePage
    kept = []
    stub = types.SimpleNamespace(input_text=None)
    for s in ("の。", "あ。", "H.", "No.", "はい、そうです。"):
        stub2 = types.SimpleNamespace(_WORDLIKE=P._WORDLIKE)
        try:
            P._commit_stream_sentence(stub2, s)
        except AttributeError:      # got past the filters -> would be committed
            kept.append(s)
    check("fillers dropped, real words kept", kept == ["No.", "はい、そうです。"], str(kept))
    # Soft cap: continuous sound with no dip -> keep listening past the cap.
    page2, _ = _page()
    page2._hang_ms, page2._max_seg_ms = 900.0, 3000.0
    page2._frame_is_speech = lambda _f: True
    rng = np.random.default_rng(1)
    for i in range(int(3000 * 1.3 / 16)):
        fr = (rng.standard_normal(lp._VAD_FRAME) * 8000).astype(np.int16).tobytes()
        page2._vad_step(fr, np.frombuffer(fr, dtype=np.int16))
    check("no forced cut at the cap when there's no pause (waits up to 1.5x)",
          not any(f for _, f in page2.dispatched + page2._recog_pending))
    for i in range(int(3000 * 0.3 / 16)):
        fr = (rng.standard_normal(lp._VAD_FRAME) * 8000).astype(np.int16).tobytes()
        page2._vad_step(fr, np.frombuffer(fr, dtype=np.int16))
    check("…but it does cut by 1.5x the cap",
          any(f for _, f in page2.dispatched + page2._recog_pending))


def test_runon_japanese_breaks_at_a_particle():
    print("Live: unpunctuated Japanese run-on breaks at a particle, never inside a katakana word")
    _page()
    from qt_app.live_page import LivePage as P
    run = "今日はデータベースの設計について説明させていただいてそのあとでプロジェクトマネジメントのスケジュールを確認します"
    units, _ = P._split_scored(run, False)
    check("a unit was cut from the run-on", len(units) >= 1, str(units))
    first = units[0]
    nxt = run[len(first):]
    check("the cut is at a hiragana -> kanji/katakana boundary",
          P._script(first[-1]) == "H" and P._script(nxt[0]) in "CK", repr((first[-3:], nxt[:3])))
    run2 = "あ" * 30 + "プロジェクトマネジメントスケジュール" + "です"
    u2, _ = P._split_scored(run2, False)
    check("never splits a katakana run", all(not (P._script(u[-1]) == "K" and P._script(run2[len(u)]) == "K")
                                              for u in u2[:1]), str(u2))


def test_backchannels_ride_with_the_next_line():
    print("Live: backchannels (うん。/ですね。) don't get a line of their own")
    from qt_app.live_page import LivePage as P
    sent = []

    class _Stop(Exception):
        pass

    def _insert(line):          # the first thing the real commit does
        sent.append(line.split("] ", 1)[1].strip())
        raise _Stop
    page = types.SimpleNamespace(_WORDLIKE=P._WORDLIKE, _norm_map=P._norm_map,
                                 _BACKCHANNELS=P._BACKCHANNELS, _ctx_history=[], _held="",
                                 input_text=types.SimpleNamespace(insertPlainText=_insert))
    for s in ("うん。", "そうですね。", "前回ちょっとお伝えした通りですけど。", "AWSに保存する予定です。"):
        try:
            P._commit_stream_sentence(page, s)
        except _Stop:
            pass
    check("backchannels merged into the next real line",
          sent == ["うん。そうですね。前回ちょっとお伝えした通りですけど。", "AWSに保存する予定です。"],
          str(sent))

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
         test_hard_cap_cuts_at_a_pause_without_loss_or_overlap,
         test_redecode_spacing_neither_repeats_nor_drops,
         test_fragments_and_soft_cap,
         test_runon_japanese_breaks_at_a_particle,
         test_backchannels_ride_with_the_next_line,
         test_web_twin_has_the_same_fix])
