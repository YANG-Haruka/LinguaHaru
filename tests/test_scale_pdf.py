# Large-PDF handling: a long PDF is translated in parts (bounded memory) and
# still comes out as ONE file; concurrent PDFs of one run never share a BabelDOC
# working directory; the manga repack does not hold every page until the end.
# Fake LLM, no network, layout model on CPU.
import json
import os
import sys
import time

import pytest

pytest.importorskip("babeldoc")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)  # prompts are loaded via relative paths

import core.translators.pdf_translator as pt  # noqa: E402


def _translator(tmp_path, pdf_name):
    # history_dir: process() records every run — keep it out of the real history.
    return pt.PdfTranslator(
        str(tmp_path / pdf_name), model=f"fake-{time.time()}", use_online=True,
        api_key="unused", src_lang="en", dst_lang="zh", continue_mode=False,
        max_token=2048, max_retries=3, thread_count=8, glossary_path=None,
        temp_dir=str(tmp_path / "temp"), result_dir=str(tmp_path / "result"),
        session_lang="en", log_dir=str(tmp_path / "log"),
        history_dir=str(tmp_path / "history"))


def _make_pdf(path, pages, toc=None):
    import pymupdf
    doc = pymupdf.open()
    for p in range(pages):
        # insert_htmlbox embeds a real font; BabelDOC >= 0.6.3 extracts nothing
        # from the base-14 fonts insert_text uses.
        doc.new_page().insert_htmlbox(
            pymupdf.Rect(50, 50, 545, 780),
            f"<p>Page {p + 1} opens with a paragraph about the harbor and its long pier.</p>")
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


# --- long PDFs are translated in parts -------------------------------------- #

def test_only_long_pdfs_are_split():
    assert pt._split_pages_per_part(1) is None
    assert pt._split_pages_per_part(pt._SPLIT_ABOVE_PAGES) is None      # as before
    assert pt._split_pages_per_part(201) == 67          # 3 even parts, no 1-page tail
    assert pt._split_pages_per_part(2000) == 100
    for n in (201, 299, 301, 1234, 2001):
        per = pt._split_pages_per_part(n)
        last = n - per * (-(-n // per) - 1)
        assert per <= pt._SPLIT_PAGES_PER_PART and last > per / 2, (n, per, last)


def test_split_is_decided_from_the_page_count_and_page_range(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_SPLIT_ABOVE_PAGES", 4, raising=False)
    monkeypatch.setattr(pt, "_SPLIT_PAGES_PER_PART", 2, raising=False)
    monkeypatch.setattr(pt.PdfTranslator, "_ensure_layout_model", lambda self: object())
    opts = {"table": False, "ocr": False, "alternating": False, "pages": "",
            "only_translated": False}
    monkeypatch.setattr(pt.PdfTranslator, "_pdf_options", staticmethod(lambda: opts))
    _make_pdf(tmp_path / "five.pdf", 5)
    _make_pdf(tmp_path / "four.pdf", 4)
    engine = pt._CallbackTranslator("en", "zh", lambda text: text, {})

    def pages_per_part(name):
        config = _translator(tmp_path, name)._create_babeldoc_config(engine)
        return getattr(config.split_strategy, "max_pages_per_part", None)

    assert pages_per_part("five.pdf") == 2          # 5 pages -> 3 parts (2 + 2 + 1)
    assert pages_per_part("four.pdf") is None       # at the threshold: one pass
    # BabelDOC 0.6.3 asserts on a part without any page of the range: no split.
    opts["pages"] = "2,5"
    assert pages_per_part("five.pdf") is None


def test_split_pdf_comes_out_as_one_merged_file(tmp_path, monkeypatch):
    """End to end through BabelDOC (layout model on CPU, ~20 s): a 4-page PDF
    forced into 2 parts of 2 pages."""
    monkeypatch.setattr(pt, "_SPLIT_ABOVE_PAGES", 2, raising=False)
    monkeypatch.setattr(pt, "_SPLIT_PAGES_PER_PART", 2, raising=False)
    toc = [[1, "Start", 1], [1, "Second part", 3], [2, "End", 4]]
    _make_pdf(tmp_path / "four.pdf", 4, toc)

    sent = []

    def fake_llm(segments, *a, **kw):
        sent.extend(segments.values())
        return json.dumps({k: "机器翻译测试" for k in segments}, ensure_ascii=False), True, None

    monkeypatch.setattr(pt, "translate_text", fake_llm)

    class _NoCache:                     # keep the real BabelDOC cache.v1.db untouched
        def get(self, key):
            return None

        def set(self, key, value):
            pass

    real_init = pt._CallbackTranslator.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        self.cache = _NoCache()

    monkeypatch.setattr(pt._CallbackTranslator, "__init__", init)

    parts_seen = []
    real_do_translate = pt.do_translate

    def spy(pm, config):
        parts_seen.append(getattr(config.split_strategy, "max_pages_per_part", None))
        return real_do_translate(pm, config)

    monkeypatch.setattr(pt, "do_translate", spy)

    translator = _translator(tmp_path, "four.pdf")
    progress = []
    out_path, missing = translator.process(
        "four", ".pdf", progress_callback=lambda p, desc="": progress.append(p))

    import pymupdf
    assert parts_seen == [2]                                    # really went in parts
    assert not missing
    assert all(any(f"Page {p}" in s for s in sent) for p in (1, 2, 3, 4))
    with pymupdf.open(out_path) as out:
        assert out.page_count == 4                              # one merged PDF
        assert out.get_toc() == toc                             # bookmarks survive
    assert [f for f in os.listdir(tmp_path / "result") if f.endswith(".pdf")] \
        == ["four_en2zh.pdf"]
    assert progress == sorted(progress) and progress[-1] == 1.0  # one bar over all parts
    assert not os.path.exists(translator.babeldoc_working_dir)   # part files removed


# --- one working directory per PDF ----------------------------------------- #

def test_pdfs_of_one_run_do_not_share_a_working_dir(tmp_path):
    a = _translator(tmp_path, "a.pdf")
    b = _translator(tmp_path, "b.pdf")          # same run: same temp_dir
    again = _translator(tmp_path, "a.pdf")      # e.g. a proofread re-export of a.pdf
    dirs = [os.path.normcase(t.babeldoc_working_dir) for t in (a, b, again)]
    assert len(set(dirs)) == 3
    for d in dirs:                              # and none sits inside another
        assert not any(o != d and o.startswith(d + os.sep) for o in dirs)

    # BabelDOC works in <working_dir>/<pdf name>/…; b is mid-translation when a ends.
    b_file = os.path.join(b.babeldoc_working_dir, "b", "part_0", "input.part0.pdf")
    a_file = os.path.join(a.babeldoc_working_dir, "a", "input.pdf")
    for f in (a_file, b_file):
        os.makedirs(os.path.dirname(f))
        open(f, "wb").close()
    a.cleanup()
    assert not os.path.exists(a.babeldoc_working_dir)
    assert os.path.exists(b_file), "finishing one PDF deleted another PDF's working files"


# --- manga repack ---------------------------------------------------------- #

def test_manga_repack_writes_pages_to_disk_as_it_goes(tmp_path, monkeypatch):
    pytest.importorskip("rapidocr")
    fitz = pytest.importorskip("fitz")
    import cv2
    import numpy as np
    import core.translators.manga_pdf_translator as mp

    monkeypatch.setattr(mp, "_FLUSH_PAGES", 2, raising=False)
    file_dir, result_dir = tmp_path / "doc", tmp_path / "result"
    (file_dir / "manga_pages").mkdir(parents=True)
    pages = []
    for i in range(5):                          # page i is (40 + 10*i) px wide
        rel = os.path.join("manga_pages", f"page_{i + 1:03d}.png")
        img = np.full((60, 40 + 10 * i, 3), 255, np.uint8)
        cv2.imencode(".png", img)[1].tofile(str(file_dir / rel))
        pages.append({"page": i + 1, "image": rel, "regions": []})

    parts = lambda: list(file_dir.glob("manga_out.*.part.pdf"))     # a name of its own for each export
    flushed = []                                # was the part file there, per page?
    out = mp.render_manga_pages_to_pdf(
        pages, {}, str(file_dir), str(result_dir), "zh", "book", "ja",
        progress_callback=lambda frac, desc: flushed.append(bool(parts())))

    # pages 1-2 are on disk before page 3 is rendered (old code: never)
    assert flushed == [False, False, True, True, True]
    assert not parts(), "temporary part file left behind"
    with fitz.open(out) as doc:             # all 5 pages, in order (px at 96 dpi -> pt)
        assert [p.rect.width for p in doc] == [30, 37.5, 45, 52.5, 60]
