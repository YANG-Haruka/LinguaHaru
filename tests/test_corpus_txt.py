# Corpus tests: plain TXT line-ending and structure preservation.
#   - CRLF (Windows) files round-trip as CRLF, not collapsed to LF
#   - blank lines / indentation preserved
#
# Run from the repo root:
#   python tests/test_corpus_txt.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("txt")


def test_txt_crlf_preserved():
    print("TXT: CRLF line endings + blank lines/indentation are preserved")
    from core.pipelines.txt_translation_pipeline import (
        extract_txt_content_to_json, write_translated_content_to_txt)

    src = os.path.join(WORK_DIR, "crlf.txt")
    # Windows CRLF file with a blank line and an indented line.
    with open(src, "wb") as f:
        f.write("First paragraph line\r\n"
                "\r\n"
                "    Indented second line\r\n"
                "Third line\r\n".encode("utf-8"))

    src_json = extract_txt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_txt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")

    raw = open(out, "rb").read()
    check("output uses CRLF, not bare LF", b"\r\n" in raw, repr(raw[:40]))
    check("no LF left without a preceding CR",
          raw.replace(b"\r\n", b"") .count(b"\n") == 0, repr(raw))

    text = raw.decode("utf-8")
    lines = text.split("\r\n")
    check("first line translated", lines[0] == T + "First paragraph line", repr(lines[0]))
    check("blank line preserved", lines[1] == "", repr(lines))
    check("indentation preserved on translated line",
          lines[2] == "    " + T + "Indented second line", repr(lines[2]))


def test_txt_lf_stays_lf():
    print("TXT: LF-only files stay LF (no CRLF injected)")
    from core.pipelines.txt_translation_pipeline import (
        extract_txt_content_to_json, write_translated_content_to_txt)

    src = os.path.join(WORK_DIR, "lf.txt")
    with open(src, "wb") as f:
        f.write("Alpha line\nBeta line\n".encode("utf-8"))

    src_json = extract_txt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_txt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    raw = open(out, "rb").read()
    check("no CR introduced into an LF-only file", b"\r" not in raw, repr(raw))
    check("lines translated", T + "Alpha line" in raw.decode("utf-8"), repr(raw))


if __name__ == "__main__":
    run([test_txt_crlf_preserved, test_txt_lf_stays_lf])
