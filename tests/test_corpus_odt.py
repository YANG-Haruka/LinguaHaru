# Corpus tests: ODT with nested tables and text:a hyperlinks.
#
# Run from the repo root:
#   python tests/test_corpus_odt.py
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("odt")

CONTENT_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<office:document-content '
                'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                'xmlns:xlink="http://www.w3.org/1999/xlink">'
                "<office:body><office:text>")
CONTENT_TAIL = "</office:text></office:body></office:document-content>"

MANIFEST = ('<?xml version="1.0"?><manifest:manifest '
            'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
            '<manifest:file-entry manifest:media-type="application/vnd.oasis.opendocument.text" '
            'manifest:full-path="/"/></manifest:manifest>')


def _build_odt(path, body):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", CONTENT_HEAD + body + CONTENT_TAIL,
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", MANIFEST, compress_type=zipfile.ZIP_DEFLATED)


def _translate(name, body):
    from core.pipelines.odt_translation_pipeline import (
        extract_odt_content_to_json, write_translated_content_to_odt)
    src = os.path.join(WORK_DIR, name)
    _build_odt(src, body)
    src_json = extract_odt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_odt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        content = z.read("content.xml").decode("utf-8")
    return first, content


def test_odt_nested_table():
    print("ODT: paragraph inside a table inside a table-cell of an outer table")
    first, content = _translate("nested.odt", (
        "<text:p>Intro paragraph before the tables.</text:p>"
        '<table:table table:name="Outer">'
        "<table:table-row>"
        "<table:table-cell><text:p>Outer cell first paragraph</text:p>"
        '<table:table table:name="Inner">'
        "<table:table-row>"
        "<table:table-cell><text:p>Inner nested cell text</text:p></table:table-cell>"
        "<table:table-cell><text:p>Second inner cell text</text:p></table:table-cell>"
        "</table:table-row></table:table>"
        "</table:table-cell>"
        "<table:table-cell><text:p>Outer second cell text</text:p></table:table-cell>"
        "</table:table-row></table:table>"
        "<text:p>Closing paragraph after the tables.</text:p>"))

    check("mimetype first and stored",
          first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED)
    check("intro and closing paragraphs translated",
          T + "Intro paragraph before the tables." in content
          and T + "Closing paragraph after the tables." in content, content)
    check("outer table cells translated",
          T + "Outer cell first paragraph" in content
          and T + "Outer second cell text" in content, content)
    check("inner nested table cells translated",
          T + "Inner nested cell text" in content
          and T + "Second inner cell text" in content, content)
    check("nested table structure intact (Outer and Inner names survive)",
          'table:name="Outer"' in content and 'table:name="Inner"' in content
          and content.count("<table:table ") == 2, content)


def test_odt_hyperlinks():
    print("ODT: text:a hyperlinks rebuilt in place with attributes")
    first, content = _translate("links.odt", (
        "<text:p>Before the link "
        '<text:a xlink:type="simple" xlink:href="https://example.com/spec">'
        "official spec page</text:a> and after "
        '<text:a xlink:href="#section2">an internal anchor</text:a> the tail ends.</text:p>'
        "<text:p>Paragraph without any links at all.</text:p>"))

    check("paragraph with links translated", T + "Before the link" in content, content)
    check("both hrefs survive",
          'xlink:href="https://example.com/spec"' in content
          and 'xlink:href="#section2"' in content, content)
    check("xlink:type attribute survives", 'xlink:type="simple"' in content, content)
    check("link texts stay inside their text:a elements",
          ">official spec page</text:a>" in content
          and ">an internal anchor</text:a>" in content, content)
    pos = [content.find("Before the link"), content.find("official spec page"),
           content.find("and after"), content.find("an internal anchor"),
           content.find("the tail ends")]
    check("text order around the links preserved",
          all(p >= 0 for p in pos) and pos == sorted(pos), str(pos))
    check("no HLINK placeholders leaked", "HLINK" not in content, content)
    check("plain paragraph translated",
          T + "Paragraph without any links at all." in content, content)


STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-styles '
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
    "<office:master-styles>"
    '<style:master-page style:name="Standard">'
    "<style:header><text:p>Header text on every page</text:p></style:header>"
    "<style:footer><text:p>Footer copyright notice here</text:p></style:footer>"
    "</style:master-page></office:master-styles></office:document-styles>")

META_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-meta '
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">'
    "<office:meta>"
    "<dc:title>The Document Title Here</dc:title>"
    "<dc:creator>Original Author Name</dc:creator>"
    "<dc:description>A summary describing the document.</dc:description>"
    "<meta:keyword>First keyword phrase</meta:keyword>"
    "</office:meta></office:document-meta>")


def test_odt_headers_footers_and_meta():
    print("ODT: styles.xml header/footer + meta.xml title/desc/keyword translated")
    from core.pipelines.odt_translation_pipeline import (
        extract_odt_content_to_json, write_translated_content_to_odt)

    src = os.path.join(WORK_DIR, "headfoot.odt")
    body = "<text:p>Main body paragraph of the document.</text:p>"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", CONTENT_HEAD + body + CONTENT_TAIL,
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("styles.xml", STYLES_XML, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("meta.xml", META_XML, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", MANIFEST, compress_type=zipfile.ZIP_DEFLATED)

    src_json = extract_odt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_odt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with zipfile.ZipFile(out) as z:
        content = z.read("content.xml").decode("utf-8")
        styles = z.read("styles.xml").decode("utf-8")
        meta = z.read("meta.xml").decode("utf-8")

    check("body paragraph translated", T + "Main body paragraph of the document." in content,
          content)
    check("header text translated", T + "Header text on every page" in styles, styles)
    check("footer text translated", T + "Footer copyright notice here" in styles, styles)
    check("meta title translated", T + "The Document Title Here" in meta, meta)
    check("meta description translated", T + "A summary describing the document." in meta, meta)
    check("meta keyword translated", T + "First keyword phrase" in meta, meta)
    check("meta creator (author) NOT translated",
          "<dc:creator>Original Author Name</dc:creator>" in meta, meta)


DRAW_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<office:document-content '
             'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
             'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
             'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
             'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
             'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
             'xmlns:xlink="http://www.w3.org/1999/xlink">'
             "<office:body><office:text>")


def test_odt_image_frame_alt():
    print("ODT: draw:frame image survives; svg:title/svg:desc alt translated")
    from core.pipelines.odt_translation_pipeline import (
        extract_odt_content_to_json, write_translated_content_to_odt)

    # A normal paragraph; a paragraph mixing text + a draw:frame image with a
    # hyperlink; and an image-only paragraph (frame is the whole paragraph).
    frame_inline = (
        '<draw:frame draw:name="Inline" svg:width="2cm" svg:height="1cm">'
        '<draw:image xlink:href="Pictures/inline.png" xlink:type="simple"/>'
        "<svg:title>Inline image title</svg:title>"
        "<svg:desc>Inline image description</svg:desc>"
        "</draw:frame>")
    frame_only = (
        '<draw:frame draw:name="Standalone" svg:width="5cm" svg:height="3cm">'
        '<draw:image xlink:href="Pictures/standalone.png"/>'
        "<svg:title>Standalone picture caption</svg:title>"
        "<svg:desc>Standalone picture longer description text</svg:desc>"
        "</draw:frame>")
    body = (
        "<text:p>Plain paragraph with only text.</text:p>"
        "<text:p>Lead text " + frame_inline + " and a "
        '<text:a xlink:href="https://example.com/img">link</text:a> after.</text:p>'
        "<text:p>" + frame_only + "</text:p>")

    src = os.path.join(WORK_DIR, "image.odt")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", DRAW_HEAD + body + CONTENT_TAIL,
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", MANIFEST, compress_type=zipfile.ZIP_DEFLATED)

    src_json = extract_odt_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_odt(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with zipfile.ZipFile(out) as z:
        content = z.read("content.xml").decode("utf-8")

    check("normal paragraph text translated",
          T + "Plain paragraph with only text." in content, content)
    check("paragraph lead text translated", T + "Lead text" in content, content)
    check("inline draw:frame survives (image not lost)",
          'draw:name="Inline"' in content
          and 'xlink:href="Pictures/inline.png"' in content, content)
    check("standalone (image-only paragraph) draw:frame survives",
          'draw:name="Standalone"' in content
          and 'xlink:href="Pictures/standalone.png"' in content, content)
    check("both draw:image elements present",
          content.count("<draw:image") == 2, content)
    check("svg:title alt text translated (both)",
          T + "Inline image title" in content
          and T + "Standalone picture caption" in content, content)
    check("svg:desc alt text translated (both)",
          T + "Inline image description" in content
          and T + "Standalone picture longer description text" in content, content)
    check("hyperlink in image paragraph survives with href + link text",
          'xlink:href="https://example.com/img"' in content
          and ">link</text:a>" in content, content)
    check("no INLINE placeholders leaked", "INLINE" not in content, content)
    check("no HLINK placeholders leaked", "HLINK" not in content, content)


if __name__ == "__main__":
    run([test_odt_nested_table, test_odt_hyperlinks,
         test_odt_headers_footers_and_meta, test_odt_image_frame_alt])
