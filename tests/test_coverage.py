# Tests for core.coverage.summarize():
#   - category grouping from raw item "type" strings
#   - translated vs fallback (missing / empty / unchanged translation)
#   - unknown type -> 其它
#   - robustness on missing / bad-JSON files (zeroed report, never raises)
#
# Standalone: prints checks, exits nonzero on failure.
#   python tests/test_coverage.py
import os
import sys
import json
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core import coverage

_failures = []


def check(name, cond):
    print(("OK  " if cond else "FAIL") + f"  {name}")
    if not cond:
        _failures.append(name)


def _write(tmp, name, data):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def test_category_grouping_and_counts():
    with tempfile.TemporaryDirectory() as tmp:
        src = [
            {"count_src": 1, "type": "text", "value": "Hello"},
            {"count_src": 2, "type": "paragraph", "value": "World"},
            {"count_src": 3, "type": "table_cell", "value": "A"},
            {"count_src": 4, "type": "excel_comment", "value": "note"},
            {"count_src": 5, "type": "word_alttext", "value": "logo"},
            {"count_src": 6, "type": "opf_meta", "value": "Title"},
            {"count_src": 7, "type": "header_footer", "value": "Page"},
        ]
        dst = [
            {"count_src": 1, "type": "text", "original": "Hello", "translated": "你好"},
            {"count_src": 2, "type": "paragraph", "original": "World", "translated": "世界"},
            {"count_src": 3, "type": "table_cell", "original": "A", "translated": "甲"},
            {"count_src": 4, "type": "excel_comment", "original": "note", "translated": "备注"},
            {"count_src": 5, "type": "word_alttext", "original": "logo", "translated": "标识"},
            {"count_src": 6, "type": "opf_meta", "original": "Title", "translated": "标题"},
            {"count_src": 7, "type": "header_footer", "original": "Page", "translated": "页"},
        ]
        r = coverage.summarize(_write(tmp, "src.json", src),
                               _write(tmp, "dst_translated.json", dst))
        check("total == 7", r["total"] == 7)
        check("translated == 7", r["translated"] == 7)
        check("fallback == 0", r["fallback"] == 0)
        bc = r["by_category"]
        check("正文 == 2", bc.get("正文") == 2)
        check("表格 == 1", bc.get("表格") == 1)
        check("批注 == 1", bc.get("批注") == 1)
        check("图片说明 == 1", bc.get("图片说明") == 1)
        check("元数据 == 1", bc.get("元数据") == 1)
        check("页眉/页脚 == 1", bc.get("页眉/页脚") == 1)


def test_fallback_counting():
    with tempfile.TemporaryDirectory() as tmp:
        src = [{"count_src": i, "type": "text", "value": f"v{i}"} for i in range(1, 5)]
        dst = [
            {"count_src": 1, "type": "text", "original": "v1", "translated": "翻译1"},  # ok
            {"count_src": 2, "type": "text", "original": "v2", "translated": ""},        # empty
            {"count_src": 3, "type": "text", "original": "v3", "translated": "v3"},      # unchanged
            {"count_src": 4, "type": "text", "original": "v4"},                          # missing
        ]
        r = coverage.summarize(_write(tmp, "src.json", src),
                               _write(tmp, "dst_translated.json", dst))
        check("total == 4", r["total"] == 4)
        check("translated == 1", r["translated"] == 1)
        check("fallback == 3", r["fallback"] == 3)


def test_unknown_type_to_other():
    with tempfile.TemporaryDirectory() as tmp:
        src = [
            {"count_src": 1, "type": "text", "value": "a"},
            {"count_src": 2, "type": "some_brand_new_type", "value": "b"},
        ]
        dst = [
            {"count_src": 1, "type": "text", "original": "a", "translated": "甲"},
            {"count_src": 2, "type": "some_brand_new_type", "original": "b", "translated": "乙"},
        ]
        r = coverage.summarize(_write(tmp, "src.json", src),
                               _write(tmp, "dst_translated.json", dst))
        check("其它 == 1", r["by_category"].get("其它") == 1)
        check("正文 == 1", r["by_category"].get("正文") == 1)


def test_missing_files_zeroed():
    r = coverage.summarize("does_not_exist_src.json", "does_not_exist_dst.json")
    check("missing -> zeroed total", r["total"] == 0)
    check("missing -> zeroed translated", r["translated"] == 0)
    check("missing -> zeroed fallback", r["fallback"] == 0)
    check("missing -> empty by_category", r["by_category"] == {})


def test_bad_json_zeroed():
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "src.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not valid json ]")
        r = coverage.summarize(bad, bad)
        check("bad json -> total 0", r["total"] == 0)
        check("bad json -> no raise (dict returned)", isinstance(r, dict))


def test_dst_missing_uses_src_total():
    with tempfile.TemporaryDirectory() as tmp:
        src = [{"count_src": i, "type": "text", "value": f"v{i}"} for i in range(1, 4)]
        r = coverage.summarize(_write(tmp, "src.json", src), "no_dst.json")
        check("dst missing -> total from src (3)", r["total"] == 3)
        check("dst missing -> all fallback (3)", r["fallback"] == 3)
        check("dst missing -> translated 0", r["translated"] == 0)


def test_format_line():
    line = coverage.format_line(
        {"total": 120, "translated": 120, "fallback": 0,
         "by_category": {"正文": 80, "表格": 40}})
    check("format_line has total", "120 segments" in line)
    check("format_line has category", "正文 80" in line)
    check("format_line has fallback", "0 未翻译" in line)


if __name__ == "__main__":
    test_category_grouping_and_counts()
    test_fallback_counting()
    test_unknown_type_to_other()
    test_missing_files_zeroed()
    test_bad_json_zeroed()
    test_dst_missing_uses_src_total()
    test_format_line()
    if _failures:
        print(f"\n{len(_failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll coverage tests passed.")
