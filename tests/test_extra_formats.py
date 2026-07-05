# Round-trip tests for the extra formats: HTML, ODT, JSON, VTT, ASS, LRC, TSV.
#
# Run from the repo root:
#   python tests/test_extra_formats.py
import json
import os
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "extra")
TEMP_DIR = os.path.join(WORK_DIR, "temp")
RESULT_DIR = os.path.join(WORK_DIR, "result")

T = "[T]"
CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"\n      -> {detail}" if detail and not cond else ""))
    return bool(cond)


def fake_translate(src_json_path):
    with open(src_json_path, encoding="utf-8") as f:
        data = json.load(f)
    out = [{"count_src": i["count_src"], "type": i.get("type", "text"),
            "original": i["value"], "translated": T + i["value"]} for i in data]
    dst = os.path.join(os.path.dirname(src_json_path), "dst_translated.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return dst


def test_html():
    print("HTML")
    from core.pipelines.html_translation_pipeline import (
        extract_html_content_to_json, write_translated_content_to_html)

    src = os.path.join(WORK_DIR, "page.html")
    with open(src, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html><head><title>Page title text</title>"
                '<style>body { color: red; }</style></head>'
                "<body><h1>Main heading content</h1>"
                "<p>Paragraph with <a href='https://example.com'>a link inside</a> here.</p>"
                "<table><tr><td>Cell content alpha</td></tr></table>"
                "</body></html>")

    src_json = extract_html_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_html(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()

    check("heading translated", T + "Main heading content" in result, result)
    check("table cell translated", T + "Cell content alpha" in result, result)
    check("paragraph translated with anchor rebuilt in place",
          T + "Paragraph with " in result and ">a link inside</a>" in result, result)
    check("css untouched", "color: red" in result, result)
    check("link href untouched", "https://example.com" in result, result)


def test_odt():
    print("ODT")
    from core.pipelines.odt_translation_pipeline import (
        extract_odt_content_to_json, write_translated_content_to_odt)

    src = os.path.join(WORK_DIR, "doc.odt")
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        "<office:body><office:text>"
        '<text:h text:style-name="Heading_1">Document heading line</text:h>'
        "<text:p>Simple body paragraph text.</text:p>"
        '<text:p>Styled paragraph with <text:span text:style-name="T1">inner span</text:span> tail.</text:p>'
        "<table:table><table:table-row><table:table-cell>"
        "<text:p>Table cell paragraph</text:p>"
        "</table:table-cell></table:table-row></table:table>"
        "</office:text></office:body></office:document-content>"
    )
    manifest = ('<?xml version="1.0"?><manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<manifest:file-entry manifest:media-type="application/vnd.oasis.opendocument.text" '
                'manifest:full-path="/"/></manifest:manifest>')
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", content_xml, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)

    src_json = extract_odt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_odt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")

    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        chap = z.read("content.xml").decode("utf-8")

    check("mimetype first and stored",
          first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED)
    check("heading translated", T + "Document heading line" in chap, chap)
    check("simple paragraph translated", T + "Simple body paragraph text." in chap, chap)
    check("styled paragraph translated wholesale",
          T + "Styled paragraph with inner span tail." in chap, chap)
    check("table cell paragraph translated", T + "Table cell paragraph" in chap, chap)


def test_json_format():
    print("JSON")
    from core.pipelines.json_translation_pipeline import (
        extract_json_content_to_json, write_translated_content_to_json)

    src = os.path.join(WORK_DIR, "locale.json")
    payload = {
        "app": {"title": "Welcome to the application", "version": "1.2.3"},
        "buttons": ["Save your changes", "Cancel the operation"],
        "count": 42,
        "multi": "First line here\nSecond line here",
    }
    with open(src, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    src_json = extract_json_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_json(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = json.load(f)

    check("nested value translated", result["app"]["title"] == T + "Welcome to the application",
          str(result))
    check("list values translated", result["buttons"][0] == T + "Save your changes", str(result))
    check("version string untouched", result["app"]["version"] == "1.2.3", str(result))
    check("number untouched", result["count"] == 42, str(result))
    check("multiline newline restored",
          result["multi"] == T + "First line here\nSecond line here", repr(result["multi"]))


def test_vtt():
    print("VTT")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_vtt_content_to_json, write_translated_content_to_vtt)

    src = os.path.join(WORK_DIR, "subs.vtt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\nNOTE this comment stays\n\n"
                "cue-1\n00:00:01.000 --> 00:00:03.000 position:50%\n"
                "Hello from the first cue\n\n"
                "00:00:04.000 --> 00:00:06.000\nSecond cue text line\n")

    src_json = extract_vtt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_vtt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()

    check("header kept", result.startswith("WEBVTT"), result)
    check("NOTE kept untranslated", "NOTE this comment stays" in result, result)
    check("cue settings kept", "position:50%" in result, result)
    check("cues translated", T + "Hello from the first cue" in result
          and T + "Second cue text line" in result, result)


def test_ass():
    print("ASS")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_ass_content_to_json, write_translated_content_to_ass)

    src = os.path.join(WORK_DIR, "subs.ass")
    with open(src, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nTitle: Demo\n\n[V4+ Styles]\n"
                "Format: Name, Fontname\nStyle: Default,Arial\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Styled opening line{\\i0}\n"
                "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,First part\\NSecond part\n")

    src_json = extract_ass_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_ass(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8-sig") as f:
        result = f.read()

    check("override tags restored in place",
          "{\\i1}" in result and "{\\i0}" in result and "{{ASS_" not in result, result)
    check("styled line translated", T + "Styled opening line" in result.replace("{\\i1}", ""), result)
    check("\\N line break restored", "\\N" in result, result)
    check("style section untouched", "Style: Default,Arial" in result, result)


def test_lrc():
    print("LRC")
    from core.pipelines.subtitle_formats_pipeline import (
        extract_lrc_content_to_json, write_translated_content_to_lrc)

    src = os.path.join(WORK_DIR, "song.lrc")
    with open(src, "w", encoding="utf-8") as f:
        f.write("[ti:Song Title Meta]\n[ar:Artist Name]\n"
                "[00:12.00]First lyric line here\n"
                "[00:17.20][01:05.00]Repeated chorus line\n")

    src_json = extract_lrc_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_lrc(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()

    check("metadata untouched", "[ti:Song Title Meta]" in result, result)
    check("timestamps kept", "[00:12.00]" in result and "[00:17.20][01:05.00]" in result, result)
    check("lyrics translated", T + "First lyric line here" in result
          and T + "Repeated chorus line" in result, result)


def test_tsv():
    print("TSV (via CSV pipeline)")
    from core.pipelines.csv_translation_pipeline import (
        extract_csv_content_to_json, write_translated_content_to_csv)

    src = os.path.join(WORK_DIR, "data.tsv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        f.write("Item name\tQuantity\nWooden table\t4\n")

    src_json = extract_csv_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_csv(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    check("output keeps .tsv extension", out.endswith(".tsv"), out)
    with open(out, encoding="utf-8-sig") as f:
        result = f.read()
    check("tab delimiter kept and text translated",
          f"{T}Wooden table\t4" in result, repr(result))


def main():
    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(RESULT_DIR, exist_ok=True)

    for fn in (test_html, test_odt, test_json_format, test_vtt, test_ass, test_lrc, test_tsv):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            CHECKS.append((fn.__name__ + " (crashed)", False))
        print()

    passed = sum(1 for _, ok in CHECKS if ok)
    print(f"{passed}/{len(CHECKS)} checks passed")
    for name, ok in CHECKS:
        if not ok:
            print(f"  FAIL: {name}")
    sys.exit(0 if passed == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
