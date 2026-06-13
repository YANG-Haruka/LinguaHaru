# Corpus tests: JSON with deep nesting, arrays of objects, empty strings.
#
# Run from the repo root:
#   python tests/test_corpus_json.py
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("json")


def test_json_deep_and_arrays():
    print("JSON: 5-level nesting, arrays of objects, empty strings, mixed types")
    from pipeline.json_translation_pipeline import (
        extract_json_content_to_json, write_translated_content_to_json)

    src = os.path.join(WORK_DIR, "deep.json")
    payload = {
        "level1": {
            "level2": {
                "level3": {
                    "level4": {
                        "level5": "Deeply nested message text",
                        "empty": "",
                    },
                    "number": 3.14,
                },
            },
            "items": [
                {"label": "First item label", "id": "AB-1234", "enabled": True},
                {"label": "Second item label", "note": "", "children": [
                    {"label": "Grandchild item label"},
                ]},
            ],
        },
        "empty_top": "",
        "blank": "   ",
        "null_value": None,
        "flags": [True, False, None],
        "greeting": "こんにちは世界",
    }
    with open(src, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    src_json = extract_json_content_to_json(src, TEMP_DIR)
    with open(src_json, encoding="utf-8") as f:
        extracted = [i["value"] for i in json.load(f)]

    check("empty strings are not sent for translation",
          all(v.strip() for v in extracted), str(extracted))

    dst_json = fake_translate(src_json)
    out = write_translated_content_to_json(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8") as f:
        result = json.load(f)

    check("level-5 nested value translated",
          result["level1"]["level2"]["level3"]["level4"]["level5"]
          == T + "Deeply nested message text", str(result))
    check("array-of-objects values translated",
          result["level1"]["items"][0]["label"] == T + "First item label"
          and result["level1"]["items"][1]["label"] == T + "Second item label", str(result))
    check("nested array object value translated",
          result["level1"]["items"][1]["children"][0]["label"] == T + "Grandchild item label",
          str(result))
    check("CJK value translated", result["greeting"] == T + "こんにちは世界", str(result))

    # Structure / non-translatables untouched
    check("empty strings untouched",
          result["level1"]["level2"]["level3"]["level4"]["empty"] == ""
          and result["empty_top"] == "" and result["level1"]["items"][1]["note"] == "",
          str(result))
    check("whitespace-only string untouched", result["blank"] == "   ", repr(result["blank"]))
    check("ID-like code untouched", result["level1"]["items"][0]["id"] == "AB-1234", str(result))
    check("numbers / booleans / nulls untouched",
          result["level1"]["level2"]["level3"]["number"] == 3.14
          and result["level1"]["items"][0]["enabled"] is True
          and result["null_value"] is None
          and result["flags"] == [True, False, None], str(result))
    check("keys untouched (no [T] in any key)",
          T not in json.dumps(list(_all_keys(result)), ensure_ascii=False), str(result))


def _all_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_keys(v)


if __name__ == "__main__":
    run([test_json_deep_and_arrays])
