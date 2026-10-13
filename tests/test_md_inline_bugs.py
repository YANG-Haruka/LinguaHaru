# Regression tests for Markdown bugs: nested-list indentation, prose lines
# starting with inline HTML, unmasked HTML/ref labels, bilingual lazy
# continuation. Fake translator (strips like the engine does), no network.
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipelines.md_translation_pipeline import (
    extract_md_content_to_json, write_translated_content_to_md)

T = "[T]"


def _run(tmp_path, text, bilingual=False):
    src = tmp_path / "doc.md"
    src.write_text(text, encoding="utf-8")
    src_json = extract_md_content_to_json(str(src), str(tmp_path))
    with open(src_json, encoding="utf-8") as f:
        data = json.load(f)
    dst = tmp_path / "dst.json"
    # the engine strips both the source sent and the reply
    dst.write_text(json.dumps([{"count_src": i["count_src"], "translated": T + i["value"].strip()}
                               for i in data]), encoding="utf-8")
    out = write_translated_content_to_md(str(src), src_json, str(dst), str(tmp_path),
                                         str(tmp_path / "out"), bilingual_mode=bilingual)
    with open(out, encoding="utf-8") as f:
        return [i["value"] for i in data], f.read()


def test_nested_list_indentation_kept(tmp_path):
    _, out = _run(tmp_path, "- Top item text\n    - Nested item text\n        - Deeper item text\n")
    assert "\n    [T]- Nested item text\n        [T]- Deeper item text" in out


def test_line_starting_with_inline_html_translated(tmp_path):
    values, out = _run(tmp_path, "<b>Note:</b> this is important text\n")
    assert len(values) == 1 and "<b>" not in values[0]
    assert out.startswith(T + "<b>Note:</b> this is important text")


def test_html_line_attributes_masked(tmp_path):
    values, out = _run(tmp_path, '<p>See <a href="https://x.com/a_b">the link</a> please</p>\n')
    assert "https://x.com" not in values[0]
    assert '<p>[T]See <a href="https://x.com/a_b">the link</a> please</p>' in out


def test_reference_label_and_footnote_masked(tmp_path):
    values, out = _run(tmp_path, "See [the docs][docs-link] and note[^1] here.\n\n"
                                 "[docs-link]: https://x.com\n[^1]: The footnote text.\n")
    assert not any("docs-link" in v or "[^1]" in v for v in values)
    assert "[the docs][docs-link] and note[^1] here." in out
    assert T + "[^1]: The footnote text." in out


def test_bilingual_quote_does_not_swallow_next_line(tmp_path):
    _, out = _run(tmp_path, "Line one text\nLine two text\n", bilingual=True)
    lines = out.split("\n")
    i = lines.index("> Line one text")
    assert lines[i + 1] == "" and lines[i + 2] == T + "Line two text"
