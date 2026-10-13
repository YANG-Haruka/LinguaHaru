# Regression tests for HTML block extraction / write-back bugs (shared with
# EPUB via epub_translation_pipeline helpers). Fake translator, no network.
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipelines.html_translation_pipeline import (
    extract_html_content_to_json, write_translated_content_to_html)

T = "[T]"


def _run(tmp_path, html, encoding="utf-8", bilingual=False):
    src = tmp_path / "page.html"
    src.write_bytes(html.encode(encoding))
    src_json = extract_html_content_to_json(str(src), str(tmp_path))
    with open(src_json, encoding="utf-8") as f:
        data = json.load(f)
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps([{"count_src": i["count_src"], "translated": T + i["value"]}
                               for i in data]), encoding="utf-8")
    out = write_translated_content_to_html(str(src), src_json, str(dst), str(tmp_path),
                                           str(tmp_path / "out"), bilingual_mode=bilingual)
    with open(out, encoding="utf-8") as f:
        return [i["value"] for i in data], f.read()


def test_linked_image_not_lost(tmp_path):
    _, out = _run(tmp_path, '<html><body><p><a href="big.jpg"><img src="small.jpg"/></a>'
                            ' Caption text here</p></body></html>')
    assert '<a href="big.jpg"><img src="small.jpg"></a>' in out
    assert T in out


def test_comment_tail_not_duplicated(tmp_path):
    _, out = _run(tmp_path, "<html><body><p>Hello<!-- note --> world and more</p></body></html>")
    assert out.count("world and more") == 1


def test_inline_code_kept_verbatim(tmp_path):
    values, out = _run(tmp_path, "<html><body><p>Call <code>get_value()</code> to read it</p>"
                                 "<table><tr><td><code>npm install</code></td>"
                                 "<td>Install it</td></tr></table></body></html>")
    assert not any("get_value" in v or "npm install" in v for v in values)
    assert "<code>get_value()</code>" in out and "<code>npm install</code>" in out


def test_footnote_sup_kept(tmp_path):
    _, out = _run(tmp_path, '<html><body><p>Text with note<sup><a href="#fn1">1</a></sup>'
                            ' continues here</p></body></html>')
    assert '<sup><a href="#fn1">1</a></sup>' in out


def test_ruby_reading_not_merged(tmp_path):
    values, _ = _run(tmp_path, "<html><body><p><ruby>漢<rt>かん</rt></ruby>字の勉強をする</p></body></html>")
    assert values == ["漢字の勉強をする"]


def test_nested_menu_head_link_translated(tmp_path):
    _, out = _run(tmp_path, '<html><body><ul><li><a href="#p1">Part One</a><ul>'
                            '<li><a href="#c1">Chapter One</a></li></ul></li></ul></body></html>')
    assert T in out.split("Part One")[0].rsplit("<li>", 1)[1]
    assert '<a href="#p1">Part One</a>' in out


def test_xml_declaration_does_not_crash(tmp_path):
    _, out = _run(tmp_path, '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
                            "<body><p>Hello world text</p></body></html>")
    assert T + "Hello world text" in out


def test_non_utf8_meta_charset_rewritten(tmp_path):
    _, out = _run(tmp_path, '<html><head><meta charset="gbk"></head><body><p>你好世界文本</p></body></html>',
                  encoding="gbk")
    assert 'charset="utf-8"' in out and "gbk" not in out


def test_bilingual_table_cell_and_list_item_stay_single(tmp_path):
    _, out = _run(tmp_path, "<html><body><table><tr><td>First cell</td><td>Second cell</td></tr></table>"
                            "<ol><li>Step one text</li></ol></body></html>", bilingual=True)
    assert out.count("<td") == 2 and out.count("<li") == 1
    assert "First cell" in out.replace(T + "First cell", "")
