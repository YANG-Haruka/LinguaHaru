# Tests for the stability trio (error classification, multi-key rotation,
# RPM limiter), user text rules, and the AI glossary parser.
#
# Run from the repo root:
#   python tests/test_stability_and_rules.py
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


class FakeOpenAIError(Exception):
    pass


def test_error_classification():
    print("Error classification + key rotation")
    import llmWrapper.online_translation as ot

    # Reset rotation state
    ot._bad_keys.clear()

    class FakeClient:
        def __init__(self, api_key=None, base_url=None):
            self.api_key = api_key
            FakeClient.last_key = api_key

        class chat:
            class completions:
                @staticmethod
                def create(**params):
                    raise FakeOpenAIError(FakeClient.error_message)

        # instance attribute path used by code: client.chat.completions.create
        def __getattr__(self, item):
            raise AttributeError(item)

    # The code calls client.chat.completions.create - make it instance-level
    class FakeClient2:
        error_message = ""
        last_key = None

        def __init__(self, api_key=None, base_url=None):
            FakeClient2.last_key = api_key
            outer = self

            class _Completions:
                @staticmethod
                def create(**params):
                    raise FakeOpenAIError(FakeClient2.error_message)

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    original = ot.OpenAI
    ot.OpenAI = FakeClient2
    try:
        messages = [{"role": "user", "content": "hi"}]
        model = next(os.path.splitext(f)[0] for f in os.listdir("config/api_config")
                     if f.endswith(".json"))

        # 1. single key + auth error -> HardApiError immediately
        FakeClient2.error_message = "Error code: 401 - invalid api key"
        try:
            ot.translate_online("sk-only-key", messages, model)
            check("single bad key raises HardApiError", False, "no exception")
        except ot.HardApiError:
            check("single bad key raises HardApiError", True)

        # 2. multi-key: first auth error quarantines, returns soft failure
        ot._bad_keys.clear()
        result, success, _ = ot.translate_online("sk-key-A, sk-key-B", messages, model)
        check("multi-key auth error is soft (quarantine + retry)", success is False
              and "quarantined" in result, str(result))

        # 3. when every key is quarantined -> HardApiError
        try:
            for _ in range(4):
                ot.translate_online("sk-key-A, sk-key-B", messages, model)
            check("all keys bad raises HardApiError", False, "no exception")
        except ot.HardApiError:
            check("all keys bad raises HardApiError", True)

        # 4. rate limit stays soft no matter what
        ot._bad_keys.clear()
        FakeClient2.error_message = "Error code: 429 - rate limit exceeded"
        result, success, _ = ot.translate_online("sk-only-key", messages, model)
        check("rate limit is a soft error", success is False and "Rate limit" in result,
              str(result))
    finally:
        ot.OpenAI = original
        ot._bad_keys.clear()


def test_rpm_limiter():
    print("RPM limiter")
    from llmWrapper.online_translation import _RpmLimiter

    limiter = _RpmLimiter()
    limiter.limit = 100  # bypass config load
    start = time.time()
    for _ in range(50):
        limiter.wait()
    check("under the limit there is no blocking", time.time() - start < 0.5)
    check("global window tracks calls", len(limiter.windows["_global"]) == 50,
          str({k: len(v) for k, v in limiter.windows.items()}))

    # Per-model override keeps its own window, independent of the global one
    for _ in range(7):
        limiter.wait(key="(Gemini) Gemini-2.0-flash", limit_override=15)
    check("per-model window independent",
          len(limiter.windows["(Gemini) Gemini-2.0-flash"]) == 7
          and len(limiter.windows["_global"]) == 50,
          str({k: len(v) for k, v in limiter.windows.items()}))


def test_text_rules():
    print("Text rules (replace before/after, no-translate list)")
    import config.text_rules as tr

    rules_path = os.path.join("config", "text_rules.json")
    backup = None
    if os.path.exists(rules_path):
        with open(rules_path, encoding="utf-8") as f:
            backup = f.read()
    try:
        with open(rules_path, "w", encoding="utf-8") as f:
            json.dump({
                "replace_before": [{"from": "TyypoBrand", "to": "TypoBrand"}],
                "replace_after": [{"from": "誤訳語", "to": "正訳語"}],
                "no_translate": ["KeepMeAsIs"],
            }, f, ensure_ascii=False)
        tr._cache["mtime"] = None  # bust cache

        check("replace_before applies",
              tr.apply_replace_before("about TyypoBrand products") == "about TypoBrand products")
        check("replace_after applies",
              tr.apply_replace_after("これは誤訳語です") == "これは正訳語です")
        check("no_translate matches", tr.is_no_translate("  KeepMeAsIs "))

        from pipeline.skip_pipeline import should_translate
        check("skip_pipeline honors no_translate list", not should_translate("KeepMeAsIs"))
        check("other text still translates", should_translate("Regular sentence here"))
    finally:
        if backup is None:
            os.remove(rules_path)
        else:
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write(backup)
        tr._cache["mtime"] = None


def test_glossary_parser():
    print("AI glossary parser")
    from textProcessing.glossary_extractor import _parse_terms

    raw = 'Here you go:\n[["Haruka", "ハルカ"], ["LinguaHaru", "リンガハル"]]\nDone.'
    terms = _parse_terms(raw)
    check("parses pair arrays", terms == [("Haruka", "ハルカ"), ("LinguaHaru", "リンガハル")],
          str(terms))
    check("rejects junk", _parse_terms("no json here") == [])
    check("dict entries accepted",
          _parse_terms('[{"src": "ACME", "dst": "アクメ"}]') == [("ACME", "アクメ")])


def test_desktop_server():
    print("Desktop shell: embedded server starts and serves the UI")
    import urllib.request
    from app_desktop import start_server

    app_module, port = start_server()
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=10).read()
        check("Gradio responds through the embedded server", b"gradio" in html.lower(),
              html[:120])
    finally:
        app_module.demo.close()


def main():
    for fn in (test_error_classification, test_rpm_limiter, test_text_rules,
               test_glossary_parser, test_desktop_server):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            FAILED.append(fn.__name__ + " (crashed)")
        print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAIL: {name}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
