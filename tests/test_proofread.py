# Tests for the proofreading editor (Proofread tab):
#   1. A real translation run (translator classes with a faked LLM) writes
#      manifest.json + a copy of the original into temp/<doc>/.
#   2. The save function writes edited 'translated' values back into
#      dst_translated.json (and rejects row-count mismatches).
#   3. Re-export regenerates the document and the edited text appears in it.
# Covers TXT and DOCX.
#
# Run from the repo root:
#   python tests/test_proofread.py
import json
import os
import shutil
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

# Console-safe output on Windows (CJK text)
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_proofread_work")
TEMP_DIR = os.path.join(WORK_DIR, "temp")
RESULT_DIR = os.path.join(WORK_DIR, "result")
LOG_DIR = os.path.join(WORK_DIR, "log")

T = "[T]"
EDIT_MARK = "EDITED-BY-PROOFREADER"

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))
    return bool(cond)


def fake_translate_text(segments, previous_text, model, use_online, api_key,
                        system_prompt, user_prompt, previous_prompt, glossary_prompt,
                        glossary_terms=None, check_stop_callback=None):
    """Fake LLM: prefix every value with [T] and echo the same keys back."""
    from textProcessing.translation_checker import clean_json
    data = json.loads(clean_json(segments))
    out = {k: T + v for k, v in data.items()}
    return (json.dumps(out, ensure_ascii=False), True,
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


def run_fake_translation(app, translator_class, src_file, ext):
    """Run a full translator-class pipeline with the faked LLM."""
    import textProcessing.base_translator as bt
    original = bt.translate_text
    bt.translate_text = fake_translate_text
    try:
        translator = translator_class(
            src_file, "fake-model", False, "", "en", "fr", False,
            max_token=768, max_retries=2, thread_count=1, glossary_path=None,
            temp_dir=TEMP_DIR, result_dir=RESULT_DIR, session_lang="en", log_dir=LOG_DIR
        )
        out_path, missing = translator.process(os.path.splitext(src_file)[0], ext)
        return out_path, missing
    finally:
        bt.translate_text = original


def proofread_cycle(app, doc_name, edit_row_value):
    """Load table -> edit one translated cell -> save -> re-export.

    Returns (exported_path, statuses) or (None, statuses)."""
    statuses = {}

    df, load_status = app.load_proofread_table(doc_name, "en")
    statuses["load"] = load_status
    check(f"{doc_name}: table loaded with 3 columns", hasattr(df, "iloc") and df.shape[1] == 3,
          str(getattr(df, "shape", df)))

    # Mismatch guard: a table with a dropped row must be rejected
    bad_df = df.iloc[:-1].copy()
    mismatch_status = app.save_proofread_table(doc_name, bad_df, "en")
    check(f"{doc_name}: row-count mismatch is rejected",
          "mismatch" in mismatch_status.lower() or "不匹配" in mismatch_status, mismatch_status)

    # Edit the translated column (index 2) of the first row
    df.iloc[0, 2] = edit_row_value
    save_status = app.save_proofread_table(doc_name, df, "en")
    statuses["save"] = save_status
    check(f"{doc_name}: edits saved", "Saved 1" in save_status, save_status)

    # The edit must land in dst_translated.json (translated field only)
    dst_path = os.path.join(TEMP_DIR, doc_name, "dst_translated.json")
    with open(dst_path, encoding="utf-8") as f:
        data = json.load(f)
    check(f"{doc_name}: dst_translated.json updated",
          data[0]["translated"] == edit_row_value, repr(data[0]))
    check(f"{doc_name}: original field untouched",
          not data[0]["original"].startswith(T), repr(data[0]["original"]))

    file_update, export_status = app.export_proofread_doc(doc_name, "en")
    statuses["export"] = export_status
    exported = file_update.get("value") if isinstance(file_update, dict) else None
    ok = check(f"{doc_name}: re-export produced a file",
               exported and os.path.exists(exported), f"{exported} / {export_status}")
    return (exported if ok else None), statuses


def test_txt(app):
    print("TXT: fake translation -> manifest -> edit -> re-export")
    from translator.txt_translator import TxtTranslator

    src_file = os.path.join(WORK_DIR, "proof_sample_txt.txt")
    with open(src_file, "w", encoding="utf-8") as f:
        f.write("First line of the document\n"
                "Second line with more text\n"
                "Third and final line\n")

    out_path, missing = run_fake_translation(app, TxtTranslator, src_file, ".txt")
    check("txt translation output exists", os.path.exists(out_path), out_path)
    check("txt no missing segments", not missing, str(missing))

    doc_dir = os.path.join(TEMP_DIR, "proof_sample_txt")
    manifest_path = os.path.join(doc_dir, "manifest.json")
    check("manifest.json written", os.path.exists(manifest_path), doc_dir)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    check("manifest fields correct",
          manifest.get("file_extension") == ".txt" and manifest.get("src_lang") == "en"
          and manifest.get("dst_lang") == "fr" and manifest.get("model") == "fake-model"
          and manifest.get("input_file") == "proof_sample_txt.txt", str(manifest))
    original_copy = os.path.join(doc_dir, manifest.get("original_copy", ""))
    check("original file copied into temp folder",
          os.path.exists(original_copy)
          and open(original_copy, encoding="utf-8").read() == open(src_file, encoding="utf-8").read(),
          original_copy)

    docs = app.list_proofread_docs()
    check("doc listed for proofreading", "proof_sample_txt" in docs, str(docs))

    exported, _ = proofread_cycle(app, "proof_sample_txt", EDIT_MARK + " ligne une")
    if exported:
        with open(exported, encoding="utf-8") as f:
            content = f.read()
        check("edited text appears in regenerated txt", EDIT_MARK in content, content)
        check("untouched rows keep the fake translation",
              T + "Second line with more text" in content, content)


def test_docx(app):
    print("DOCX: fake translation -> manifest -> edit -> re-export")
    from docx import Document
    from translator.word_translator import WordTranslator

    src_file = os.path.join(WORK_DIR, "proof_sample_docx.docx")
    doc = Document()
    doc.add_paragraph("Opening paragraph for the proofread test")
    doc.add_paragraph("Closing paragraph with different words")
    doc.save(src_file)

    out_path, missing = run_fake_translation(app, WordTranslator, src_file, ".docx")
    check("docx translation output exists", os.path.exists(out_path), out_path)

    doc_dir = os.path.join(TEMP_DIR, "proof_sample_docx")
    check("docx manifest written", os.path.exists(os.path.join(doc_dir, "manifest.json")), doc_dir)
    check("docx listed for proofreading", "proof_sample_docx" in app.list_proofread_docs(),
          str(app.list_proofread_docs()))

    exported, _ = proofread_cycle(app, "proof_sample_docx", EDIT_MARK + " premiere phrase")
    if exported:
        with zipfile.ZipFile(exported) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        check("edited text appears in regenerated docx", EDIT_MARK in xml, xml[:400])
        check("untouched paragraph keeps the fake translation",
              T + "Closing paragraph with different words" in xml, xml[:400])


def test_pdf_excluded(app):
    print("PDF: excluded from the proofread list")
    pdf_dir = os.path.join(TEMP_DIR, "fake_pdf_doc")
    os.makedirs(pdf_dir, exist_ok=True)
    with open(os.path.join(pdf_dir, "dst_translated.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(pdf_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"file_extension": ".pdf"}, f)
    check("pdf doc not listed", "fake_pdf_doc" not in app.list_proofread_docs(),
          str(app.list_proofread_docs()))
    shutil.rmtree(pdf_dir, ignore_errors=True)


def main():
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    for d in (WORK_DIR, TEMP_DIR, RESULT_DIR, LOG_DIR):
        os.makedirs(d, exist_ok=True)

    # Point the app's custom paths at the test sandbox (restored afterwards)
    config_path = os.path.join("config", "system_config.json")
    with open(config_path, encoding="utf-8") as f:
        config_backup = f.read()
    try:
        config = json.loads(config_backup)
        config["temp_dir"] = TEMP_DIR
        config["result_dir"] = RESULT_DIR
        config["log_dir"] = LOG_DIR
        config["auto_extract_glossary"] = False
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

        import app

        # NOTE: a fresh translation clears the whole temp dir, so each format
        # must finish its full proofread cycle before the next one starts.
        for fn in (test_txt, test_docx, test_pdf_excluded):
            try:
                fn(app)
            except Exception:
                import traceback
                traceback.print_exc()
                FAILED.append(fn.__name__ + " (crashed)")
            print()
    finally:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_backup)

    print("=" * 60)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAIL: {name}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
