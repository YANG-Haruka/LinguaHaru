# Corpus tests: DOCX comments, endnotes and charts translation.
#
# python-docx can't author comments/endnotes/charts, so a base doc is built
# and those parts are injected into the .docx zip, then run through the real
# extract -> translate -> write round trip.
#
# Run from the repo root:
#   python tests/test_corpus_docx_extras.py
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("docx_extras")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

COMMENTS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:comments xmlns:w="{W}">'
    '<w:comment w:id="0" w:author="Rev" w:date="2024-01-01T00:00:00Z" w:initials="R">'
    "<w:p><w:r><w:t>Comment text to translate</w:t></w:r></w:p></w:comment></w:comments>")

ENDNOTES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:endnotes xmlns:w="{W}">'
    '<w:endnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>'
    '<w:endnote w:type="continuationSeparator" w:id="0">'
    "<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>"
    '<w:endnote w:id="1"><w:p><w:r><w:t>Endnote text to translate</w:t></w:r></w:p></w:endnote>'
    "</w:endnotes>")

CHART = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart>'
    '<c:title><c:tx><c:rich><a:p><a:r><a:t>Sales Chart Title</a:t></a:r></a:p>'
    '</c:rich></c:tx></c:title><c:plotArea><c:barChart><c:ser><c:cat><c:strRef>'
    '<c:f>Sheet1!$A$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0">'
    '<c:v>Quarter One Label</c:v></c:pt></c:strCache></c:strRef></c:cat>'
    "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>")

OVERRIDES = (
    b'<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-'
    b'officedocument.wordprocessingml.comments+xml"/>'
    b'<Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-'
    b'officedocument.wordprocessingml.endnotes+xml"/>'
    b'<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-'
    b'officedocument.drawingml.chart+xml"/>')


def _build_docx_with_parts(path):
    from docx import Document
    base = path + ".base"
    doc = Document()
    doc.add_paragraph("Main body paragraph text.")
    doc.save(base)

    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(path, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "[Content_Types].xml":
                data = data.replace(b"</Types>", OVERRIDES + b"</Types>")
            zout.writestr(n, data)
        zout.writestr("word/comments.xml", COMMENTS)
        zout.writestr("word/endnotes.xml", ENDNOTES)
        zout.writestr("word/charts/chart1.xml", CHART)
    os.remove(base)


def test_docx_comments_endnotes_charts():
    print("DOCX: comments, endnotes and chart text translated")
    from core.pipelines.word_translation_pipeline import (
        extract_word_content_to_json, write_translated_content_to_word)

    src = os.path.join(WORK_DIR, "extras.docx")
    _build_docx_with_parts(src)

    src_json = extract_word_content_to_json(src, TEMP_DIR)
    import json
    with open(src_json, encoding="utf-8") as f:
        extracted = [i["value"] for i in json.load(f)]
    check("comment text extracted", "Comment text to translate" in extracted, str(extracted))
    check("endnote text extracted", "Endnote text to translate" in extracted, str(extracted))
    check("chart title extracted", "Sales Chart Title" in extracted, str(extracted))
    check("chart category cache extracted", "Quarter One Label" in extracted, str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_word(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           bilingual_mode=False, src_lang="en", dst_lang="ja")

    with zipfile.ZipFile(out) as z:
        comments = z.read("word/comments.xml").decode("utf-8")
        endnotes = z.read("word/endnotes.xml").decode("utf-8")
        chart = z.read("word/charts/chart1.xml").decode("utf-8")
        body = z.read("word/document.xml").decode("utf-8")

    check("comment translated", T + "Comment text to translate" in comments, comments)
    check("endnote translated", T + "Endnote text to translate" in endnotes, endnotes)
    check("endnote separators untouched", "<w:separator/>" in endnotes, endnotes)
    check("chart title translated", T + "Sales Chart Title" in chart, chart)
    check("chart category cache translated", T + "Quarter One Label" in chart, chart)
    check("body paragraph still translated", T + "Main body paragraph text." in body, body)


if __name__ == "__main__":
    run([test_docx_comments_endnotes_charts])
