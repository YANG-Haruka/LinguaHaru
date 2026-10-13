# Regression tests for EPUB bugs: nested nav.xhtml TOC titles, HTML named
# entities in XHTML, comment tails. Fake translator, no network.
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipelines.epub_translation_pipeline import (
    extract_epub_content_to_json, write_translated_content_to_epub)

T = "[T]"
XHTML = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>'


def _build(path, docs):
    manifest = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>'
                       for i, n in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:'
                   'opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0"><metadata/><manifest>{manifest}</manifest><spine>{spine}</spine></package>')
        for name, body in docs.items():
            z.writestr(name, XHTML.format(body))


def _run(tmp_path, docs):
    src = str(tmp_path / "book.epub")
    _build(src, docs)
    src_json = extract_epub_content_to_json(src, str(tmp_path))
    with open(src_json, encoding="utf-8") as f:
        data = json.load(f)
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps([{"count_src": i["count_src"], "translated": T + i["value"]}
                               for i in data]), encoding="utf-8")
    out = write_translated_content_to_epub(src, src_json, str(dst), str(tmp_path), str(tmp_path / "out"))
    with zipfile.ZipFile(out) as z:
        return data, {n: z.read(n).decode("utf-8") for n in docs}


def test_nested_nav_part_title_translated(tmp_path):
    nav = ('<nav><ol><li><a href="c1.xhtml">Part One</a><ol>'
           '<li><a href="c1.xhtml#s1">Chapter One</a></li></ol></li></ol></nav>')
    data, out = _run(tmp_path, {"nav.xhtml": nav})
    assert any("Part One" in i["value"] for i in data)
    nav_out = out["nav.xhtml"]
    assert '<a href="c1.xhtml">Part One</a>' in nav_out
    assert nav_out.index(T) < nav_out.index("Part One")
    assert '<a href="c1.xhtml#s1">Chapter One</a>' in nav_out


def test_inline_head_does_not_shift_block_items(tmp_path):
    # head_inline items use their own index, so block items (and older src.json) stay aligned
    nav = ('<ol><li><a href="a.xhtml">Part One</a><ol><li>Chapter text one</li></ol></li>'
           '<li>Second entry text</li></ol>')
    data, out = _run(tmp_path, {"c.xhtml": nav})
    blocks = [i for i in data if i["type"] == "text"]
    assert [b["value"] for b in blocks] == ["Chapter text one", "Second entry text"]
    assert T + "Chapter text one" in out["c.xhtml"] and T + "Second entry text" in out["c.xhtml"]


def test_named_entities_preserved(tmp_path):
    data, out = _run(tmp_path, {"c.xhtml": "<p>Price:&nbsp;100 dollars here</p><div>wait&hellip;</div>"})
    assert data[0]["value"] == "Price:\xa0100 dollars here"
    assert "&#8230;" in out["c.xhtml"] or "…" in out["c.xhtml"]


def test_comment_tail_not_duplicated(tmp_path):
    _, out = _run(tmp_path, {"c.xhtml": "<p>Hello<!-- note --> world and more</p>"})
    assert out["c.xhtml"].count("world and more") == 1
