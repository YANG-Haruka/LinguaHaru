# Work that grew with the size of the whole job instead of the size of one batch
# (found on a 100,000-segment workbook), plus two leaks on the failure paths:
#   * translated_status / the failed list: whole-file read + rewrite per batch
#   * the in-memory result buffer kept forever after a stop or a failure
#   * glossary matching: terms re-sorted and one regex looked up per term per cell
#   * Excel: every cell scanned every merged range
#   * xlwings: Excel left running when the workbook could not be opened
# No network, no models, no Excel.
#
#   python -m pytest tests/test_scale_engine.py
import csv
import json
import os
import sys
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import pytest

import core.engine.base_translator as bt
from core.engine import text_separator as ts
from core.engine import translation_checker as tc
from core.llm.online_translation import HardApiError
from core.pipelines import excel_translation_pipeline as xl


def _seg(d):
    return "```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _count_writes(monkeypatch):
    """Record every whole-file JSON write the checker does."""
    writes = []
    real = tc._atomic_write_json

    def counting(path, obj):
        writes.append(path)
        real(path, obj)
    monkeypatch.setattr(tc, "_atomic_write_json", counting)
    return writes


# --- 1. translated_status / failed list: no whole-file rewrite per batch -----
def test_status_is_written_on_flush_not_per_batch(tmp_path, monkeypatch):
    src, res, fail = (str(tmp_path / n) for n in
                      ("src_deduped_split.json", "dst_translated_split.json", "failed.json"))
    items = [{"count_src": i, "count_split": i, "value": f"Line number {i} here",
              "type": "text", "translated_status": False, "chunk": "1/1"}
             for i in range(1, 61)]
    with open(src, "w", encoding="utf-8") as f:
        json.dump(items, f)
    writes = _count_writes(monkeypatch)

    for batch in range(10):                       # ids 1..50, five per batch
        ids = range(batch * 5 + 1, batch * 5 + 6)
        got = tc.process_translation_results(
            _seg({str(i): f"Line number {i} here" for i in ids}),
            _seg({str(i): f"第{i}行" for i in ids}), src, res, fail, "en", "zh")
        assert len(got) == 5
    assert writes.count(src) == 0                 # was 10: one rewrite per batch

    assert tc.flush_results(res)
    assert writes.count(src) == 1
    on_disk = _load(src)
    assert [x["translated_status"] for x in on_disk] == [True] * 50 + [False] * 10
    for x in on_disk:                             # nothing else in the file changed
        x["translated_status"] = False
    assert on_disk == items

    # Resume reads the status exactly as before: only the rest is segmented.
    segments = ts.stream_segment_json(src, 4096, "s", "u", "p", "en", "zh", None, True)
    todo = [k for s in segments for k in json.loads(ts.clean_json_fence(s[0]))]
    assert todo == [str(i) for i in range(51, 61)]
    tc.invalidate_results(res)
    tc.invalidate_results(fail)


def test_failed_list_is_buffered_and_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "_FLUSH_INTERVAL", 3600)
    fail = str(tmp_path / "failed.json")
    writes = _count_writes(monkeypatch)
    for batch in range(20):
        tc.save_failed_json_without_duplicates(
            fail, [{"count_split": i, "value": f"v{i}"}
                   for i in range(batch * 5 + 1, batch * 5 + 6)])
    tc.save_failed_json_without_duplicates(fail, [{"count_split": "3", "value": "again"}])
    assert writes.count(fail) == 1                # was 21: one rewrite per call

    assert tc.flush_results(fail)
    assert [x["count_split"] for x in _load(fail)] == list(range(1, 101))
    # A dropped buffer is reloaded from disk, so the dedup still sees old ids.
    tc.invalidate_results(fail)
    tc.save_failed_json_without_duplicates(
        fail, [{"count_split": 100, "value": "v100"}, {"count_split": 101, "value": "v101"}])
    assert tc.flush_results(fail)
    assert [x["count_split"] for x in _load(fail)] == list(range(1, 102))
    tc.invalidate_results(fail)


# --- 1 + 2. stop / failure / hard kill, then resume --------------------------
LINES = [f"Sentence number {i} of the quarterly report is written here." for i in range(1, 61)]


def zh(text):
    return "译:" + text


def _translator(tmp_path, continue_mode):
    from core.translators.txt_translator import TxtTranslator
    src = str(tmp_path / "report.txt")
    if not os.path.exists(src):
        with open(src, "w", encoding="utf-8") as f:
            f.write("\n".join(LINES))
    dirs = {n: str(tmp_path / n) for n in ("temp", "result", "log", "history")}
    for p in dirs.values():
        os.makedirs(p, exist_ok=True)
    return src, TxtTranslator(
        src, "fake", False, "", "en", "zh", continue_mode, max_token=768, max_retries=2,
        thread_count=1, glossary_path=None, temp_dir=dirs["temp"], result_dir=dirs["result"],
        session_lang="en", log_dir=dirs["log"], history_dir=dirs["history"])


def _forget_buffers():
    """What a killed process leaves behind: nothing in memory."""
    for name in ("_result_cache", "_result_dirty", "_result_last",
                 "_failed_seen", "_status_pending"):
        getattr(tc, name, {}).clear()


@pytest.mark.parametrize("how", ["stop", "failure", "kill"])
def test_interrupted_run_frees_buffers_and_resumes(tmp_path, monkeypatch, how):
    # Only the first batch reaches the result file through the timer; the rest
    # is in memory until a flush — which a killed process never gets to.
    monkeypatch.setattr(tc, "_FLUSH_INTERVAL", 3600)
    monkeypatch.setattr(bt, "cache_store_validated", lambda *a, **k: None)
    batches = []                                   # ids of every batch sent to the model

    def fake(segments, *a, **k):
        d = json.loads(tc.clean_json(segments))
        if how != "stop" and len(batches) == 3 and not resumed:
            raise HardApiError("no credit left", "insufficient_balance")
        batches.append(list(d))
        return json.dumps({k_: zh(v) for k_, v in d.items()}, ensure_ascii=False), True, None
    monkeypatch.setattr(bt, "translate_text", fake)

    resumed = False
    src, tr = _translator(tmp_path, False)
    if how == "stop":
        def stop_after_three():
            if len(batches) >= 3:
                raise RuntimeError("__stopped__")
        tr.check_stop_requested = stop_after_three
    if how == "kill":                              # no handler runs in a killed process
        tr.save_failed_summary = lambda *a, **k: None
    with pytest.raises((RuntimeError, HardApiError)):
        tr.process(os.path.splitext(src)[0], ".txt")
    assert len(batches) == 3
    assert len(LINES) > 2 * sum(map(len, batches))     # most of the file is still to do

    if how == "kill":
        _forget_buffers()
    else:
        # 2: nothing of this task stays in memory after the final flush
        assert tr.result_split_json_path not in tc._result_cache
        assert tr.failed_json_path not in tc._result_cache
        assert tr.result_split_json_path not in tc._result_dirty
        # ... and the flush wrote the status the resume relies on
        marked = {str(x["count_split"]) for x in _load(tr.src_split_json_path)
                  if x["translated_status"]}
        assert marked == {i for b in batches for i in b}
    saved = {str(x["count_split"]) for x in _load(tr.result_split_json_path)}
    assert saved == {i for b in (batches[:1] if how == "kill" else batches) for i in b}

    resumed = True
    first_run = len(batches)
    src, tr2 = _translator(tmp_path, True)
    out, missing = tr2.process(os.path.splitext(src)[0], ".txt")
    again = {i for b in batches[first_run:] for i in b}
    assert not (again & saved)                     # nothing already on disk is re-sent
    assert not missing
    with open(out, encoding="utf-8-sig") as f:
        assert [x for x in f.read().splitlines() if x.strip()] == [zh(x) for x in LINES]
    # success frees them too
    assert tr2.result_split_json_path not in tc._result_cache
    assert tr2.failed_json_path not in tc._result_cache


def test_resume_does_not_trust_source_fallback_entries(tmp_path):
    """check_and_sort_translations writes source text as the 'translation' of a
    failed item; a later resume must still retry it."""
    src, res = str(tmp_path / "src_deduped_split.json"), str(tmp_path / "dst_translated_split.json")
    with open(src, "w", encoding="utf-8") as f:
        json.dump([{"count_split": i, "value": f"Line {i}", "translated_status": False}
                   for i in (1, 2, 3)], f)
    with open(res, "w", encoding="utf-8") as f:
        json.dump([{"count_split": 1, "original": "Line 1", "translated": "第1行"},
                   {"count_split": 2, "original": "Line 2", "translated": "Line 2"}], f)
    tc.restore_translated_status(src, res)
    assert [x["translated_status"] for x in _load(src)] == [True, False, False]


# --- 3. glossary matching ----------------------------------------------------
GLOSSARY = [
    ("app", "应用"), ("application", "应用程序"), ("Application Server", "应用服务器"),
    ("机器学习", "machine learning"), ("学习", "study"), ("深度学习模型", "deep model"),
    ("C++", "C加加"), ("node.js", "Node"), ("e-mail", "电子邮件"), ("rock 'n' roll", "摇滚"),
    ("GPU", "显卡"), ("gpu", "图形处理器"), ("app", "应用软件"), ("", "empty"),
    ("サーバー", "服务器"), ("한국어", "韩语"), ("ไทย", "泰语"),
    ("café", "咖啡馆"), ("naïve user", "新手"),
    ("版本2", "v2"), ("v2.0", "二点零"), ("100%", "百分百"),
    ("data set", "数据集"), ("dataset", "数据集2"),
]
# Outputs of the per-cell implementation this replaced (longest term first,
# last duplicate wins, whole-word + case-insensitive for Latin, substring for
# CJK / Hangul / Thai / terms with symbols).
EXPECTED = {
    "Open the app and the Application Server.":
        [("Application Server", "应用服务器"), ("application", "应用程序"), ("app", "应用软件")],
    "applications are not apps": [],
    "APP": [("app", "应用软件")],
    "深度学习模型与机器学习":
        [("深度学习模型", "deep model"), ("机器学习", "machine learning"), ("学习", "study")],
    "Install C++ and Node.JS; send an E-MAIL.": [("e-mail", "电子邮件"), ("C++", "C加加")],
    "The gpu and GPU differ": [("GPU", "显卡"), ("gpu", "图形处理器")],
    "サーバーを再起動、한국어와 ไทย": [("サーバー", "服务器"), ("한국어", "韩语"), ("ไทย", "泰语")],
    "A naïve user at the CAFÉ": [("naïve user", "新手"), ("café", "咖啡馆")],
    "版本2 is v2.0 at 100%": [("v2.0", "二点零"), ("100%", "百分百"), ("版本2", "v2")],
    "the data set / dataset / data  set": [("data set", "数据集"), ("dataset", "数据集2")],
    "": [],
    "nothing here": [],
    "rock 'n' roll forever": [("rock 'n' roll", "摇滚")],
    "my_app is not an app-like thing, but app's is": [("app", "应用软件")],
}


def test_glossary_matches_are_unchanged():
    for text, expected in EXPECTED.items():
        assert ts.find_terms_with_hashtable(text, GLOSSARY) == expected, text
        # the single-term rule the QA check uses agrees with the batch matcher
        assert {s for s, _ in GLOSSARY if ts._term_matches(s, text)} ==             {s for s, _ in expected}, text
    # entries given as lists (not tuples) are accepted as before
    assert ts.find_terms_with_hashtable("APP", [list(e) for e in GLOSSARY]) == EXPECTED["APP"]


def test_segmenting_with_a_large_glossary_is_fast(tmp_path):
    """2,000 terms x 300 cells: 28.6 s with the per-cell sort + regex lookups,
    0.5 s with the glossary compiled once. The bound leaves ~8x headroom."""
    glossary = str(tmp_path / "terms.csv")
    with open(glossary, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["en", "zh"])
        for i in range(2000):
            w.writerow([f"valve type {i:04d}" if i % 3 else f"Rotor{i:04d}", f"术语{i}"])
    src = str(tmp_path / "src_deduped_split.json")
    with open(src, "w", encoding="utf-8") as f:
        json.dump([{"count_split": i + 1,
                    "value": f"Replace the valve type {i:04d} before testing ROTOR{i + 2:04d} again."}
                   for i in range(300)], f)
    ts.num_tokens_from_string("warm up the tokenizer")
    start = time.perf_counter()
    segments = ts.stream_segment_json(src, 4096, "s", "u", "p", "en", "zh", glossary, False)
    elapsed = time.perf_counter() - start
    found = {term for s in segments for term, _ in s[2]}
    assert "valve type 0001" in found and "Rotor0003" in found      # ROTOR0003: case-insensitive
    assert "valve type 0000" not in found                           # index 0 is a Rotor term
    assert elapsed < 4.0, f"segmenting took {elapsed:.1f}s"


# --- 4. Excel: merged-cell lookup built once per sheet -----------------------
def test_excel_merged_lookup_is_per_sheet_not_per_cell(tmp_path, monkeypatch):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Parts"
    merged_top_lefts = set()
    for i in range(30):                            # 30 merged pairs in columns A:B
        row = i * 2 + 1
        ws.cell(row=row, column=1, value=f"Merged heading {i}")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        merged_top_lefts.add((row, 1))
    for row in range(1, 101):                      # 200 plain cells in columns D:E
        ws.cell(row=row, column=4, value=f"Plain text {row}")
        ws.cell(row=row, column=5, value=f"More text {row}")
    path = str(tmp_path / "parts.xlsx")
    wb.save(path)

    calls = []
    real = xl.range_boundaries

    def counting(ref):
        calls.append(ref)
        return real(ref)
    monkeypatch.setattr(xl, "range_boundaries", counting)
    cells = [c for c in _load(xl._extract_with_openpyxl(path, str(tmp_path / "temp")))
             if c.get("row")]
    assert len(cells) == 230
    assert {(c["row"], c["column"]) for c in cells if c["is_merged"]} == merged_top_lefts
    assert len(calls) <= 30                        # was one per cell per range (~6,000)


# --- 5. xlwings: Excel is quit even when the workbook cannot be opened -------
def _fake_xlwings(monkeypatch):
    quits = []

    class Books:
        def open(self, path):
            raise RuntimeError("workbook is locked")

    class App:
        def __init__(self, visible=False):
            self.books = Books()

        def quit(self):
            quits.append(1)
    monkeypatch.setitem(sys.modules, "xlwings", types.SimpleNamespace(App=App))
    return quits


def test_xlwings_extract_quits_excel_when_open_fails(tmp_path, monkeypatch):
    quits = _fake_xlwings(monkeypatch)
    with pytest.raises(RuntimeError, match="locked"):
        xl._extract_with_xlwings(str(tmp_path / "book.xlsx"), str(tmp_path))
    assert quits == [1]


def test_xlwings_write_quits_excel_when_open_fails(tmp_path, monkeypatch):
    quits = _fake_xlwings(monkeypatch)
    original, translated = str(tmp_path / "src.json"), str(tmp_path / "dst.json")
    with open(original, "w", encoding="utf-8") as f:
        json.dump([{"count_src": 1, "sheet": "Parts", "row": 1, "column": 1,
                    "value": "Valve", "is_merged": False}], f)
    with open(translated, "w", encoding="utf-8") as f:
        json.dump([{"count_src": 1, "translated": "阀门"}], f, ensure_ascii=False)
    with pytest.raises(RuntimeError, match="locked"):
        xl._write_with_xlwings(str(tmp_path / "book.xlsx"), original, translated,
                               str(tmp_path / "result"), src_lang="en", dst_lang="zh")
    assert quits == [1]
