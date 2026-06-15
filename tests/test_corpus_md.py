# Corpus tests: Markdown structures.
#   - nested blockquotes          - fenced code with language
#   - inline code                 - reference-style links
#   - images                      - task lists
#   - aligned pipe tables
#
# The MD pipeline is line-based: each translatable line is sent to the
# translator WITH its markdown markers (>, -, #, |, backticks, link syntax),
# and the prompt instructs the model to keep markup. The fake translator
# prefixes the whole line, so markers must survive verbatim after the [T].
#
# Run from the repo root:
#   python tests/test_corpus_md.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("md")

SOURCE = """# Top level heading

> Outer quote first line
> > Inner nested quote line
> > > Third level quote line

```python
def untouched_code():
    return "this string must not be translated"
```

Inline `code_span` inside a normal sentence here.

Reference link to [the documentation][docs] in running text.

[docs]: https://example.com/documentation "Documentation title"
[plain]: https://example.com/plain

![Diagram of the system](images/diagram.png)

- [ ] Open task item text
- [x] Completed task item text

| Left column | Center column | Right column |
|:------------|:-------------:|-------------:|
| Alpha cell  | Beta cell     | 42           |

Closing paragraph after the table.
"""


def test_md_structures():
    print("MD: blockquotes, fenced code, inline code, ref links, images, tasks, tables")
    from core.pipelines.md_translation_pipeline import (
        extract_md_content_to_json, write_translated_content_to_md)

    src = os.path.join(WORK_DIR, "structures.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write(SOURCE)

    src_json = extract_md_content_to_json(src, TEMP_DIR)
    import json
    with open(src_json, encoding="utf-8") as f:
        extracted = [i["value"] for i in json.load(f)]

    check("fenced code content NOT extracted",
          not any("untouched_code" in v for v in extracted), str(extracted))
    check("reference link definitions NOT extracted",
          not any("example.com" in v for v in extracted), str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_md(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                         src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()
    lines = result.split("\n")

    # --- translation marker on every translatable line ---
    check("heading line translated", T + "# Top level heading" in result, result)
    check("nested blockquote lines translated with markers intact",
          T + "> Outer quote first line" in result
          and T + "> > Inner nested quote line" in result
          and T + "> > > Third level quote line" in result, result)
    check("inline-code sentence translated, backticks survive",
          T + "Inline `code_span` inside a normal sentence here." in result, result)
    check("reference link USAGE translated with [text][id] syntax intact",
          T + "Reference link to [the documentation][docs] in running text." in result, result)
    check("image line translated, image path untouched",
          T + "![Diagram of the system](images/diagram.png)" in result, result)
    check("task list lines translated with checkbox markers intact",
          T + "- [ ] Open task item text" in result
          and T + "- [x] Completed task item text" in result, result)
    check("table data row translated with pipes intact",
          T + "| Alpha cell  | Beta cell     | 42           |" in result, result)
    check("closing paragraph translated",
          T + "Closing paragraph after the table." in result, result)

    # --- structure untouched ---
    check("fenced code block byte-identical (incl. language tag)",
          "```python\ndef untouched_code():\n"
          '    return "this string must not be translated"\n```' in result, result)
    check("reference definitions byte-identical",
          '[docs]: https://example.com/documentation "Documentation title"' in lines
          and "[plain]: https://example.com/plain" in lines, result)
    check("table alignment row untouched",
          "|:------------|:-------------:|-------------:|" in lines, result)
    check("blank-line structure preserved (same line count)",
          len(lines) == len(SOURCE.split("\n")),
          f"{len(lines)} vs {len(SOURCE.split(chr(10)))}")


FRONT_MATTER_SRC = """---
title: My Document Title
tags: [alpha, beta]
---

# Real heading here

~~~js
const x = "do not translate this code line";
~~~

Normal paragraph to translate.
"""


def test_md_frontmatter_and_tilde_fence():
    print("MD: YAML front-matter + ~~~ fenced code are NOT translated")
    from core.pipelines.md_translation_pipeline import (
        extract_md_content_to_json, write_translated_content_to_md)
    import json

    src = os.path.join(WORK_DIR, "frontmatter.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write(FRONT_MATTER_SRC)

    src_json = extract_md_content_to_json(src, TEMP_DIR)
    with open(src_json, encoding="utf-8") as f:
        extracted = [i["value"] for i in json.load(f)]
    check("front-matter keys NOT extracted",
          not any("title:" in v or "tags:" in v for v in extracted), str(extracted))
    check("~~~ code content NOT extracted",
          not any("do not translate" in v for v in extracted), str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_md(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                         src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = f.read()
    check("front-matter block byte-identical",
          "---\ntitle: My Document Title\ntags: [alpha, beta]\n---" in result, result)
    check("~~~ code block byte-identical",
          '~~~js\nconst x = "do not translate this code line";\n~~~' in result, result)
    check("real heading translated", T + "# Real heading here" in result, result)
    check("paragraph translated", T + "Normal paragraph to translate." in result, result)


if __name__ == "__main__":
    run([test_md_structures, test_md_frontmatter_and_tilde_fence])
