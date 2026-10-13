# Regression tests for CSV write-back bugs (identity "translation", no network).
import csv
import json
import os

from core.pipelines.csv_translation_pipeline import (
    extract_csv_content_to_json, write_translated_content_to_csv)


def _roundtrip(tmp_path, name, raw_bytes):
    src = tmp_path / name
    src.write_bytes(raw_bytes)
    temp, result = str(tmp_path / "temp"), str(tmp_path / "result")
    src_json = extract_csv_content_to_json(str(src), temp)
    items = json.load(open(src_json, encoding="utf-8"))
    dst_json = os.path.join(os.path.dirname(src_json), "dst.json")
    json.dump([{"count_src": i["count_src"], "original": i["value"], "translated": i["value"]}
               for i in items], open(dst_json, "w", encoding="utf-8"), ensure_ascii=False)
    out = write_translated_content_to_csv(str(src), src_json, dst_json, temp, result, "zh", "en")
    return items, open(out, "rb").read()


def test_bom_source_keeps_bom_for_excel(tmp_path):
    _, out = _roundtrip(tmp_path, "bom.csv", "\ufeffname,desc\r\n苹果,红色的水果\r\n".encode())
    assert out.startswith(b"\xef\xbb\xbf")


def test_gbk_source_gets_bom_for_excel(tmp_path):
    _, out = _roundtrip(tmp_path, "gbk.csv", "名称,描述\r\n苹果,红色的水果\r\n".encode("gbk"))
    assert out.startswith(b"\xef\xbb\xbf")
    assert "苹果" in out.decode("utf-8-sig")


def test_plain_utf8_source_has_no_bom(tmp_path):
    _, out = _roundtrip(tmp_path, "u8.csv", "id,name\r\n1,苹果\r\n".encode())
    assert not out.startswith(b"\xef\xbb\xbf")


def test_formula_and_literal_cells_untouched(tmp_path):
    items, out = _roundtrip(tmp_path, "f.csv",
                            b"label,total,flag\r\nSum of items,=SUM(B1:B3),TRUE\r\nNote,- bullet item,null\r\n")
    values = {i["value"] for i in items}
    assert "=SUM(B1:B3)" not in values and "TRUE" not in values and "null" not in values
    rows = list(csv.reader(out.decode("utf-8").splitlines()))
    assert rows[1][1] == "=SUM(B1:B3)"
    assert rows[2][1] == "- bullet item"  # source lead char kept, no injected space


def test_translation_introducing_formula_char_is_still_escaped(tmp_path):
    src = tmp_path / "x.csv"
    src.write_bytes(b"a,b\r\nHello there,World peace\r\n")
    temp, result = str(tmp_path / "temp"), str(tmp_path / "result")
    src_json = extract_csv_content_to_json(str(src), temp)
    items = json.load(open(src_json, encoding="utf-8"))
    dst_json = os.path.join(os.path.dirname(src_json), "dst.json")
    json.dump([{"count_src": i["count_src"], "translated": "=HYPERLINK(1)"} for i in items],
              open(dst_json, "w", encoding="utf-8"))
    out = write_translated_content_to_csv(str(src), src_json, dst_json, temp, result)
    rows = list(csv.reader(open(out, encoding="utf-8", newline="")))
    assert rows[1][0] == " =HYPERLINK(1)"
