# The translated subtitle is named after its source file (config
# `subtitle_match_source_name`, default ON) so a player auto-loads it next to
# the video; unticking restores the `_<src>2<dst>` suffix. Covers every subtitle
# writer plus the path the engine reports to history.
#
# Run from the repo root:
#   python tests/test_subtitle_naming.py
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core.pipelines import subtitle_formats_pipeline as fmts
from core.pipelines.subtitle_translation_pipeline import (
    extract_srt_content_to_json, write_translated_content_to_srt)

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello there.

2
00:00:03,000 --> 00:00:04,000
Goodbye.
"""


def _set_option(on):
    """Patch the config reader instead of the user's system_config.json."""
    fmts._match_source_name_enabled = lambda: on


def test_naming_both_modes():
    for same_name, expect in ((True, "movie.srt"), (False, "movie_en2zh.srt")):
        _set_option(same_name)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "movie.srt")
            with open(src, "w", encoding="utf-8") as f:
                f.write(SRT)
            temp_dir, result_dir = os.path.join(d, "t"), os.path.join(d, "r")
            os.makedirs(temp_dir)
            json_path = extract_srt_content_to_json(src, temp_dir)
            translated = os.path.join(d, "dst.json")
            with open(json_path, encoding="utf-8") as f:
                items = json.load(f)
            with open(translated, "w", encoding="utf-8") as f:
                json.dump([{"count_src": it["count_src"], "translated": "译文"}
                           for it in items], f, ensure_ascii=False)

            out = write_translated_content_to_srt(src, json_path, translated,
                                                  result_dir, "en", "zh")
            assert os.path.basename(out) == expect, (same_name, out)
            assert os.path.exists(out), out
            # Timestamps survive either way.
            body = open(out, encoding="utf-8").read()
            assert "00:00:01,000 --> 00:00:02,000" in body, body
    print("OK: srt naming honors the option in both modes")


def test_other_subtitle_formats():
    """vtt/ass/lrc share one helper, so they follow the same rule."""
    for same_name, expect in ((True, "movie.vtt"), (False, "movie_en2zh.vtt")):
        _set_option(same_name)
        with tempfile.TemporaryDirectory() as d:
            p = fmts.subtitle_result_path(os.path.join(d, "movie.vtt"), d, "en", "zh")
            assert os.path.basename(p) == expect, (same_name, p)
    # An explicit extension wins (the video path feeds in a temp .srt).
    _set_option(True)
    with tempfile.TemporaryDirectory() as d:
        p = fmts.subtitle_result_path(os.path.join(d, "movie.mkv"), d, "en", "zh", ".srt")
        assert os.path.basename(p) == "movie.srt", p
    print("OK: vtt/ass/lrc and the media path use the same rule")


def test_engine_reports_the_written_path():
    """History must point at the file that was actually written, not at the
    generic `<name>_<src>2<dst><input ext>` guess (which for a video task named
    a .mp4 that no step ever wrote)."""
    from core.engine.base_translator import DocumentTranslator
    import inspect
    src = inspect.getsource(DocumentTranslator._process_impl)
    assert "_written_output_path" in src, "engine ignores the writer's path"

    for mod, cls in (("core.translators.subtitle_translator", "SubtitlesTranslator"),
                     ("core.translators.video_translator", "VideoTranslator"),
                     ("core.translators.extra_formats_translator", "_SimpleTranslator"),
                     ("core.translators.extra_formats_translator", "VttTranslator")):
        try:
            m = __import__(mod, fromlist=[cls])
        except Exception as e:  # optional plugin deps (video) may be absent
            print(f"  skipped {cls}: {e}")
            continue
        body = inspect.getsource(getattr(m, cls).write_translated_json_to_file)
        assert "_written_output_path" in body, f"{cls} drops the written path"
    print("OK: every subtitle writer reports the path it wrote")


def test_both_frontends_expose_the_option():
    """Qt and Web must both offer the toggle, and the web key must be settable
    (an unwhitelisted key is silently dropped by /api/config)."""
    qt = open(os.path.join(REPO_ROOT, "qt_app/translate_page.py"), encoding="utf-8").read()
    assert 'config.get("subtitle_match_source_name", True)' in qt
    assert "subs_name_card.setVisible" in qt

    js = open(os.path.join(REPO_ROOT, "webapp/static/app.js"), encoding="utf-8").read()
    html = open(os.path.join(REPO_ROOT, "webapp/static/index.html"), encoding="utf-8").read()
    server = open(os.path.join(REPO_ROOT, "webapp/server.py"), encoding="utf-8").read()
    for ident in ("subs-same-name", "subtitle-options"):
        assert f'"{ident}"' in js and f'id="{ident}"' in html, ident
    assert '"subtitle_match_source_name"' in server         # /api/config whitelist
    assert 'config.get("subtitle_match_source_name", True)' in server   # bootstrap

    en = json.load(open(os.path.join(REPO_ROOT, "config/locales/en.json"), encoding="utf-8"))
    assert en["Subtitle Same Name"] and en["Subtitle Same Name Hint"]
    print("OK: both frontends wire the option (labels, ids, whitelist)")


if __name__ == "__main__":
    test_naming_both_modes()
    test_other_subtitle_formats()
    test_engine_reports_the_written_path()
    test_both_frontends_expose_the_option()
    print("\nall subtitle-naming tests passed")
