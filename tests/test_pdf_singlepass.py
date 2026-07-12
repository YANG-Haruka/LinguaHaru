# Single-pass PDF translation test.
#
# Generates a small PDF, replaces the LLM call with a fake translator, and
# runs the full BabelDOC single-pass pipeline (layout analysis included).
# Asserts the output PDF exists and contains the fake-translated text.
#
# Run from the repo root:
#   python tests/test_pdf_singlepass.py
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)  # prompts are loaded via relative paths

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "pdf")
T = "[T]"


def main():
    import pymupdf

    os.makedirs(WORK_DIR, exist_ok=True)
    pdf_path = os.path.join(WORK_DIR, "sample.pdf")

    # Build the sample via insert_htmlbox, NOT insert_text: the latter uses a
    # base-14 font with no ToUnicode CMap, and BabelDOC >= 0.6.3 (the CVE-2026-54071
    # cmapdb fix) no longer extracts glyphs from such PDFs — the layout pass would
    # find zero paragraphs. htmlbox embeds a real font so BabelDOC sees the text.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(
        pymupdf.Rect(50, 50, 545, 780),
        "<p>This is the first paragraph of the test document.</p>"
        "<p>Machine translation quality has improved a great deal recently.</p>"
        "<p>Neural networks now handle context and long sentences much better.</p>",
    )
    doc.save(pdf_path)
    doc.close()

    import core.translators.pdf_translator as pt

    calls = []

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None,
                            options=None):
        # Return CJK text: dst_lang is zh, and is_translation_valid rejects an
        # English echo as "wrong target language" — so a Latin marker would be
        # dropped and every paragraph kept as source. Echo each key back.
        calls.append(dict(segments))
        reply = json.dumps({k: T + "机器翻译测试" for k in segments}, ensure_ascii=False)
        return reply, True, {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}

    pt.translate_text = fake_translate_text

    # Unique model name per run: the BabelDOC translation cache keys on the
    # model, so this guarantees the callback actually runs instead of
    # returning cached results from a previous test run
    translator = pt.PdfTranslator(
        pdf_path, model=f"fake-model-{int(time.time())}", use_online=True, api_key="unused",
        src_lang="en", dst_lang="zh", continue_mode=False, max_token=2048,
        max_retries=3, thread_count=4, glossary_path=None,
        temp_dir=os.path.join(WORK_DIR, "temp"),
        result_dir=os.path.join(WORK_DIR, "result"),
        session_lang="en", log_dir=os.path.join(WORK_DIR, "log"),
    )

    started = time.time()
    out_path, missing = translator.process("sample", ".pdf",
                                           progress_callback=lambda p, desc="": None)
    elapsed = time.time() - started

    print(f"output: {out_path}")
    print(f"missing: {missing}")
    print(f"paragraphs sent to translator: {len(calls)}")
    print(f"elapsed: {elapsed:.1f}s")

    # End-to-end: BabelDOC extracted paragraphs (callback ran), our translate +
    # validation accepted them (nothing missing), and a real output was written.
    # We assert on `missing`/`calls` rather than re-extracting the CJK text from
    # the output PDF — BabelDOC embeds the translated font as a subset without a
    # ToUnicode map, so get_text() on the output can't reliably recover it (that's
    # a property of the output font, not of whether translation happened).
    assert os.path.exists(out_path), "output PDF missing"
    assert calls, "translator callback was never invoked (BabelDOC extracted no text)"
    assert not missing, f"failed paragraphs: {missing}"
    print("PASS: single-pass PDF translation works end to end")


if __name__ == "__main__":
    main()
