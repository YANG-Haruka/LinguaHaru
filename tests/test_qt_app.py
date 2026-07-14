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
    import core.engine.base_translator as bt
    from core.engine.translation_checker import clean_json

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None,
                            **kwargs):
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
    # Deferred pages now build one-per-event-loop-tick (so startup doesn't freeze);
    # pump until the last one (Settings) exists instead of a single processEvents().
    import time as _time
    _deadline = _time.time() + 10
    while w.settings_page is None and _time.time() < _deadline:
        app.processEvents()
    assert w.translate_page.objectName() == "TranslatePage"
    assert w.glossary_page.objectName() == "GlossaryPage"
    assert w.settings_page.objectName() == "SettingsPage"
    assert w.history_page.objectName() == "HistoryPage"
    assert w.proofread_page.objectName() == "ProofreadPage"
    # new pages (interface mgmt, plugins, live voice) + the progress dashboard
    assert w.interface_page.objectName() == "InterfacePage"
    assert w.plugins_page.objectName() == "PluginsPage"
    assert w.live_page.objectName() == "LivePage"
    assert w.navigationInterface.widget("LivePage") is not None
    assert w.translate_page.dashboard.objectName() == "ProgressDashboard"
    # theme toggle flips and persists
    before = w._theme_dark
    w.toggle_theme()
    assert w._theme_dark != before
    w.toggle_theme()  # restore
    # global retranslate touches every page without error
    w.on_lang_changed("en")
    w.on_lang_changed("zh")
    print("  PASS: main window + all pages (incl. interface/plugins/dashboard)")
    return True


def test_new_pages_standalone():
    print("PAGES: interface + plugins + dashboard construct standalone")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from qt_app.interface_page import InterfacePage
    from qt_app.plugins_page import PluginsPage
    from qt_app.progress_dashboard import ProgressDashboard

    ip = InterfacePage(lang="zh")
    assert ip.objectName() == "InterfacePage"
    pp = PluginsPage(lang="zh")
    assert pp.objectName() == "PluginsPage"
    # optional-plugin cards: PDF, Image OCR, 漫画翻译, Video/Audio, Real-Time Voice, 翻译语音输入
    assert len(pp._opt_cards) == 6, len(pp._opt_cards)
    dash = ProgressDashboard(lang="zh")
    dash.start()
    dash.update_metrics(percent=50, total_files=4, done_files=2,
                        thread_count=4, failed=0, total_tokens=12345)

    # Real-time voice page + its pure-Python PCM converters.
    from qt_app.live_page import (
        LivePage, _decode_to_mono_float, _resample, _encode_from_mono_float)
    from PySide6.QtMultimedia import QAudioFormat
    SF = QAudioFormat.SampleFormat
    lv = LivePage(lang="zh")
    assert lv.objectName() == "LivePage"
    # Int16 encode->decode roundtrip stays within one quantization step.
    floats = [0.5, -0.5, 0.25, -0.25, 0.0, 0.999, -0.999]
    dec = _decode_to_mono_float(_encode_from_mono_float(floats, SF.Int16, 1), SF.Int16, 1)
    assert max(abs(a - b) for a, b in zip(floats, dec)) < 1e-3
    # 48k -> 16k decimates length ~3x; stereo with L=-R downmixes to silence.
    assert abs(len(_resample(list(range(4800)), 48000, 16000)) - 1600) <= 1
    stereo = [v for x in floats for v in (x, -x)]
    mono = _decode_to_mono_float(
        _encode_from_mono_float(stereo, SF.Int16, 1), SF.Int16, 2)
    assert max(abs(v) for v in mono) < 1e-3
    print("  PASS: new pages constructed + dashboard metrics + live PCM converters")
    return True


def test_backend_interface_helpers():
    print("BACKEND: interface read/write/active round-trip")
    from core import backend
    name = "(Custom) _qt_itf_test"
    saved_online = backend.get_config("default_online_model", "")
    saved_default_online = backend.get_config("default_online", False)
    try:
        backend.write_api_config(name, {
            "base_url": "https://x/v1", "model": "m", "temperature": 0.5})
        cfg = backend.read_api_config(name)
        assert cfg["base_url"] == "https://x/v1" and cfg["model"] == "m", cfg
        names = [i["name"] for i in backend.list_online_interfaces()]
        assert name in names, names
        # not an official prefix -> custom
        itf = next(i for i in backend.list_online_interfaces() if i["name"] == name)
        assert itf["official"] is False
        backend.set_active_model(name, use_online=True)
        assert backend.get_active_model(use_online=True) == name
        # install command maps to a requirements file
        cmd = backend.install_command_for("PDF")
        assert cmd and cmd[-2:] == ["-r", cmd[-1]] or "requirements/pdf.txt" in cmd[-1]
        assert backend.install_command_for("Nope") is None
        print("  PASS: interface helpers + install command")
    finally:
        backend.delete_api_config(name)
        backend.set_config("default_online_model", saved_online)
        backend.set_config("default_online", saved_default_online)
    return True


def test_backend_resolution():
    print("BACKEND: extension -> class resolution (incl. bilingual partial)")
    from core import backend

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
    from core import backend

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
    from core import backend
    online = backend.scan_online_models()
    assert isinstance(online, list) and online, "expected online configs present"
    assert all(".json" not in m for m in online)
    print(f"  PASS: {len(online)} online models discovered")
    return True


def test_worker_end_to_end():
    print("WORKER: end-to-end on a tiny .txt with fake LLM")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop, QTimer
    from core import backend
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


def _run_worker(worker, timeout_ms=60000):
    """Run a TranslationWorker to completion on a local event loop; returns
    a dict with either 'path'/'missing' or 'error'."""
    from PySide6.QtCore import QEventLoop, QTimer
    result = {}
    loop = QEventLoop()
    worker.finished.connect(
        lambda path, missing: (result.update(path=path, missing=missing), loop.quit()))
    worker.failed.connect(lambda msg: (result.update(error=msg), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(5000)
    return result


def test_proofread_roundtrip():
    print("PROOFREAD: list/load/save/re-export round-trip + page construction")
    from PySide6.QtWidgets import QApplication
    from core import backend
    from qt_app.worker import TranslationWorker
    from qt_app.proofread_page import ProofreadPage

    app = QApplication.instance() or QApplication([])
    install_fake_llm()

    # page constructs without error
    page = ProofreadPage(lang="en")
    assert page.objectName() == "ProofreadPage"

    os.makedirs(WORK_DIR, exist_ok=True)
    src_path = os.path.join(WORK_DIR, "proof_doc.txt")
    with open(src_path, "w", encoding="utf-8") as f:
        f.write("First proofread line\nSecond proofread line\nThird proofread line\n")

    config = backend.read_config()
    saved = {k: config.get(k) for k in ("temp_dir", "result_dir", "log_dir")}
    backend.set_config("temp_dir", os.path.join(WORK_DIR, "temp"))
    backend.set_config("result_dir", os.path.join(WORK_DIR, "result"))
    backend.set_config("log_dir", os.path.join(WORK_DIR, "log"))
    try:
        worker = TranslationWorker(
            file_path=src_path, model="fake", use_online=True, api_key="x",
            src_lang="English", dst_lang="Français",
            max_token=2048, max_retries=2, thread_count=1,
            glossary_name=None, bilingual_flags={},
        )
        res = _run_worker(worker)
        assert "error" not in res, f"translation failed: {res.get('error')}"

        docs = backend.list_proofread_docs()
        assert "proof_doc" in docs, f"doc not listed: {docs}"

        rows = backend.load_proofread_table("proof_doc")
        assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
        assert rows[0][1] == "First proofread line", rows[0]
        assert rows[0][2].startswith(T), rows[0]

        # row-count mismatch is refused
        try:
            backend.save_proofread_table("proof_doc", rows[:-1])
            raise AssertionError("expected row-count mismatch refusal")
        except ValueError:
            pass

        # edit one translated value and save
        edit = "EDITED-BY-PROOFREADER ligne une"
        rows[0] = (rows[0][0], rows[0][1], edit)
        changed = backend.save_proofread_table("proof_doc", rows)
        assert changed == 1, f"expected 1 changed, got {changed}"

        # edit landed in dst_translated.json (translated only)
        dst = os.path.join(WORK_DIR, "temp", "proof_doc", "dst_translated.json")
        with open(dst, encoding="utf-8") as f:
            data = json.load(f)
        assert data[0]["translated"] == edit, data[0]
        assert not data[0]["original"].startswith(T), data[0]

        out_path = backend.export_proofread_doc("proof_doc")
        assert os.path.exists(out_path), out_path
        with open(out_path, encoding="utf-8") as f:
            content = f.read()
        assert edit in content, f"edited text missing from export: {content!r}"
        assert T + "Second proofread line" in content, content
        print(f"  PASS: proofread round-trip, export {os.path.basename(out_path)}")
    finally:
        for k, v in saved.items():
            if v is not None:
                backend.set_config(k, v)
    return True


def test_multifile_concurrent():
    print("WORKER: two .txt files translated concurrently, both outputs present")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEventLoop, QTimer
    from core import backend
    from qt_app.worker import TranslationWorker

    app = QApplication.instance() or QApplication([])
    install_fake_llm()

    os.makedirs(WORK_DIR, exist_ok=True)
    paths = []
    for i in (1, 2):
        p = os.path.join(WORK_DIR, f"multi_{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"File {i} first line\nFile {i} second line\n")
        paths.append(p)

    config = backend.read_config()
    saved = {k: config.get(k) for k in ("temp_dir", "result_dir", "log_dir")}
    backend.set_config("temp_dir", os.path.join(WORK_DIR, "temp"))
    backend.set_config("result_dir", os.path.join(WORK_DIR, "result"))
    backend.set_config("log_dir", os.path.join(WORK_DIR, "log"))
    try:
        outputs = {}
        loop = QEventLoop()
        workers = []
        for p in paths:
            w = TranslationWorker(
                file_path=p, model="fake", use_online=True, api_key="x",
                src_lang="English", dst_lang="Français",
                max_token=2048, max_retries=2, thread_count=2,
                glossary_name=None, bilingual_flags={},
            )
            w._tag = os.path.basename(p)
            def done(path, missing, tag=w._tag):
                outputs[tag] = path
                if len(outputs) == len(paths):
                    loop.quit()
            w.finished.connect(done)
            w.failed.connect(lambda msg: (outputs.update(_err=msg), loop.quit()))
            workers.append(w)

        QTimer.singleShot(90000, loop.quit)
        for w in workers:  # start ALL concurrently (bounded pool >= 2)
            w.start()
        loop.exec()
        for w in workers:
            w.wait(5000)

        assert "_err" not in outputs, f"a file failed: {outputs.get('_err')}"
        assert len(outputs) == 2, f"expected 2 outputs, got {outputs}"
        for tag, path in outputs.items():
            assert path and os.path.exists(path), f"missing output for {tag}: {path}"
            with open(path, encoding="utf-8") as f:
                assert T in f.read(), f"no fake translation in {tag}"
        print(f"  PASS: both files translated -> {sorted(outputs.keys())}")
    finally:
        for k, v in saved.items():
            if v is not None:
                backend.set_config(k, v)
    return True


def test_i18n_helper():
    print("I18N: tr() returns zh for a known key and falls back for a missing one")
    from qt_app.i18n import tr, UI_LANGS, lang_display_name, lang_from_display_name
    from core.languages_config import LABEL_TRANSLATIONS

    zh_translate = LABEL_TRANSLATIONS["zh"]["Translate"]
    assert tr("Translate", "zh") == zh_translate, tr("Translate", "zh")
    assert tr("Translate", "zh") != "Translate", "zh should differ from English key"

    # missing key falls back to the key text itself, no crash
    assert tr("This Key Does Not Exist", "zh") == "This Key Does Not Exist"
    # unknown language falls back to English
    assert tr("Translate", "xx") == LABEL_TRANSLATIONS["en"]["Translate"]

    assert "en" in UI_LANGS and "zh" in UI_LANGS
    assert lang_from_display_name(lang_display_name("ja")) == "ja"
    print("  PASS: i18n helper zh + fallbacks")
    return True


def main():
    install_fake_llm()
    tests = [
        test_main_window,
        test_new_pages_standalone,
        test_backend_interface_helpers,
        test_backend_resolution,
        test_backend_glossary_roundtrip,
        test_backend_model_discovery,
        test_worker_end_to_end,
        test_proofread_roundtrip,
        test_multifile_concurrent,
        test_i18n_helper,
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
    ok = all(results.values())
    # PySide/Qt native objects can make the interpreter's shutdown return a bogus
    # nonzero code on Windows even when every test passed (sys.exit(0) is reached).
    # Hard-exit with the ACTUAL result — after flushing — so the suite's exit code
    # reflects the assertions, not Qt DLL-unload noise. A real failure still exits 1.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
