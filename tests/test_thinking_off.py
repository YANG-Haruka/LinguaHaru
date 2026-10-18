# Thinking/reasoning is OFF by default on every backend. A thinking model
# otherwise spends the reply budget reasoning and the JSON comes back truncated
# or EMPTY (verified on LM Studio + qwen3.5-9b: 400/400 tokens of thinking,
# empty content, finish_reason=length), which surfaces as "the whole PDF was
# kept untranslated". Each backend needs a different switch; these tests pin
# the payload each one actually sends, plus the fallback when a provider
# rejects the param, and the PDF log that used to mislabel a validation
# failure as "unparseable".
#
# Run from the repo root:
#   python tests/test_thinking_off.py
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import check, run

MSGS = [{"role": "system", "content": "translate"},
        {"role": "user", "content": '{"1": "Good morning"}'}]


class _Resp:
    def __init__(self, body):
        self.text = json.dumps(body)
    def raise_for_status(self):
        pass


def _capture_offline(reply_content):
    """Monkeypatch offline_translation so translate_offline() talks to a fake
    server; returns the dict the captured request payload lands in."""
    from core.llm import offline_translation as ot
    seen = {}
    def fake_post(url, json=None, timeout=None):
        seen["url"], seen["payload"] = url, json
        if "11434" in url:
            return _Resp({"message": {"content": reply_content},
                          "prompt_eval_count": 3, "eval_count": 2})
        return _Resp({"choices": [{"message": {"content": reply_content}}],
                      "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    ot.requests.post = fake_post
    ot.is_ollama_running = lambda *a, **k: True
    ot.is_lm_studio_running = lambda *a, **k: True
    ot.lm_studio_context = lambda *a, **k: 8192
    return seen


def test_offline_payloads():
    print("Thinking off: Ollama sends think=false, LM Studio reasoning_effort=none")
    from core.llm import offline_translation as ot
    from core.llm import online_translation as on

    seen = _capture_offline('{"1": "早上好"}')
    out, ok, _ = ot.translate_offline(list(MSGS), "(Ollama) qwen3:32b")
    check("ollama call succeeds", ok and "早上好" in out, str(out))
    check("ollama payload has think=false", seen["payload"].get("think") is False,
          str({k: v for k, v in seen["payload"].items() if k != "messages"}))
    check("ollama keeps the /no_think text switch for old servers",
          "/no_think" in seen["payload"]["messages"][-1]["content"])

    out, ok, _ = ot.translate_offline(list(MSGS), "(LM Studio) qwen/qwen3.5-9b")
    check("lm studio call succeeds", ok and "早上好" in out, str(out))
    check("lm studio payload has reasoning_effort=none",
          seen["payload"].get("reasoning_effort") == "none",
          str({k: v for k, v in seen["payload"].items() if k != "messages"}))

    # Opt-out: system_config disable_thinking=false must send neither switch.
    real = on._disable_thinking_default
    on._disable_thinking_default = lambda: False
    try:
        ot.translate_offline(list(MSGS), "(Ollama) qwen3:32b")
        check("opt-out: no think field for ollama", "think" not in seen["payload"])
        ot.translate_offline(list(MSGS), "(LM Studio) x")
        check("opt-out: no reasoning_effort for lm studio",
              "reasoning_effort" not in seen["payload"])
    finally:
        on._disable_thinking_default = real


def test_unclosed_think_is_dropped():
    print("Thinking off: an UNCLOSED <think> tail (truncated reasoning) is stripped")
    from core.llm import offline_translation as ot
    _capture_offline("<think>Let me carefully translate this sen")
    out, ok, _ = ot.translate_offline(list(MSGS), "(Ollama) qwen3:32b")
    check("no reasoning prose reaches the parser", "<think>" not in out and "carefully" not in out,
          repr(out)[:120])
    _capture_offline('<think>plan</think>\n{"1": "早上好"}')
    out, ok, _ = ot.translate_offline(list(MSGS), "(Ollama) qwen3:32b")
    check("a closed think block is still stripped", "plan" not in out and "早上好" in out, repr(out)[:120])


def test_online_switch_per_provider():
    print("Thinking off: online switch chosen per provider host, with rejection fallback")
    from core.llm.online_translation import _thinking_off_params, _create_completion

    cases = {
        "https://api.deepseek.com/v1":                        ({}, {"thinking": {"type": "disabled"}}),
        "https://open.bigmodel.cn/api/paas/v4":               ({}, {"thinking": {"type": "disabled"}}),
        "https://dashscope.aliyuncs.com/compatible-mode/v1":  ({}, {"enable_thinking": False}),
        "https://api.siliconflow.cn/v1":                      ({}, {"enable_thinking": False}),
        "https://openrouter.ai/api/v1":                       ({}, {"reasoning": {"enabled": False}}),
        "https://generativelanguage.googleapis.com/v1beta/openai/": ({"reasoning_effort": "none"}, {}),
        "https://api.openai.com/v1":                          ({"reasoning_effort": "none"}, {}),
        "http://localhost:11434/v1":                          ({"reasoning_effort": "none"}, {}),
        "http://127.0.0.1:1234/v1":                           ({"reasoning_effort": "none"}, {}),
        "https://some.unknown.host/v1":                       ({}, {"thinking": {"type": "disabled"}}),
        "https://api.anthropic.com/v1/":                      ({}, {}),
        "https://ws1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1": ({}, {"enable_thinking": False}),
    }
    for url, want in cases.items():
        got = _thinking_off_params(url)
        check(f"switch for {url.split('/')[2]}", got == want, f"{got} != {want}")

    # Provider rejects the switch -> retried once WITHOUT any thinking key.
    calls = []
    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**params):
                    calls.append(params)
                    if len(calls) == 1:
                        raise RuntimeError("400 Unrecognized request argument: reasoning_effort")
                    return "ok"
    r = _create_completion(_Client, {"model": "m", "messages": [], "reasoning_effort": "none",
                                     "extra_body": {"enable_thinking": False, "keep": 1}})
    check("fallback returns the retried call", r == "ok")
    check("retry dropped reasoning_effort", "reasoning_effort" not in calls[1])
    check("retry dropped enable_thinking but kept other extra_body",
          calls[1].get("extra_body") == {"keep": 1}, str(calls[1].get("extra_body")))

    # A reasoning model rejects custom sampling -> retried on provider defaults.
    calls.clear()

    class _Strict:
        class chat:
            class completions:
                @staticmethod
                def create(**params):
                    calls.append(params)
                    if "temperature" in params:
                        raise RuntimeError("400 Unsupported value: 'temperature' does not support 0.1")
                    return "ok"
    r = _create_completion(_Strict, {"model": "gpt-6-luna", "messages": [], "temperature": 0.1})
    check("sampling rejection retried without temperature",
          r == "ok" and "temperature" not in calls[-1], str(calls))

    from core.translation_modes import _sampling_supported
    check("OpenAI reasoning models skip custom sampling",
          not _sampling_supported({"base_url": "https://api.openai.com/v1", "model": "gpt-6.1-sol"})
          and not _sampling_supported({"base_url": "https://api.openai.com/v1", "model": "o4-mini"})
          and _sampling_supported({"base_url": "https://api.openai.com/v1", "model": "gpt-4o"}))


def test_pdf_log_distinguishes_validation_from_parse():
    print("PDF: a parsed-but-rejected translation is logged as validation, never 'unparseable'")
    import threading
    from core.translators import pdf_translator as pt

    logs = []
    pt.app_logger = types.SimpleNamespace(
        warning=lambda m: logs.append(("W", m)), info=lambda m: None,
        error=lambda m: logs.append(("E", m)), debug=lambda m: None)
    src = "The system provides a robust framework."
    # Model echoes the source (what a local model left thinking on tends to do).
    pt.translate_text = lambda *a, **k: (json.dumps({"1": src}), True, None)
    fake = types.SimpleNamespace(
        check_for_stop=lambda: None, _cancel_event=threading.Event(),
        _proofread_overrides=None, lock=threading.Lock(), _paragraph_counter=0,
        _glossary_entries=[], previous_text_default={}, model="m", use_online=False,
        api_key="", system_prompt="s", user_prompt="u", previous_prompt="p",
        glossary_prompt="", topts={}, _check_stop_and_signal_cancel=lambda: None,
        _add_token_usage=lambda u: None, src_lang="en", dst_lang="zh",
        _qa_pairs=[], _failed_paragraphs=[], _fatal_error=None,
        _parse_single_translation=pt.PdfTranslator._parse_single_translation)
    out = pt.PdfTranslator._translate_paragraph(fake, src)
    text = " | ".join(m for _, m in logs)
    check("source kept when every attempt is rejected", out == src, repr(out))
    check("logged as a validation failure", "failed validation" in text, text[:200])
    check("NOT logged as unparseable", "Unparseable" not in text, text[:300])
    check("the rejected text is shown", src[:20] in text, text[:300])

    # And a genuinely unparseable reply is still reported as such.
    logs.clear()
    pt.translate_text = lambda *a, **k: ("Sure! Here is the translation:", True, None)
    fake._failed_paragraphs = []
    pt.PdfTranslator._translate_paragraph(fake, src)
    text = " | ".join(m for _, m in logs)
    check("garbage reply is logged as unparseable", "Unparseable" in text, text[:200])
    check("garbage reply is not logged as validation", "failed validation" not in text)


if __name__ == "__main__":
    run([test_offline_payloads, test_unclosed_think_is_dropped,
         test_online_switch_per_provider, test_pdf_log_distinguishes_validation_from_parse])
