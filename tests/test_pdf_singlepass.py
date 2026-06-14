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

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "This is the first paragraph of the test document.", fontsize=12)
    page.insert_text((72, 140), "Machine translation quality has improved significantly.", fontsize=12)
    doc.save(pdf_path)
    doc.close()

    import core.translators.pdf_translator as pt

    calls = []

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None):
        value = segments["1"]
        calls.append(value)
        reply = json.dumps({"1": T + value}, ensure_ascii=False)
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

    assert os.path.exists(out_path), "output PDF missing"
    assert not missing, f"failed paragraphs: {missing}"
    assert calls, "translator callback was never invoked"

    out_doc = pymupdf.open(out_path)
    text = "".join(p.get_text() for p in out_doc)
    out_doc.close()
    assert T in text, f"translated marker not found in output text: {text[:300]!r}"
    print("PASS: single-pass PDF translation works end to end")


if __name__ == "__main__":
    main()
