# Corpus test: 2-page multi-paragraph PDF through the full BabelDOC
# single-pass pipeline (layout analysis included), with the LLM call
# replaced by the fake [T]-prefix translator.
#
# Run from the repo root:
#   python tests/test_corpus_pdf.py
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)  # prompts are loaded via relative paths

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "corpus", "pdf")
T = "[T]"

PAGE1 = ["The first paragraph introduces the purpose of this document.",
         "A second paragraph continues with additional background detail.",
         "The third paragraph on page one concludes the introduction."]
PAGE2 = ["Page two opens with a fresh paragraph about implementation.",
         "Another paragraph describes the evaluation methodology used.",
         "The closing paragraph summarizes the overall findings."]


def main():
    import pymupdf

    os.makedirs(WORK_DIR, exist_ok=True)
    pdf_path = os.path.join(WORK_DIR, "twopage.pdf")

    doc = pymupdf.open()
    page1 = doc.new_page()
    for i, text in enumerate(PAGE1):
        page1.insert_text((72, 100 + i * 50), text, fontsize=12)
    page2 = doc.new_page()
    for i, text in enumerate(PAGE2):
        page2.insert_text((72, 100 + i * 50), text, fontsize=12)
    doc.save(pdf_path)
    doc.close()

    import core.translators.pdf_translator as pt

    calls = []

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None):
        reply = {}
        for key, value in segments.items():
            calls.append(value)
            reply[key] = T + value
        return (json.dumps(reply, ensure_ascii=False), True,
                {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})

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
    out_path, missing = translator.process("twopage", ".pdf",
                                           progress_callback=lambda p, desc="": None)
    elapsed = time.time() - started

    print(f"output: {out_path}")
    print(f"missing: {missing}")
    print(f"segments sent to translator: {len(calls)}")
    print(f"elapsed: {elapsed:.1f}s")

    assert os.path.exists(out_path), "output PDF missing"
    assert not missing, f"failed paragraphs: {missing}"
    assert len(calls) >= 6, f"expected all 6 paragraphs to reach the translator: {calls}"
    sent = " ".join(calls)
    for text in PAGE1 + PAGE2:
        assert text in sent, f"paragraph never sent to translator: {text!r}"

    out_doc = pymupdf.open(out_path)
    assert out_doc.page_count == 2, f"page count changed: {out_doc.page_count}"
    page_texts = [p.get_text() for p in out_doc]
    out_doc.close()

    # Every paragraph translated, and each stayed on its own page
    for text in PAGE1:
        assert T + text in page_texts[0], f"page-1 paragraph missing/mistranslated: {text!r}"
    for text in PAGE2:
        assert T + text in page_texts[1], f"page-2 paragraph missing/mistranslated: {text!r}"
    assert all(T + t not in page_texts[1] for t in PAGE1), "page-1 text leaked onto page 2"

    print("PASS: 2-page multi-paragraph PDF translation works end to end")


if __name__ == "__main__":
    main()
