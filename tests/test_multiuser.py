# Tests for multi-user isolation in the FastAPI Web deploy (webapp.sessions):
#   - session-id validity (hex-only, so it is safe as a path component)
#   - per-session stop isolation (one user's Stop never affects another)
#   - per-session path isolation (concurrent runs with the same filename never
#     collide)
#   - proofread scoping (no cross-session IDOR, no path traversal)
#
# The base temp/result/log dirs are monkeypatched to a sandbox so the real
# directories are never touched.
#
# Run from the repo root:
#   python tests/test_multiuser.py
import json
import os
import sys
import threading

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

WORK = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "multiuser")
T = "[T]"
PASSED, FAILED = [], []

import core.backend as backend
from webapp import sessions

# Sandbox base dirs (sessions.session_paths reads backend.get_custom_paths()).
_TEMP = os.path.join(WORK, "temp")
_RESULT = os.path.join(WORK, "result")
_LOG = os.path.join(WORK, "log")


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def install_fake_llm():
    import core.engine.base_translator as bt
    from core.engine.translation_checker import clean_json

    def fake(segments, previous_text, model, use_online, api_key, system_prompt,
             user_prompt, previous_prompt, glossary_prompt, glossary_terms=None,
             check_stop_callback=None, **kwargs):
        if check_stop_callback:
            check_stop_callback()
        data = json.loads(clean_json(segments if isinstance(segments, str)
                                     else json.dumps(segments, ensure_ascii=False)))
        reply = {k: T + v for k, v in data.items()}
        return json.dumps(reply, ensure_ascii=False), True, {"total_tokens": 1}

    bt.translate_text = fake


def use_sandbox():
    for d in (_TEMP, _RESULT, _LOG):
        os.makedirs(d, exist_ok=True)
    backend.get_custom_paths = lambda: (_TEMP, _RESULT, _LOG)


def test_session_id_validity():
    print("session id validity (safe as a path component)")
    sid = sessions.new_session_id()
    check("new id is 32-char hex (128-bit, path-safe)",
          len(sid) == 32 and all(c in "0123456789abcdef" for c in sid), sid)
    check("accepts a hex token", sessions.valid_session_id("abcdef123456"))
    check("rejects empty", not sessions.valid_session_id(""))
    check("rejects path separator", not sessions.valid_session_id("a/b"))
    check("rejects traversal", not sessions.valid_session_id(".."))
    check("rejects non-hex", not sessions.valid_session_id("ZZZ"))


def test_stop_isolation():
    print("per-session stop isolation")
    A, B = "a" * 12, "b" * 12
    sessions.reset_stop_flag(A)
    sessions.reset_stop_flag(B)
    sessions.request_stop(A)  # user A clicks Stop

    raised_a = False
    try:
        sessions.check_stop_requested(A)
    except sessions.StopTranslationException:
        raised_a = True
    check("A's stop raises for A", raised_a)

    b_ok = True
    try:
        sessions.check_stop_requested(B)
    except sessions.StopTranslationException:
        b_ok = False
    check("A's stop does NOT affect B", b_ok)

    sessions.disconnect(B)  # B's tab closes
    raised_b = False
    try:
        sessions.check_stop_requested(B)
    except sessions.StopTranslationException:
        raised_b = True
    check("B disconnect stops B", raised_b)

    sessions.clear_stop_flag(A)
    sessions.clear_stop_flag(B)


def _run_txt(session_id, text, results, idx):
    from core.translators.txt_translator import TxtTranslator
    temp_dir, result_dir, log_dir = sessions.session_paths(session_id)
    # SAME base filename for both users, to prove path isolation prevents
    # collisions purely via the per-session dirs.
    upload = os.path.join(WORK, "uploads", session_id)
    os.makedirs(upload, exist_ok=True)
    user_src = os.path.join(upload, "shared.txt")
    with open(user_src, "w", encoding="utf-8") as f:
        f.write(text)

    tr = TxtTranslator(user_src, "fake", True, "k", "en", "zh", False,
                       max_token=2048, max_retries=1, thread_count=2,
                       glossary_path=None, temp_dir=temp_dir, result_dir=result_dir,
                       session_lang="en", log_dir=log_dir)
    out, _ = tr.process(*os.path.splitext(os.path.basename(user_src)))
    with open(out, encoding="utf-8") as f:
        results[idx] = f.read()


def test_concurrent_no_collision():
    print("concurrent isolated runs do not collide")
    A, B = "a" * 12, "c" * 12
    results = {}
    threads = [
        threading.Thread(target=_run_txt, args=(A, "Alpha content line", results, 0)),
        threading.Thread(target=_run_txt, args=(B, "Bravo content line", results, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("user A got A's translation", results.get(0) == T + "Alpha content line",
          repr(results.get(0)))
    check("user B got B's translation", results.get(1) == T + "Bravo content line",
          repr(results.get(1)))
    check("outputs landed in separate session dirs",
          os.path.isdir(os.path.join(_RESULT, A))
          and os.path.isdir(os.path.join(_RESULT, B)))


def test_proofread_cross_session_blocked():
    print("proofread is scoped to the caller's session (no IDOR)")
    A, B = "a" * 12, "d" * 12

    def _make_doc(session_id, doc):
        folder = os.path.join(_TEMP, session_id, doc)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "dst_translated.json"), "w", encoding="utf-8") as f:
            json.dump([{"count_src": 1, "original": "x", "translated": "y"}], f)
        with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"file_extension": ".txt"}, f)

    _make_doc(A, "docA")
    _make_doc(B, "docB")

    listed_a = sessions.list_proofread_docs(A)
    check("A sees its own doc", f"{A}/docA" in listed_a, str(listed_a))
    check("A does NOT see B's doc", f"{B}/docB" not in listed_a, str(listed_a))
    # A cannot resolve B's doc even by guessing the namespaced path
    check("A cannot resolve B's doc dir",
          sessions.proofread_doc_dir(f"{B}/docB", A) is None)
    check("B can resolve its own doc dir",
          sessions.proofread_doc_dir(f"{B}/docB", B) is not None)
    # Path traversal blocked, and bare (non-namespaced) docs are not accessible
    check("traversal still blocked",
          sessions.proofread_doc_dir("../config/system_config", A) is None)
    check("bare (non-namespaced) doc rejected",
          sessions.proofread_doc_dir("docA", A) is None)


def main():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    use_sandbox()
    install_fake_llm()

    for fn in (test_session_id_validity, test_stop_isolation,
               test_concurrent_no_collision, test_proofread_cross_session_blocked):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            FAILED.append(fn.__name__ + " (crashed)")
        print()

    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for n in FAILED:
        print(f"  FAIL: {n}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
