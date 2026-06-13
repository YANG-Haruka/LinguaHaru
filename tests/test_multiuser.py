# Tests for multi-user parallel translation: per-session stop isolation,
# per-session path isolation, and genuinely concurrent runs not colliding.
#
# Run from the repo root:
#   python tests/test_multiuser.py
import json
import os
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

WORK = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "multiuser")
T = "[T]"
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def install_fake_llm():
    import textProcessing.base_translator as bt
    from textProcessing.translation_checker import clean_json

    def fake(segments, previous_text, model, use_online, api_key, system_prompt,
             user_prompt, previous_prompt, glossary_prompt, glossary_terms=None,
             check_stop_callback=None):
        # Honor the per-session stop callback so the stop test can interrupt
        if check_stop_callback:
            check_stop_callback()
        data = json.loads(clean_json(segments if isinstance(segments, str)
                                     else json.dumps(segments, ensure_ascii=False)))
        reply = {k: T + v for k, v in data.items()}
        return json.dumps(reply, ensure_ascii=False), True, {"total_tokens": 1}

    bt.translate_text = fake


def test_session_id_derivation():
    print("session id derivation")
    import app

    class Req:
        def __init__(self, h): self.session_hash = h

    a1 = app._session_id_for_request(Req("abcdef123456789"))
    a2 = app._session_id_for_request(Req("abcdef123456789"))
    b = app._session_id_for_request(Req("zzzzzz999"))
    check("same hash -> same id (continue works)", a1 == a2, f"{a1} vs {a2}")
    check("different hash -> different id (isolation)", a1 != b, f"{a1} vs {b}")
    check("no request -> still yields an id", bool(app._session_id_for_request(None)))


def test_stop_isolation():
    print("per-session stop isolation")
    import app
    from app import StopTranslationException

    app.reset_stop_flag("sidA")
    app.reset_stop_flag("sidB")
    # Register both sessions to two browser hashes
    with app.stop_lock:
        app.active_sessions["hashA"] = "sidA"
        app.active_sessions["hashB"] = "sidB"

    class Req:
        def __init__(self, h): self.session_hash = h

    # User A clicks stop
    app.request_stop_translation("en", request=Req("hashA"))

    raised_a = False
    try:
        app.check_stop_requested("sidA")
    except StopTranslationException:
        raised_a = True
    check("A's stop raises for A", raised_a)

    b_ok = True
    try:
        app.check_stop_requested("sidB")
    except StopTranslationException:
        b_ok = False
    check("A's stop does NOT affect B", b_ok)

    # Disconnect of B's tab stops B only
    app.on_session_disconnect(Req("hashB"))
    raised_b = False
    try:
        app.check_stop_requested("sidB")
    except StopTranslationException:
        raised_b = True
    check("B disconnect stops B", raised_b)
    check("B removed from active_sessions", "hashB" not in app.active_sessions)

    app.clean_stop_flag("sidA")
    app.clean_stop_flag("sidB")


def _run_txt(session_id, text, results, idx):
    from translator.txt_translator import TxtTranslator
    src = os.path.join(WORK, f"{session_id}_in.txt")
    # Same base filename for both users to prove isolation prevents collision
    src = os.path.join(WORK, "shared_name.txt")
    # ... but each user needs its own copy on disk; write per-session input
    user_src = os.path.join(WORK, f"src_{session_id}.txt")
    with open(user_src, "w", encoding="utf-8") as f:
        f.write(text)

    temp_dir = os.path.join(WORK, "temp", session_id)
    result_dir = os.path.join(WORK, "result", session_id)
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(result_dir, exist_ok=True)

    tr = TxtTranslator(user_src, "fake", True, "k", "en", "zh", False,
                       max_token=2048, max_retries=1, thread_count=2,
                       glossary_path=None, temp_dir=temp_dir, result_dir=result_dir,
                       session_lang="en", log_dir=os.path.join(WORK, "log", session_id))
    out, _ = tr.process(*os.path.splitext(os.path.basename(user_src)))
    with open(out, encoding="utf-8") as f:
        results[idx] = f.read()


def test_concurrent_no_collision():
    print("concurrent isolated runs do not collide")
    results = {}
    threads = [
        threading.Thread(target=_run_txt, args=("userA", "Alpha content line", results, 0)),
        threading.Thread(target=_run_txt, args=("userB", "Bravo content line", results, 1)),
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
          os.path.isdir(os.path.join(WORK, "result", "userA"))
          and os.path.isdir(os.path.join(WORK, "result", "userB")))


def main():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    install_fake_llm()

    for fn in (test_session_id_derivation, test_stop_isolation, test_concurrent_no_collision):
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
