# Housekeeping around video/audio tasks, from a real 4-file batch (1850 s wall):
# 96% of that time was GPU transcription, 1% was the LLM, and the run left
# 28 GB behind — 24 GB of it plain copies of the source videos. Three fixes:
#
#   1. The GPU wait is logged and counted. Transcription is serialized across
#      tasks, so a queued file showed "Extracting content..." for 23 minutes
#      with nothing in the log between that and the transcription start.
#   2. Cues with no word character are dropped. Whisper emits a bare "…" for
#      breathing/background: 643 of one file's 1974 cues (40%), each a
#      flickering "…", and they also inflated the coverage report's
#      "untranslated" count because the translator rightly skips them.
#   3. A multi-GB input is referenced in place instead of copied into temp for
#      the Proofread tab, and retention now bounds the working-folder tree.
#
# Run from the repo root:
#   python tests/test_video_task_hygiene.py
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import check, run


def test_gpu_queue_is_countable():
    print("Video: the GPU wait exposes a queue length (a plain Lock has none)")
    from core.compute_lock import queued
    with queued() as first:
        with queued() as second:
            check("nested waiters counted", (first, second) == (1, 2), f"{first},{second}")
    with queued() as again:
        check("count resets when waiters leave", again == 1, str(again))

    import inspect
    from core.pipelines import video_translation_pipeline as vp
    src = inspect.getsource(vp.transcribe_media) if hasattr(vp, "transcribe_media") else ""
    if not src:                      # name differs -> scan the module once
        src = inspect.getsource(vp)
    check("the wait is written to the run log, not just the progress bar",
          "Waiting for the GPU" in src and "GPU acquired after waiting" in src)


def test_wordless_cues_are_dropped():
    print("Video: a cue must carry a word character to become a subtitle line")
    from core.pipelines.video_translation_pipeline import _WORDLIKE
    for junk in ("…", "...", "♪", "?!", "—", "  "):
        check(f"dropped: {junk!r}", not _WORDLIKE.search(junk))
    for real in ("あ", "図像", "3000円", "OK", "Привет", "…あ…", "ないよ"):
        check(f"kept: {real!r}", bool(_WORDLIKE.search(real)))


def test_big_input_is_not_copied_into_temp():
    print("Video: a multi-GB input is referenced in place, not copied for Proofread")
    from core.engine.base_translator import DocumentTranslator
    cap = DocumentTranslator._MANIFEST_COPY_MAX_BYTES
    check("copy cap is a sane document size", 1 << 20 <= cap <= 1 << 30, str(cap))

    import inspect
    src = inspect.getsource(DocumentTranslator._write_manifest)
    check("the manifest records where the original lives", '"source_path"' in src)
    check("the copy is gated on the cap", "_MANIFEST_COPY_MAX_BYTES" in src)
    # Re-export must follow that path, and say so when the file is gone.
    from core import backend
    exp = inspect.getsource(backend.export_proofread_doc)
    check("re-export falls back to source_path", 'manifest.get("source_path")' in exp)
    check("re-export reports a moved/deleted original",
          "moved or deleted" in exp)


def test_temp_retention_is_size_bounded():
    print("Video: retention bounds the working-folder tree, oldest first")
    from core.retention import prune_temp
    d = tempfile.mkdtemp()
    try:
        names = ["r1_old", "r2", "r3", "r4", "r5", "r6_new"]
        for i, name in enumerate(names):
            p = os.path.join(d, name)
            os.makedirs(p)
            with open(os.path.join(p, "blob"), "wb") as f:
                f.write(b"x" * (2 << 20))          # 2 MB each -> 12 MB total
            t = time.time() - (len(names) - i) * 86400
            os.utime(p, (t, t))

        check("unlimited (0) prunes nothing", prune_temp(d, max_size_mb=0) == 0)
        removed = prune_temp(d, max_size_mb=7, keep_newest=3)
        left = sorted(os.listdir(d))
        check("pruned the oldest until under the cap", removed == 3, str(removed))
        check("kept the newest runs", left == ["r4", "r5", "r6_new"], str(left))
        check("an over-cap protected set is left alone",
              prune_temp(d, max_size_mb=1, keep_newest=3) == 0
              and sorted(os.listdir(d)) == ["r4", "r5", "r6_new"])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    import inspect
    from core import retention
    src = inspect.getsource(retention.run_retention)
    check("startup retention sweeps temp too", "prune_temp(" in src)
    check("it has a working default (the bug was unbounded growth)",
          'cfg.get("temp_max_size_mb", 20000)' in src)


def test_subtitles_are_not_deduplicated():
    print("Subtitles: repeated lines are translated per occurrence, not collapsed")
    import json
    import tempfile
    import types
    from core.engine.base_translator import DocumentTranslator
    from core.engine.text_separator import deduplicate_translation_content

    fake = types.SimpleNamespace(_NO_DEDUP_EXTS=DocumentTranslator._NO_DEDUP_EXTS)
    fake._dedup_enabled = types.MethodType(DocumentTranslator._dedup_enabled, fake)
    for name in ("a.srt", "a.vtt", "a.ass", "a.lrc", "a.mp4", "a.mkv", "a.mp3"):
        fake.input_file_path = name
        check(f"dedup OFF for {name}", not fake._dedup_enabled())
    for name in ("a.docx", "a.xlsx", "a.pdf", "a.txt"):
        fake.input_file_path = name
        check(f"dedup ON for {name}", fake._dedup_enabled())

    # The pass-through keeps every item AND a 1:1 map, so split/restore is
    # unchanged — a broken map would silently misplace translated lines.
    items = [{"count_src": i + 1, "value": v, "type": "text"} for i, v in
             enumerate(["はい", "うん", "はい", "そうですね", "はい", "うん"])]
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.json")
    with open(src, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    on_data, on_map = deduplicate_translation_content(src, enabled=True)
    off_data, off_map = deduplicate_translation_content(src, enabled=False)
    check("dedup ON collapses the repeats", len(on_data) == 3, str(len(on_data)))
    check("dedup OFF keeps every occurrence", len(off_data) == 6, str(len(off_data)))
    check("dedup OFF maps 1:1", off_map == {i: i for i in range(1, 7)}, str(off_map))
    check("every kept item is translatable on its own",
          all(x.get("count_deduped") and "value" in x for x in off_data))
    import shutil
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    run([test_gpu_queue_is_countable, test_wordless_cues_are_dropped,
         test_big_input_is_not_copied_into_temp, test_temp_retention_is_size_bounded,
         test_subtitles_are_not_deduplicated])
