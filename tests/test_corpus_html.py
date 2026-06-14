# Corpus tests: HTML structures.
#   - rowspan/colspan table attributes preserved
#   - multiple anchors in one paragraph rebuilt in place
#   - nested lists (li containing ul) and definition lists
#   - pre/code behavior (documented)
#
# Run from the repo root:
#   python tests/test_corpus_html.py
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("html")


def _translate(name, html):
    from core.pipelines.html_translation_pipeline import (
        extract_html_content_to_json, write_translated_content_to_html)
    src = os.path.join(WORK_DIR, name)
    with open(src, "w", encoding="utf-8") as f:
        f.write(html)
    src_json = extract_html_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_html(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        return f.read()


def test_html_spans_table():
    print("HTML: rowspan/colspan attributes preserved")
    result = _translate("spans.html", """<!DOCTYPE html><html><body>
<table border="1">
<tr><td colspan="2">Header spanning two columns</td><td rowspan="2">Tall side cell</td></tr>
<tr><td>Lower left cell</td><td>Lower middle cell</td></tr>
</table>
</body></html>""")

    check("colspan attribute survives", 'colspan="2"' in result, result)
    check("rowspan attribute survives", 'rowspan="2"' in result, result)
    check("spanned cells translated",
          T + "Header spanning two columns" in result and T + "Tall side cell" in result, result)
    check("normal cells translated",
          T + "Lower left cell" in result and T + "Lower middle cell" in result, result)
    check("table structure intact (2 rows, 4 cells)",
          result.count("<tr>") == 2 and result.count("<td") == 4, result)


def test_html_multiple_anchors():
    print("HTML: multiple anchors in one paragraph rebuilt in place")
    result = _translate("anchors.html", """<!DOCTYPE html><html><body>
<p>Start text <a href="https://example.com/one" class="x">first link</a> middle text \
<a href="https://example.com/two" id="l2">second link</a> and \
<a href="#local">third anchor</a> end text.</p>
</body></html>""")

    check("all three hrefs survive",
          all(h in result for h in ('href="https://example.com/one"',
                                    'href="https://example.com/two"', 'href="#local"')), result)
    check("anchor attributes (class/id) survive",
          'class="x"' in result and 'id="l2"' in result, result)
    check("link texts inside their anchors",
          ">first link</a>" in result and ">second link</a>" in result
          and ">third anchor</a>" in result, result)
    check("paragraph translated with [T] prefix", T + "Start text" in result, result)
    body = result[result.find("<p"):]
    order = [body.find("first link"), body.find("middle text"),
             body.find("second link"), body.find("third anchor"), body.find("end text")]
    check("text order preserved around the anchors",
          all(a >= 0 for a in order) and order == sorted(order), str(order))
    check("no HLINK placeholders leaked", "HLINK" not in result, result)


def test_html_nested_and_definition_lists():
    print("HTML: nested lists with head text + definition lists")
    result = _translate("lists.html", """<!DOCTYPE html><html><body>
<ul>
<li>Parent item head text<ul><li>Nested child item one</li><li>Nested child item two</li></ul></li>
<li>Plain sibling item</li>
</ul>
<dl>
<dt>First term name</dt><dd>First term definition text</dd>
<dt>Second term name</dt><dd>Second term definition text</dd>
</dl>
</body></html>""")

    check("parent li head text translated (not lost, children kept)",
          T + "Parent item head text" in result, result)
    check("nested children translated",
          T + "Nested child item one" in result and T + "Nested child item two" in result, result)
    check("plain sibling translated", T + "Plain sibling item" in result, result)
    check("nested <ul> structure survives", result.count("<ul>") == 2, result)
    check("definition terms translated",
          T + "First term name" in result and T + "Second term name" in result, result)
    check("definition descriptions translated",
          T + "First term definition text" in result
          and T + "Second term definition text" in result, result)


def test_html_pre_code():
    print("HTML: pre/code behavior")
    result = _translate("precode.html", """<!DOCTYPE html><html><body>
<p>Run the <code>make build</code> command now.</p>
<pre>
$ make build
building target alpha...
</pre>
<pre><code class="language-sh">echo untouched literal text</code></pre>
</body></html>""")

    # Top-level <pre> is not a translatable block: its content stays verbatim.
    check("top-level <pre> content untouched",
          "$ make build\nbuilding target alpha..." in result and
          T + "$ make build" not in result, result)
    check("<pre><code> content untouched, class attribute survives",
          "echo untouched literal text" in result
          and T + "echo untouched literal text" not in result
          and 'class="language-sh"' in result, result)
    # Known limitation: a <p> with mixed inline content (here <code>) is
    # replaced wholesale - the text is translated but the inline <code> tag
    # is dropped ("losing inline tags beats keeping the source language").
    check("paragraph with inline code translated (code tag dropped - known limitation)",
          T + "Run the make build command now." in result, result)


if __name__ == "__main__":
    run([test_html_spans_table, test_html_multiple_anchors,
         test_html_nested_and_definition_lists, test_html_pre_code])
