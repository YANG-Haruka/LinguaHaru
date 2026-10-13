# Regression: boolean/null literals stored as strings must not be translated.
import json

from core.pipelines.json_translation_pipeline import extract_json_content_to_json


def test_string_literals_not_extracted(tmp_path):
    src = tmp_path / "cfg.json"
    src.write_text(json.dumps({"enabled": "true", "debug": "False", "parent": "null",
                               "title": "Settings page"}), encoding="utf-8")
    items = json.load(open(extract_json_content_to_json(str(src), str(tmp_path / "t")),
                           encoding="utf-8"))
    assert [i["value"] for i in items] == ["Settings page"]
