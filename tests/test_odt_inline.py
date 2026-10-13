# ODT paragraphs were flattened wholesale: footnotes/annotations were inlined
# into the body text and deleted, fields (page number) became static translated
# text, text:span formatting was lost, and text-box paragraphs inside a
# draw:frame stayed untranslated.
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipelines.odt_translation_pipeline import (
    extract_odt_content_to_json, write_translated_content_to_odt)

HEAD = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>')
TAIL = "</office:text></office:body></office:document-content>"


def _roundtrip(tmp_path, body):
    src = str(tmp_path / "in.odt")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", HEAD + body + TAIL)
    src_json = extract_odt_content_to_json(src, str(tmp_path))
    with open(src_json, encoding="utf-8") as f:
        items = json.load(f)
    dst_json = str(tmp_path / "dst.json")
    with open(dst_json, "w", encoding="utf-8") as f:
        json.dump([{"count_src": i["count_src"], "translated": "[" + i["value"] + "]"}
                   for i in items], f)
    out = write_translated_content_to_odt(src, src_json, dst_json, str(tmp_path),
                                          str(tmp_path / "result"))
    content = zipfile.ZipFile(out).read("content.xml").decode("utf-8")
    return [i["value"] for i in items], content[content.index("<office:text>") + 13:content.index("</office:text>")]


def test_footnote_kept_and_translated(tmp_path):
    values, out = _roundtrip(tmp_path, (
        '<text:p>Body text<text:note text:id="n1" text:note-class="footnote">'
        '<text:note-citation>1</text:note-citation><text:note-body>'
        '<text:p>The footnote</text:p></text:note-body></text:note> more.</text:p>'))
    assert values == ["Body text{{INLINE_0}} more.", "The footnote"]
    assert out == ('<text:p>[Body text<text:note text:id="n1" text:note-class="footnote">'
                   '<text:note-citation>1</text:note-citation><text:note-body>'
                   '<text:p>[The footnote]</text:p></text:note-body></text:note> more.]</text:p>')


def test_field_and_span_preserved(tmp_path):
    values, out = _roundtrip(tmp_path, (
        '<text:p>Page <text:page-number>3</text:page-number> is '
        '<text:span text:style-name="B">very important</text:span> text</text:p>'))
    assert values == ["Page {{INLINE_0}} is {{SPAN_0}}very important{{/SPAN_0}} text"]
    assert out == ('<text:p>[Page <text:page-number>3</text:page-number> is '
                   '<text:span text:style-name="B">very important</text:span> text]</text:p>')


def test_text_box_and_annotation_paragraphs_translated(tmp_path):
    _, out = _roundtrip(tmp_path, (
        '<text:p>Caption<draw:frame><draw:text-box><text:p>In box</text:p></draw:text-box>'
        '</draw:frame><office:annotation><dc:creator>Bob</dc:creator>'
        '<text:p>Check this</text:p></office:annotation></text:p>'))
    assert "<text:p>[In box]</text:p>" in out
    assert "<text:p>[Check this]</text:p>" in out
    assert "<dc:creator>Bob</dc:creator>" in out


def test_old_json_without_span_markers(tmp_path):
    # Pre-change src.json/proofread: flattened value, no "spans" key
    from lxml import etree
    from core.pipelines.odt_translation_pipeline import _apply_to_paragraph
    el = etree.fromstring('<text:p xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                          'A <text:span>b</text:span> c</text:p>')
    _apply_to_paragraph(el, "X Y Z")
    assert el.text == "X Y Z" and len(el) == 0
