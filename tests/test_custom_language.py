# Custom target languages: adding one must make it selectable in BOTH frontends
# and usable all the way down to prompt loading and the glossary.
#
# This feature had a UI in the retired Gradio app.py and was NOT ported in the
# 2026-06 rewrite (the backend function survived with zero callers). These tests
# pin the whole chain so a future refactor can't silently drop it again.
#
# Run from the repo root:
#   python tests/test_custom_language.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import check, run

CODE = "zzqa"          # not a real language: safe to create and delete


def _prompt_file(code):
    from core.paths import PROMPTS_DIR
    return os.path.join(PROMPTS_DIR, f"{code}.json")


def _cleanup():
    for code in (CODE, "zz-qa_1"):
        p = _prompt_file(code)
        if os.path.exists(p):
            os.remove(p)


def test_custom_language():
    print("Custom language: add -> listed -> code matches file -> prompt loads")
    from core import backend
    from core.languages_config import (
        add_custom_language, get_available_languages, get_language_code,
        normalize_language_code)
    from core.load_prompt import load_prompt

    _cleanup()
    before = get_available_languages()

    ok, msg = backend.add_language(CODE)     # the wrapper both frontends call
    check("backend.add_language succeeds", ok, msg)

    after = get_available_languages()
    check("language appears in the list", CODE in after, str(sorted(set(after) - set(before))))
    check("no other language disturbed", len(after) == len(before) + 1,
          f"{len(before)} -> {len(after)}")

    # The code IS the filename. load_prompt() opens "<code>.json" with no
    # fallback, so a mismatch is a FileNotFoundError at translation time on any
    # case-sensitive filesystem (Linux/Docker) — Windows hides it.
    code = get_language_code(CODE)
    check("code matches the created filename exactly",
          os.path.basename(_prompt_file(code)) in os.listdir(os.path.dirname(_prompt_file(code))),
          f"code={code!r}")

    sys_p, user_p = load_prompt("en", code)[:2]
    check("prompt loads for the custom language", bool(sys_p and user_p))
    check("target language substituted into the prompt",
          "{Text_Target_Language}" not in sys_p, sys_p[:120])

    # Mixed case / stray characters must normalize to the same safe code, so the
    # dropdown entry and the prompt file can never diverge.
    check("normalize lowercases", normalize_language_code("YUE") == "yue")
    check("normalize keeps code punctuation", normalize_language_code("zz-QA_1") == "zz-qa_1")
    check("path traversal stripped", normalize_language_code("../../etc/passwd") == "etcpasswd",
          normalize_language_code("../../etc/passwd"))
    check("empty after cleaning is rejected", add_custom_language("///")[0] is False)

    check("adding the same language twice is refused", backend.add_language(CODE)[0] is False)

    # Glossary columns are matched by code OR name, so a custom language works.
    from core.engine.text_separator import _lang_tokens
    check("glossary can match the custom code", code in _lang_tokens(code), str(_lang_tokens(code)))

    _cleanup()
    check("cleanup removed the language", CODE not in get_available_languages())


def test_glossary_gets_a_column():
    print("Custom language: gets a glossary column, in NEW and EXISTING glossaries")
    from core import backend
    from core.engine.text_separator import load_glossary as lookup

    _cleanup()
    existing, fresh = "__qa_existing", "__qa_new"
    for n in (existing, fresh):
        try:
            backend.delete_glossary(n)
        except Exception:  # noqa: BLE001
            pass

    # A glossary that already exists, with a term in it, is the case that matters:
    # fixing only new glossaries would leave the user's Default unable to hold
    # terms for the language they just added.
    backend.create_glossary(existing)
    header0, _ = backend.load_glossary(existing)
    backend.save_glossary(existing, header0,
                          [["Server", "服务器"] + [""] * (len(header0) - 2)])
    check("built-in glossary has no custom column yet", CODE not in header0)

    ok, _msg = backend.add_language(CODE)
    check("add_language succeeds", ok)

    header1, rows1 = backend.load_glossary(existing)
    check("existing glossary gained the column", CODE in header1, str(header1))
    check("its rows were padded to the new width",
          all(len(r) == len(header1) for r in rows1), str(rows1))
    check("its existing terms survived", rows1 and rows1[0][:2] == ["Server", "服务器"],
          str(rows1[:1]))

    backend.create_glossary(fresh)
    check("a new glossary is created with the column",
          CODE in backend.load_glossary(fresh)[0], str(backend.load_glossary(fresh)[0]))

    # The point of the column: a term in it must actually be found at translation
    # time. Before this, load_glossary returned [] silently for a custom language.
    idx = header1.index(CODE)
    row = list(rows1[0])
    row[idx] = "伺服機"
    backend.save_glossary(existing, header1, [row])
    path = backend.glossary_path(existing)
    check("a custom-language term is found at translation time",
          lookup(path, "en", CODE) == [("Server", "伺服機")], str(lookup(path, "en", CODE)))
    check("built-in languages still resolve",
          lookup(path, "en", "zh") == [("Server", "服务器")], str(lookup(path, "en", "zh")))

    for n in (existing, fresh):
        backend.delete_glossary(n)
    _cleanup()


def test_frontends_expose_it():
    print("Custom language: both frontends read the live list, not a hardcoded map")
    import inspect
    from webapp import server
    from qt_app import settings_page, translate_page, quick_page, live_page

    check("web exposes POST /api/language",
          any(getattr(r, "path", "") == "/api/language" for r in server.app.routes))

    src = inspect.getsource(settings_page)
    check("qt settings page can add a language", "backend.add_language(" in src)

    for mod, name in ((translate_page, "translate"), (quick_page, "quick"), (live_page, "live")):
        check(f"qt {name} page refreshes languages",
              hasattr(mod, "__file__") and "def reload_languages" in inspect.getsource(mod))

    # A custom language is absent from LANGUAGE_MAP, so a bare .get(..., "en")
    # silently translated to English instead of the chosen language.
    check("live page resolves codes via get_language_code",
          "LANGUAGE_MAP.get(self.target_combo" not in inspect.getsource(live_page))


if __name__ == "__main__":
    run([test_custom_language, test_glossary_gets_a_column, test_frontends_expose_it])
