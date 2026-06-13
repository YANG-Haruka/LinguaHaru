"""Headless tests for the Qt desktop app (qt_app + app_qt.py).

Run from the repo root with the offscreen platform:
    QT_QPA_PLATFORM=offscreen python tests/test_qt_app.py

Covers:
 1. constructing the main window + all four pages without error;
 2. backend.py: extension->class resolution (incl. a bilingual partial),
    glossary load/save round-trip, model-list discovery;
 3. the worker end-to-end on a tiny generated .txt with the LLM call
    monkeypatched, asserting the finished signal carries a real output path
    whose content shows the fake translation.
"""

import json
import os
import sys
from functools import partial

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "qt")
T = "[T]"


def install_fake_llm():
    """Replace the LLM call inside the base translator pipeline (see
    tests/test_optional_modules.py)."""
    import textProcessing.base_translator as bt
    from textProcessing.translation_checker import clean_json

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None):
        data = json.loads(clean_json(segments if isinstance(segments, str)
                                     else json.dumps(segments, ensure_ascii=False)))
        reply = {k: T + v for k, v in data.items()}
        return json.dumps(reply, ensure_ascii=False), True, {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    bt.translate_text = fake_translate_text


def test_main_window():
    print("WINDOW: construct main window + four pages")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from qt_app.main_window import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()
    assert w.translate_page.objectName() == "TranslatePage"
    assert w.glossary_page.objectName() == "GlossaryPage"
    assert w.settings_page.objectName() == "SettingsPage"
    assert w.history_page.objectName() == "HistoryPage"
    # theme toggle flips and persists
    before = w._theme_dark
    w.toggle_theme()
    assert w._theme_dark != before
    w.toggle_theme()  # restore
    print("  PASS: main window + pages constructed")
    return True


def test_backend_resolution():
    print("BACKEND: extension -> class resolution (incl. bilingual partial)")
    from qt_app import backend

    docx = backend.get_translator_class(".docx", word_bilingual_mode=True)
    assert isinstance(docx, partial), "docx should be a partial with bilingual_mode"
    assert docx.keywords.get("bilingual_mode") is True

    xlsx = backend.get_translator_class(".xlsx", excel_bilingual_mode=True)
    assert isinstance(xlsx, partial)
    assert xlsx.keywords.get("use_xlwings") is True
    assert xlsx.keywords.get("bilingual_mode") is True

    csv_cls = backend.get_translator_class(".csv")
    assert csv_cls is not None and not isinstance(csv_cls, partial)

    assert backend.get_translator_class(".nope") is None

    keys = backend.bilingual_keys_for_files(["a.docx", "b.srt", "c.vtt"])
    assert keys == ["word_bilingual_mode", "subtitle_bilingual_mode"], keys
    print("  PASS: resolution + bilingual keys correct")
    return True


def test_backend_glossary_roundtrip():
    print("BACKEND: glossary load/save round-trip")
    from qt_app import backend

    os.makedirs(backend.GLOSSARY_DIR, exist_ok=True)
    name = "_qt_test_glossary"
    path = os.path.join(backend.GLOSSARY_DIR, f"{name}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("source,target\nhello,bonjour\n")
    try:
        header, rows = backend.load_glossary(name)
        assert header == ["source", "target"], header
        assert rows == [["hello", "bonjour"]], rows

        rows.append(["world", "monde"])
        count = backend.save_glossary(name, header, rows)
        assert count == 2, count

        header2, rows2 = backend.load_glossary(name)
        assert rows2 == [["hello", "bonjour"], ["world", "monde"]], rows2

        # empty-over-nonempty guard
        try:
            backend.save_glossary(name, header, [])
            raise AssertionError("expected refusal saving empty over non-empty")
        except ValueError:
            pass
        print("  PASS: glossary round-trip + empty guard")
    finally:
        os.remove(path)
    return True


def test_backend_model_discovery():
    print("BACKEND: model-list discovery")
    from qt_app import backend
    online = backend.scan_online_models()
    assert isinstance(online, list) and online, "expected online configs present"
    assert all(".json" not in m for m in online)
    print(f"  PASS: {len(online)} online models discovered")
    return True


def test_worker_end_to_end():
    print("WORKER: end-to-end on a tiny .txt with fake LLM")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop, QTimer
    from qt_app import backend
    from qt_app.worker import TranslationWorker

    app = QApplication.instance() or QApplication([])
    install_fake_llm()

    os.makedirs(WORK_DIR, exist_ok=True)
    src_path = os.path.join(WORK_DIR, "hello.txt")
    with open(src_path, "w", encoding="utf-8") as f:
        f.write("Hello world.\nThis is a test.\n")

    # point temp/result/log at the work dir
    config = backend.read_config()
    saved = {k: config.get(k) for k in ("temp_dir", "result_dir", "log_dir")}
    backend.set_config("temp_dir", os.path.join(WORK_DIR, "temp"))
    backend.set_config("result_dir", os.path.join(WORK_DIR, "result"))
    backend.set_config("log_dir", os.path.join(WORK_DIR, "log"))

    result = {}
    worker = TranslationWorker(
        file_path=src_path, model="fake", use_online=True, api_key="x",
        src_lang="English", dst_lang="Français",
        max_token=2048, max_retries=2, thread_count=2,
        glossary_name=None, bilingual_flags={},
    )

    loop = QEventLoop()
    worker.finished.connect(lambda path, missing: (result.update(path=path, missing=missing), loop.quit()))
    worker.failed.connect(lambda msg: (result.update(error=msg), loop.quit()))
    QTimer.singleShot(60000, loop.quit)  # safety timeout
    try:
        worker.start()
        loop.exec()
        worker.wait(5000)

        assert "error" not in result, f"worker failed: {result.get('error')}"
        out_path = result.get("path")
        assert out_path and os.path.exists(out_path), f"no output file: {out_path}"
        with open(out_path, encoding="utf-8") as f:
            content = f.read()
        assert T in content, f"fake translation marker missing: {content!r}"
        print(f"  PASS: output {os.path.basename(out_path)} contains fake translation")
    finally:
        for k, v in saved.items():
            if v is not None:
                backend.set_config(k, v)
    return True


def main():
    install_fake_llm()
    tests = [
        test_main_window,
        test_backend_resolution,
        test_backend_glossary_roundtrip,
        test_backend_model_discovery,
        test_worker_end_to_end,
    ]
    results = {}
    for fn in tests:
        try:
            results[fn.__name__] = fn()
        except Exception:
            import traceback
            traceback.print_exc()
            results[fn.__name__] = False
        print()
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
