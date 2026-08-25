# Deleting a history record must delete its proofread data too.
#
# The two live in separate stores — records in SQLite, proofread folders in the
# temp tree (dst_translated.json + manifest.json, listed by walking that tree) —
# so deleting a record used to leave its proofread entry visible forever.
# manifest.json carries the record's translation_id, which links them exactly.
#
# Run from the repo root:
#   python tests/test_history_proofread_cleanup.py
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import check, run

PREFIX = "__qa_hp_"


def _make_proofread_folder(temp_dir, translation_id, name):
    """A folder shaped like a real finished translation's proofread data."""
    folder = os.path.join(temp_dir, PREFIX + name, "doc_" + name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"translation_id": translation_id, "input_file": name + ".docx"}, f)
    with open(os.path.join(folder, "dst_translated.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    return folder


def _cleanup(temp_dir):
    for entry in os.listdir(temp_dir) if os.path.isdir(temp_dir) else []:
        if entry.startswith(PREFIX):
            shutil.rmtree(os.path.join(temp_dir, entry), ignore_errors=True)


def _record(tid):
    from core.translation_history import create_translation_record
    now = datetime.now()
    return create_translation_record(
        translation_id=tid, start_time=now, end_time=now, total_tokens=1,
        src_lang="en", src_lang_display="English", dst_lang="zh", dst_lang_display="中文",
        model="test", use_online=True, input_file=f"{tid}.docx",
        output_file_path="", log_file_path="", status="completed")


def test_delete_record_removes_proofread():
    print("History: deleting a record deletes its proofread folder, and only its own")
    from core import backend
    from core.translation_history import TranslationHistoryManager

    temp_dir = backend.get_custom_paths()[0]
    os.makedirs(temp_dir, exist_ok=True)
    _cleanup(temp_dir)

    mine, other = "qa-hp-mine", "qa-hp-other"
    h = TranslationHistoryManager(log_dir=backend.history_dir())
    h.add_record(_record(mine))
    h.add_record(_record(other))
    folder_mine = _make_proofread_folder(temp_dir, mine, "mine")
    folder_other = _make_proofread_folder(temp_dir, other, "other")
    # A folder written before manifests carried an id: matching it by filename
    # could hit a DIFFERENT run of the same document, so it must be left alone.
    folder_legacy = _make_proofread_folder(temp_dir, "", "legacy")

    check("proofread folders exist", all(map(os.path.isdir,
          (folder_mine, folder_other, folder_legacy))))

    ok = h.delete_record(mine)
    check("delete_record succeeds", ok)
    check("its record is gone", not any(r.get("id") == mine for r in h.get_all_records()))
    check("its proofread folder is gone", not os.path.exists(folder_mine))
    check("another record's proofread folder survives", os.path.isdir(folder_other),
          folder_other)
    check("an id-less legacy folder is left alone", os.path.isdir(folder_legacy),
          folder_legacy)
    check("the emptied task folder is pruned",
          not os.path.exists(os.path.dirname(folder_mine)))

    # Clearing everything must take every proofread folder with a record with it.
    h.delete_record(other)
    check("clearing the other record removes its folder too", not os.path.exists(folder_other))

    _cleanup(temp_dir)


def test_manifest_carries_the_id():
    print("History: the proofread manifest records translation_id (the link)")
    import inspect
    from core.engine.base_translator import DocumentTranslator

    src = inspect.getsource(DocumentTranslator._write_manifest)
    check("_write_manifest writes translation_id", '"translation_id"' in src, src[:200])

    # The cleanup must sit in the shared manager, not in each delete handler:
    # both frontends delete history from six different places.
    from core.translation_history import TranslationHistoryManager as M
    check("delete_record cleans proofread data",
          "delete_proofread_data" in inspect.getsource(M.delete_record))
    check("clear_all_records cleans proofread data",
          "delete_proofread_data" in inspect.getsource(M.clear_all_records))
    check("clear_all_records_and_files routes through clear_all_records",
          "self.clear_all_records()" in inspect.getsource(M.clear_all_records_and_files))


if __name__ == "__main__":
    run([test_delete_record_removes_proofread, test_manifest_carries_the_id])
