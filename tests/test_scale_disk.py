# Disk growth over a long session: the app stays open for days running job
# after job, so whatever a task leaves behind has to be bounded WHILE it runs,
# not at the next launch. What these pin down:
#
#   1. Working-folder retention also runs when a task ends (off the worker).
#   2. The web layout temp/<session>/<run>/<doc> is pruned per RUN — with one
#      browser there is one session folder, always "the newest", so top-level
#      pruning never removed anything.
#   3. A finished task's upload is deleted; an interrupted one keeps it (Continue
#      re-reads it); nothing outside the upload folder is ever touched.
#   4. A .docx is unpacked inside the task's working folder, not the system
#      temp dir where a failed or stopped task left it forever.
#   5. Loose transcript / checkpoint files and the per-download log folder.
#
# And what must NEVER be pruned: anything history can still continue from, and
# anything written to a moment ago.
#
# Run from the repo root:
#   python -m pytest tests/test_scale_disk.py -q
import json
import os
import sys
import threading
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import retention

SID = "0123456789abcdef0123456789abcdef"      # a web session id (32 hex)
MB = 1 << 20


def _blob(folder, age_days, size=2 * MB, name="blob"):
    """A file of `size` in `folder`, both dated `age_days` ago."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    _age(path, age_days)
    _age(folder, age_days)
    return path


def _age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


# --- 2. the web layout -------------------------------------------------------
def test_web_runs_are_pruned_inside_the_session_folder(tmp_path):
    root = str(tmp_path)
    for i in range(6):                                   # run0 oldest … run5 newest
        _blob(os.path.join(root, SID, f"run{i}", "doc"), age_days=10 - i)
        _age(os.path.join(root, SID, f"run{i}"), 10 - i)

    removed = retention.prune_temp(root, max_size_mb=7, keep_newest=3)

    assert removed == 3
    assert sorted(os.listdir(os.path.join(root, SID))) == ["run3", "run4", "run5"]


def test_desktop_and_web_runs_share_one_oldest_first_order(tmp_path):
    root = str(tmp_path)
    _blob(os.path.join(root, "2026-01-01_10-00-00", "doc"), age_days=9)   # desktop run
    _age(os.path.join(root, "2026-01-01_10-00-00"), 9)
    for i, age in enumerate((8, 2, 1)):
        _blob(os.path.join(root, SID, f"run{i}", "doc"), age_days=age)
        _age(os.path.join(root, SID, f"run{i}"), age)

    assert retention.prune_temp(root, max_size_mb=5, keep_newest=1) == 2
    assert os.listdir(root) == [SID]
    assert sorted(os.listdir(os.path.join(root, SID))) == ["run1", "run2"]


def test_a_document_named_in_hex_is_never_half_deleted(tmp_path):
    # Old flat layout: temp/<doc>. A doc called "202611121430" looks like a
    # session id; its own subfolders must not be pruned one by one.
    root = str(tmp_path)
    doc = os.path.join(root, "202611121430")
    _blob(os.path.join(doc, "parts"), age_days=9)
    _blob(doc, age_days=9, size=10, name="src.json")
    for i in range(3):
        _blob(os.path.join(root, f"new{i}"), age_days=i + 1)

    retention.prune_temp(root, max_size_mb=7, keep_newest=3)

    assert not os.path.exists(doc)                       # gone whole (it was the oldest)
    assert sorted(os.listdir(root)) == ["new0", "new1", "new2"]


# --- what is never pruned ----------------------------------------------------
def test_a_resumable_run_is_spared_however_old(tmp_path):
    root = str(tmp_path)
    for i in range(5):
        _blob(os.path.join(root, SID, f"run{i}", "doc"), age_days=10 - i)
        _age(os.path.join(root, SID, f"run{i}"), 10 - i)
    # History can still continue run0's doc (the oldest folder of all).
    protect = [retention._norm(os.path.join(root, SID, "run0", "doc"))]

    removed = retention.prune_temp(root, max_size_mb=1, keep_newest=0, protect=protect)

    assert removed == 4
    assert os.listdir(os.path.join(root, SID)) == ["run0"]
    assert os.path.exists(os.path.join(root, SID, "run0", "doc", "blob"))


def test_a_folder_written_to_recently_is_spared(tmp_path):
    root = str(tmp_path)
    for i in range(4):
        _blob(os.path.join(root, f"run{i}", "doc"), age_days=10 - i)
        _age(os.path.join(root, f"run{i}"), 10 - i)
    # run0 is the oldest FOLDER, but a proofread save just rewrote a file in it.
    with open(os.path.join(root, "run0", "doc", "dst_translated.json"), "w") as f:
        f.write("[]")
    _age(os.path.join(root, "run0", "doc"), 10)
    _age(os.path.join(root, "run0"), 10)

    removed = retention.prune_temp(root, max_size_mb=1, keep_newest=0, min_age_s=3600)

    assert removed == 3
    assert os.listdir(root) == ["run0"]


def test_history_names_what_can_still_be_continued(tmp_path, monkeypatch):
    from core import backend
    from core.translation_history import TranslationHistoryManager, create_translation_record

    hist = str(tmp_path / "history")
    monkeypatch.setattr(backend, "history_dir", lambda session_id=None: hist)

    def _add(log_dir, rid, status, resume_info):
        now = datetime.now()
        TranslationHistoryManager(log_dir=log_dir).add_record(create_translation_record(
            translation_id=rid, start_time=now, end_time=now, total_tokens=0,
            src_lang="en", src_lang_display="English", dst_lang="zh",
            dst_lang_display="Chinese", model="m", use_online=False,
            input_file="a.docx", output_file_path="", log_file_path="",
            status=status, resume_info=resume_info))

    work = str(tmp_path / "temp" / "run1")
    upload = str(tmp_path / "uploads" / SID / "t1" / "a.docx")
    _add(hist, "stopped1", "stopped", {"temp_dir": work, "input_file_path": upload})
    _add(hist, "done1", "success", None)
    # LAN mode keeps one database per browser session, one folder down.
    lan_work = str(tmp_path / "temp" / SID / "run2")
    _add(os.path.join(hist, SID), "paused1", "paused",
         {"temp_dir": lan_work, "input_file_path": "/elsewhere/b.mp4"})

    got = retention._resumable()

    for needed in (work, os.path.join(work, "a"), upload, lan_work,
                   os.path.join(lan_work, "b")):
        assert retention._norm(needed) in got, needed

    # An unreadable history must stop the sweep, not let it prune unchecked.
    os.makedirs(os.path.join(hist, "broken"))
    with open(os.path.join(hist, "broken", "translation_history.db"), "wb") as f:
        f.write(b"this is not a database" * 100)
    with pytest.raises(Exception):
        retention._resumable()


# --- 5. loose files ----------------------------------------------------------
def test_loose_transcripts_and_checkpoints_go_but_other_files_stay(tmp_path):
    root = str(tmp_path)
    _blob(root, age_days=9, name="Old Movie.srt")
    _blob(root, age_days=9, name="Old Movie.1a2b3c4d5e.asr_ckpt.jsonl")
    _blob(root, age_days=9, name="notes.txt")             # not written by the app
    _blob(root, age_days=9, name="Kept.srt")              # beside a resumable doc
    protect = [retention._norm(os.path.join(root, "Kept"))]

    removed = retention.prune_temp(root, max_size_mb=1, keep_newest=3, protect=protect)

    assert removed == 2
    assert sorted(os.listdir(root)) == ["Kept.srt", "notes.txt"]


# --- 1. the sweep at task end ------------------------------------------------
def test_sweep_after_task_runs_off_the_calling_thread(monkeypatch):
    seen = {}
    done = threading.Event()

    def fake(working_only=False):
        seen["working_only"] = working_only
        seen["thread"] = threading.current_thread()
        done.set()

    monkeypatch.setattr(retention, "run_retention", fake)
    retention.sweep_after_task()

    assert done.wait(5)
    assert seen["working_only"] is True                  # never logs / results
    assert seen["thread"] is not threading.current_thread()


@pytest.mark.parametrize("fails", [False, True])
def test_every_task_end_asks_for_a_sweep(tmp_path, monkeypatch, fails):
    from core.engine.base_translator import DocumentTranslator

    calls = []
    monkeypatch.setattr(retention, "sweep_after_task", lambda: calls.append(1),
                        raising=False)

    class _Doc(DocumentTranslator):
        def _process_impl(self, file_name, file_extension, progress_callback=None):
            if fails:
                raise RuntimeError("boom")
            return "out.txt", {}

    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    doc = _Doc(str(src), "m", False, "", "en", "zh", False, 768, 1, 1, None,
               str(tmp_path / "temp"), str(tmp_path / "result"),
               history_dir=str(tmp_path / "history"))
    if fails:
        with pytest.raises(RuntimeError):
            doc.process(str(tmp_path / "a"), ".txt")
    else:
        doc.process(str(tmp_path / "a"), ".txt")

    assert calls == [1]


def test_the_task_end_sweep_bounds_temp_and_uploads_only(tmp_path, monkeypatch):
    import core.paths
    from core import backend

    temp, data = str(tmp_path / "temp"), str(tmp_path / "data")
    uploads = os.path.join(data, "uploads")
    monkeypatch.setattr(backend, "get_custom_paths",
                        lambda: (temp, str(tmp_path / "result"), str(tmp_path / "log")))
    monkeypatch.setattr(backend, "read_config",
                        lambda: {"temp_max_size_mb": 1, "temp_keep_newest": 0})
    monkeypatch.setattr(core.paths, "DATA_DIR", data)
    monkeypatch.setattr(retention, "_resumable", lambda: [
        retention._norm(os.path.join(uploads, SID, "kept", "interrupted.mp4"))])

    def _never(*a, **k):
        raise AssertionError("a task-end sweep must not prune logs or results")
    monkeypatch.setattr(retention, "prune_logs", _never)
    monkeypatch.setattr(retention, "prune_results", _never)

    for name in ("old1", "old2"):
        _blob(os.path.join(temp, SID, name, "doc"), age_days=3)
        _age(os.path.join(temp, SID, name), 3)
        _blob(os.path.join(uploads, SID, name), age_days=3, name="video.mp4")
    _blob(os.path.join(uploads, SID, "kept"), age_days=3, name="interrupted.mp4")
    _blob(os.path.join(temp, SID, "just_ended", "doc"), age_days=0)   # written now

    retention.run_retention(working_only=True)

    assert os.listdir(os.path.join(temp, SID)) == ["just_ended"]
    assert os.listdir(os.path.join(uploads, SID)) == ["kept"]

    # History unreadable -> nothing at all is pruned.
    _blob(os.path.join(temp, SID, "old3", "doc"), age_days=3)
    _age(os.path.join(temp, SID, "old3"), 3)

    def _unreadable():
        raise OSError("history locked")
    monkeypatch.setattr(retention, "_resumable", _unreadable)
    retention.run_retention(working_only=True)
    assert sorted(os.listdir(os.path.join(temp, SID))) == ["just_ended", "old3"]


# --- 3. uploads --------------------------------------------------------------
def test_remove_upload_only_deletes_an_unreferenced_upload(tmp_path, monkeypatch):
    root = str(tmp_path / "uploads")
    done = _blob(os.path.join(root, SID, "t1"), 0, size=10, name="done.docx")
    again = _blob(os.path.join(root, SID, "t2"), 0, size=10, name="again.docx")
    own = _blob(str(tmp_path / "Documents"), 0, size=10, name="thesis.docx")
    monkeypatch.setattr(retention, "_resumable", lambda: [retention._norm(again)])

    assert retention.remove_upload(done, root) is True
    assert not os.path.exists(os.path.dirname(done))     # its empty task folder too
    assert retention.remove_upload(again, root) is False  # a Continue still needs it
    assert os.path.exists(again)
    # A desktop record's input is the user's own file: never ours to delete.
    assert retention.remove_upload(own, root) is False
    assert retention.remove_upload(os.path.join(root, "..", "Documents", "thesis.docx"),
                                   root) is False
    assert os.path.exists(own)


def test_a_finished_upload_is_dropped_unless_proofread_still_reads_it(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from webapp import server

    root = str(tmp_path / "uploads")
    monkeypatch.setattr(server, "UPLOAD_DIR", root)
    monkeypatch.setattr(retention, "_resumable", lambda: [])
    task = os.path.join(root, SID, "t1")
    doc_dir = str(tmp_path / "temp" / "run" / "doc")

    # A document: Proofread has its own copy in the doc folder.
    small = _blob(task, 0, size=10, name="small.DOCX")
    _blob(os.path.join(doc_dir, "small"), 0, size=10, name="small.docx")
    _blob(os.path.join(doc_dir, "small"), 0, size=2, name="manifest.json")
    server._drop_upload(small, os.path.join(doc_dir, "small"))
    assert not os.path.exists(small)

    # A long video: never copied (too big) — Proofread's export still wants the
    # original to exist, so it stays like any other file over the copy cap.
    video = _blob(task, 0, size=10, name="movie.mp4")
    _blob(os.path.join(doc_dir, "movie"), 0, size=2, name="manifest.json")
    server._drop_upload(video, os.path.join(doc_dir, "movie"))
    assert os.path.exists(video)
    # ... a short one has its copy in the doc folder and goes.
    clip = _blob(task, 0, size=10, name="clip.MP4")
    _blob(os.path.join(doc_dir, "clip"), 0, size=10, name="clip.mp4")
    _blob(os.path.join(doc_dir, "clip"), 0, size=2, name="manifest.json")
    server._drop_upload(clip, os.path.join(doc_dir, "clip"))
    assert not os.path.exists(clip)

    # A document too big to copy: re-export reads it in place, so it stays.
    big = _blob(task, 0, size=10, name="big.pptx")
    _blob(os.path.join(doc_dir, "big"), 0, size=2, name="manifest.json")
    server._drop_upload(big, os.path.join(doc_dir, "big"))
    assert os.path.exists(big)

    # A continued desktop task: the input is the user's own file.
    own = _blob(str(tmp_path / "Videos"), 0, size=10, name="holiday.mp4")
    server._drop_upload(own, os.path.join(doc_dir, "holiday"))
    assert os.path.exists(own)


def test_a_web_upload_lives_exactly_as_long_as_its_task_can_use_it(tmp_path, monkeypatch):
    """Upload -> Stop -> Continue -> done, through the real web workers."""
    pytest.importorskip("fastapi")
    import core.engine.base_translator as bt
    from core import backend
    from core.engine.translation_checker import clean_json
    from core.translation_history import TranslationHistoryManager
    from webapp import server

    def fake_llm(segments, *args, **kwargs):
        data = json.loads(clean_json(segments if isinstance(segments, str)
                                     else json.dumps(segments, ensure_ascii=False)))
        return json.dumps({k: "T:" + v for k, v in data.items()}), True, {"total_tokens": 1}

    hist, uploads = str(tmp_path / "history"), str(tmp_path / "uploads")
    monkeypatch.setattr(bt, "translate_text", fake_llm)
    monkeypatch.setattr(backend, "get_custom_paths", lambda: (
        str(tmp_path / "temp"), str(tmp_path / "result"), str(tmp_path / "log")))
    monkeypatch.setattr(backend, "history_dir", lambda session_id=None: hist)
    monkeypatch.setattr(server, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(retention, "sweep_after_task", lambda: None,   # tested above
                        raising=False)
    langs = backend.available_languages()
    src_lang, dst_lang = langs[0], langs[1]

    def _task(task_id, **state):
        server.TASKS[task_id] = {"progress": 0.0, "desc": "", "status": "running",
                                 "output": None, "error": None, "stop": False,
                                 "paused": False, "session_id": SID, **state}

    upload = os.path.join(uploads, SID, "task1", "notes.txt")
    os.makedirs(os.path.dirname(upload))
    with open(upload, "w", encoding="utf-8") as f:
        f.write("First line to translate.\n\nSecond line to translate.\n")
    try:
        # Stopped before it finished: Continue will re-read the upload.
        _task("task1", stop=True)
        server._run_translation("task1", SID, [upload], "fake", False,
                                src_lang, dst_lang, "", {}, "en")
        assert server.TASKS["task1"]["status"] == "stopped"
        assert os.path.exists(upload)
        rec = [r for r in TranslationHistoryManager(log_dir=hist).get_all_records()
               if r["status"] == "stopped"][0]
        assert retention.remove_upload(upload, uploads) is False

        # Continued to the end: nothing reads the upload again.
        _task("task2")
        server._run_resume("task2", SID, rec, "en")
        state = server.TASKS["task2"]
        assert state["status"] == "done", state
        assert os.path.exists(state["output"])
        assert not os.path.exists(upload)
        assert not os.path.exists(os.path.dirname(upload))
    finally:
        server.TASKS.pop("task1", None)
        server.TASKS.pop("task2", None)


def _history_client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from webapp import server

    hist = str(tmp_path / "history")
    monkeypatch.setattr(server, "history_log_dir", lambda session_id: hist)
    monkeypatch.setattr(server, "UPLOAD_DIR", str(tmp_path / "uploads"))
    from core import backend
    monkeypatch.setattr(backend, "history_dir", lambda session_id=None: hist)
    monkeypatch.setattr(backend, "get_custom_paths", lambda: (
        str(tmp_path / "temp"), str(tmp_path / "result"), str(tmp_path / "log")))
    return TestClient(server.app), server, hist


def _record(hist, rid, status, input_path, log_path=""):
    from core.translation_history import TranslationHistoryManager, create_translation_record
    now = datetime.now()
    TranslationHistoryManager(log_dir=hist).add_record(create_translation_record(
        translation_id=rid, start_time=now, end_time=now, total_tokens=0,
        src_lang="en", src_lang_display="English", dst_lang="zh",
        dst_lang_display="Chinese", model="m", use_online=False,
        input_file=os.path.basename(input_path), output_file_path="",
        log_file_path=log_path, status=status,
        resume_info={"input_file_path": input_path}))


def test_deleting_a_history_record_deletes_its_upload_only(tmp_path, monkeypatch):
    client, server, hist = _history_client(tmp_path, monkeypatch)
    upload = _blob(os.path.join(server.UPLOAD_DIR, SID, "t1"), 0, size=10, name="a.docx")
    own = _blob(str(tmp_path / "Documents"), 0, size=10, name="thesis.docx")
    _record(hist, "web1", "stopped", upload)
    _record(hist, "desk1", "stopped", own)

    assert client.post("/api/history/delete", json={"id": "web1"}).json() == {"ok": True}
    assert client.post("/api/history/delete", json={"id": "desk1"}).json() == {"ok": True}

    assert not os.path.exists(upload)
    assert os.path.exists(own)


def test_clearing_history_deletes_the_uploads_it_kept(tmp_path, monkeypatch):
    client, server, hist = _history_client(tmp_path, monkeypatch)
    upload = _blob(os.path.join(server.UPLOAD_DIR, SID, "t1"), 0, size=10, name="a.docx")
    own = _blob(str(tmp_path / "Documents"), 0, size=10, name="thesis.docx")
    _record(hist, "web1", "failed", upload)
    _record(hist, "desk1", "failed", own)

    assert client.post("/api/history/clear", json={}).json()["ok"] is True

    assert not os.path.exists(upload)
    assert os.path.exists(own)


def test_a_log_download_leaves_no_folder_behind(tmp_path, monkeypatch):
    import tempfile
    client, _server, hist = _history_client(tmp_path, monkeypatch)
    log = tmp_path / "task.log"
    log.write_text("a line\n", encoding="utf-8")
    _record(hist, "r1", "success", "a.docx", log_path=str(log))
    _record(hist, "r2", "success", "b.docx")             # has no log -> 404
    sysdir = tmp_path / "systemp"
    sysdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(sysdir))

    ok = client.get("/api/history/log", params={"id": "r1"})
    client.get("/api/history/log", params={"id": "r2"})

    assert ok.status_code == 200 and ok.content[:2] == b"PK"
    assert os.listdir(sysdir) == []


# --- 4. Word -----------------------------------------------------------------
def test_docx_is_unpacked_inside_the_working_folder(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    import tempfile
    from core.pipelines.word_translation_pipeline import (
        extract_word_content_to_json, write_translated_content_to_word)

    sysdir = tmp_path / "systemp"
    sysdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(sysdir))
    src = str(tmp_path / "report.docx")
    d = docx.Document()
    d.add_paragraph("Hello world, this is a sentence to translate.")
    d.save(src)
    work = str(tmp_path / "temp" / "run1")

    src_json = extract_word_content_to_json(src, work)

    # A task that stops here (failure, Stop) leaves the unpacked copy where
    # retention removes it with the run — not in the system temp dir.
    assert os.listdir(sysdir) == []
    with open(os.path.join(work, "report", "temp_dir_path.txt"), encoding="utf-8") as f:
        unpacked = f.read().strip()
    assert os.path.dirname(unpacked) == work
    assert os.path.exists(os.path.join(unpacked, "word", "document.xml"))

    # Write-back (also what a Continue runs) still finds it, and removes it.
    with open(src_json, encoding="utf-8") as f:
        items = json.load(f)
    assert items
    dst_json = os.path.join(work, "report", "dst_translated.json")
    with open(dst_json, "w", encoding="utf-8") as f:
        json.dump([dict(it, translated="译文") for it in items], f, ensure_ascii=False)
    out = write_translated_content_to_word(src, src_json, dst_json, work,
                                           str(tmp_path / "result"),
                                           src_lang="en", dst_lang="zh")
    assert os.path.exists(out)
    assert not os.path.exists(unpacked)
    assert "译文" in "\n".join(p.text for p in docx.Document(out).paragraphs)

    # Re-export with no unpacked copy left: unpacks again, still not in %TEMP%.
    write_translated_content_to_word(src, src_json, dst_json, work,
                                     str(tmp_path / "result"),
                                     src_lang="en", dst_lang="zh")
    assert os.listdir(sysdir) == []
    assert sorted(os.listdir(work)) == ["report"]


def test_a_running_tasks_upload_outlives_its_history_record(tmp_path, monkeypatch):
    """Deleting (or clearing) history took the uploads of tasks still running or
    queued along: the files a batch had not reached yet were gone."""
    client, server, hist = _history_client(tmp_path, monkeypatch)
    ups = {s: _blob(os.path.join(server.UPLOAD_DIR, SID, s), 0, size=10, name="a.docx")
           for s in ("running", "queued", "stopped")}
    for s, p in ups.items():
        _record(hist, s, s, p)
    assert client.post("/api/history/delete", json={"id": "running"}).json() == {"ok": True}
    assert os.path.exists(ups["running"])
    assert client.post("/api/history/clear", json={}).json()["ok"] is True
    assert os.path.exists(ups["queued"]) and not os.path.exists(ups["stopped"])


def test_a_task_that_is_running_keeps_its_folder(tmp_path, monkeypatch):
    """A PDF (or a transcribe-only video) writes no history row with its folder
    while it runs and nothing into the folder for hours: a sweep started by
    another task's end could take it."""
    import core.paths
    from core import backend
    temp = str(tmp_path / "temp")
    monkeypatch.setattr(backend, "get_custom_paths", lambda: (temp, str(tmp_path / "result"), str(tmp_path / "log")))
    monkeypatch.setattr(backend, "read_config", lambda: {"temp_max_size_mb": 1, "temp_keep_newest": 0})
    monkeypatch.setattr(core.paths, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(retention, "_resumable", lambda: [])
    for name in ("quiet_pdf", "old1", "old2"):
        _blob(os.path.join(temp, name, "doc"), age_days=3)
        _age(os.path.join(temp, name), 3)
    retention.hold(os.path.join(temp, "quiet_pdf"), None)
    try:
        retention.run_retention(working_only=True)
        assert os.listdir(temp) == ["quiet_pdf"]
    finally:
        retention.release(os.path.join(temp, "quiet_pdf"), None)
    assert retention._active == {}
    retention.run_retention(working_only=True)               # ended: like any other old run
    assert os.listdir(temp) == []
