# Corpus tests: SRT / VTT / ASS / LRC edge cases.
#   - SRT: long multi-line cues
#   - VTT: <v Name> voice spans, multi-line cues
#   - ASS: {\k} karaoke override tags
#   - LRC: repeated timestamps on one line
#
# Run from the repo root:
#   python tests/test_corpus_subtitles.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("subtitles")


def test_srt_long_multiline_cues():
    print("SRT: long multi-line cues survive as one cue")
    import json
    from core.pipelines.subtitle_translation_pipeline import (
        extract_srt_content_to_json, write_translated_content_to_srt)

    line1 = "This is the first long line of a subtitle cue that keeps going on"
    line2 = "and this second line continues the same sentence without a break"
    line3 = "while the third line finally brings the thought to a close"
    src = os.path.join(WORK_DIR, "long.srt")
    with open(src, "w", encoding="utf-8") as f:
        f.write(f"1\n00:00:01,000 --> 00:00:06,000\n{line1}\n{line2}\n{line3}\n\n"
                "2\n00:00:07,000 --> 00:00:09,000\nShort follow-up cue\n\n")

    src_json = extract_srt_content_to_json(src, TEMP_DIR)
    with open(src_json, encoding="utf-8") as f:
        extracted = json.load(f)
    check("multi-line cue extracted as ONE cue (lines joined with markers)",
          len(extracted) == 2 and "␊" in extracted[0]["value"], str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_srt(src, src_json, dst_json, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        content = f.read()

    check("both cues present", content.count("-->") == 2, content)
    check("three lines of cue 1 restored on separate lines",
          f"{T}{line1}\n{line2}\n{line3}" in content, content)
    check("second cue translated", T + "Short follow-up cue" in content, content)


def test_vtt_voice_spans():
    print("VTT: <v Name> voice spans and multi-line cues")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_vtt_content_to_json, write_translated_content_to_vtt)

    src = os.path.join(WORK_DIR, "voices.vtt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n"
                "00:00:01.000 --> 00:00:04.000\n"
                "<v Alice>Where have you been hiding all day?</v>\n\n"
                "00:00:05.000 --> 00:00:09.000\n"
                "<v Bob>I was busy fixing the engine room</v>\n"
                "<v Alice>You could have sent a message</v>\n")

    src_json = extract_vtt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_vtt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()

    # The <v Name> markup is part of the translatable line and is passed to
    # the translator verbatim (the prompt instructs the model to keep markup);
    # the fake translator prefixes the whole line, so the span must survive.
    check("voice span names survive", "<v Alice>" in result and "<v Bob>" in result, result)
    check("voiced cue text translated",
          "Where have you been hiding all day?" in result and T in result, result)
    check("both lines of the two-voice cue survive as separate lines",
          result.count("<v Alice>") == 2 and "<v Bob>" in result, result)
    check("timestamps untouched", "00:00:01.000 --> 00:00:04.000" in result, result)


def test_ass_karaoke_tags():
    print("ASS: {\\k} karaoke override tags restored in place")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_ass_content_to_json, write_translated_content_to_ass)

    src = os.path.join(WORK_DIR, "karaoke.ass")
    with open(src, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nTitle: Karaoke demo\n\n[V4+ Styles]\n"
                "Format: Name, Fontname\nStyle: Default,Arial\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,"
                "{\\k25}Sing{\\k30}ing {\\k40}all {\\k35}night {\\k50}long\n"
                "Dialogue: 0,0:00:06.00,0:00:08.00,Default,,0,0,0,,"
                "{\\pos(320,40)\\k20}Final {\\k60}chorus\n")

    src_json = extract_ass_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_ass(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8-sig") as f:
        result = f.read()

    check("all five {\\k} tags of line 1 restored",
          all(tag in result for tag in
              ("{\\k25}", "{\\k30}", "{\\k40}", "{\\k35}", "{\\k50}")), result)
    check("combined {\\pos...\\k} tag restored verbatim",
          "{\\pos(320,40)\\k20}" in result and "{\\k60}" in result, result)
    check("no marker placeholders leaked", "{{ASS_" not in result, result)
    check("karaoke text translated",
          T in result and "night" in result and "chorus" in result, result)
    check("styles section untouched", "Style: Default,Arial" in result, result)


def test_lrc_repeated_timestamps():
    print("LRC: repeated timestamps stay on one line")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_lrc_content_to_json, write_translated_content_to_lrc)

    src = os.path.join(WORK_DIR, "repeat.lrc")
    with open(src, "w", encoding="utf-8") as f:
        f.write("[ti:Repeat Test Song]\n[ar:Test Artist]\n"
                "[00:10.00]Opening verse line here\n"
                "[00:25.00][01:15.00][02:05.00]Triple repeated chorus line\n"
                "[00:40.50][01:30.50]Twice repeated bridge line\n")

    src_json = extract_lrc_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_lrc(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()

    check("metadata untouched", "[ti:Repeat Test Song]" in result, result)
    check("triple timestamp prefix kept intact on one line",
          f"[00:25.00][01:15.00][02:05.00]{T}Triple repeated chorus line" in result, result)
    check("double timestamp prefix kept intact",
          f"[00:40.50][01:30.50]{T}Twice repeated bridge line" in result, result)
    check("single timestamp line translated",
          f"[00:10.00]{T}Opening verse line here" in result, result)


def test_vtt_no_hours_timestamps():
    print("VTT: MM:SS.mmm (no-hours) timestamps are recognized, cues translated")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_vtt_content_to_json, write_translated_content_to_vtt)
    import json

    src = os.path.join(WORK_DIR, "nohours.vtt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n"
                "00:01.000 --> 00:04.000\n"
                "First cue with no hours field\n\n"
                "01:05.500 --> 01:09.000\n"
                "Second cue also without hours\n")

    src_json = extract_vtt_content_to_json(src, TEMP_DIR)
    with open(src_json, encoding="utf-8") as f:
        extracted = [i["value"] for i in json.load(f)]
    check("both no-hours cues extracted (not dropped)",
          len(extracted) == 2, str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_vtt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()
    check("no-hours timestamps preserved", "00:01.000 --> 00:04.000" in result, result)
    check("no-hours cue text translated",
          T + "First cue with no hours field" in result
          and T + "Second cue also without hours" in result, result)


if __name__ == "__main__":
    run([test_srt_long_multiline_cues, test_vtt_voice_spans,
         test_vtt_no_hours_timestamps,
         test_ass_karaoke_tags, test_lrc_repeated_timestamps])
