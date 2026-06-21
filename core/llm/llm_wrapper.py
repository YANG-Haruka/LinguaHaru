from core.log_config import app_logger
from core.llm.online_translation import translate_online, HardApiError
from core.llm.offline_translation import translate_offline
from core.engine.placeholder_mask import mask as _mask, unmask as _unmask
import json
import re
import time

# Transport-layer retry budget per LLM call (transient network / 5xx / 429).
# Semantic failures are NOT retried here — the caller re-queues failed items.
_TRANSPORT_MAX_ATTEMPTS = 3


def _strip_json_fence(s):
    """Return the JSON body of a ```json ...``` block (or the string itself)."""
    s = (s or "").strip().lstrip("﻿")
    s = re.sub(r'^```json\s*\n?|\n?```$', '', s, flags=re.MULTILINE).strip()
    return s


def _cache_enabled():
    try:
        from core import backend
        return bool(backend.get_config("translation_cache", False))
    except Exception:  # noqa: BLE001
        return False


def _seg_items(segment):
    """Parse a ```json {count: text}``` segment string (or dict) into a dict, or
    None if it isn't that shape."""
    if isinstance(segment, dict):
        return {k: v for k, v in segment.items() if isinstance(v, str)}
    if not isinstance(segment, str):
        return None
    try:
        data = json.loads(_strip_json_fence(segment))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else None


def _cache_sig(model, system_prompt, user_prompt, previous_prompt, glossary_terms,
               options, previous_text="", context_map=None):
    """params_sig over EVERYTHING that determines this segment's output: model,
    the (language/style-encoding) prompts, matched glossary, sampling mode/temp,
    masking flag, AND the actual context fed into the prompt — the preceding-text
    context and the per-id type map. Without the context, the same source seen in
    two different contexts would wrongly share one cached translation."""
    from core.engine import translation_cache as tc
    import hashlib
    ctx = (str(previous_text or "")
           + "\x00" + (json.dumps(context_map, sort_keys=True, ensure_ascii=False)
                       if context_map else ""))
    prompt_h = hashlib.sha1(
        ("".join(str(p) for p in (system_prompt, user_prompt, previous_prompt)) + "\x00" + ctx)
        .encode("utf-8")).hexdigest()[:12]
    mode = ""
    temp = None
    top_p = None
    if options:
        params = options.get("params") or {}
        mode = options.get("mode", "") or ""
        temp = params.get("temperature")
        top_p = params.get("top_p")
    try:
        from core.engine.placeholder_mask import _mask_enabled
        mask = _mask_enabled()
    except Exception:  # noqa: BLE001
        mask = True
    # Fold top_p into the prompt_version hash too (params_sig has no top_p slot),
    # so a sampling change can't reuse a stale translation. ALSO fold the actual
    # interface config (base_url / real api model / thinking_type) so editing a
    # same-NAMED interface to point at a different backend doesn't reuse the old
    # cache (the model NAME alone is not enough — it's user-editable).
    iface_h = ""
    try:
        from core import backend
        cfg = backend.read_api_config(model) or {}
        iface_h = hashlib.sha1(
            json.dumps({k: cfg.get(k) for k in ("base_url", "model", "thinking_type")},
                       sort_keys=True).encode("utf-8")).hexdigest()[:10]
    except Exception:  # noqa: BLE001
        iface_h = ""
    pv = f"{prompt_h}|tp={top_p}|if={iface_h}"
    return tc.params_sig(model, "", "", mode=mode, temperature=temp,
                         glossary_hash=tc.glossary_hash(glossary_terms),
                         mask=mask, prompt_version=pv)


def cache_store_validated(segment, translated_results, model, system_prompt,
                          user_prompt, previous_prompt, glossary_terms, options,
                          previous_text="", context_map=None):
    """Write VALIDATED (source -> translation) pairs to the TM. Called by the
    translator only after process_translation_results has accepted the items, so
    only clean translations enter the cache. ``translated_results`` is the
    {count: translated} validated dict; sources come from ``segment``. No-op when
    the cache is disabled. Never raises."""
    if not _cache_enabled() or not translated_results:
        return
    try:
        items = _seg_items(segment) or {}
        pairs = [(items[k], translated_results[k]) for k in translated_results if k in items]
        if not pairs:
            return
        from core.engine import translation_cache as tc
        sig = _cache_sig(model, system_prompt, user_prompt, previous_prompt,
                         glossary_terms, options, previous_text, context_map)
        tc.put_many(pairs, sig)
    except Exception as e:  # noqa: BLE001 — cache must never break a translation
        app_logger.warning(f"TM store skipped: {e}")


def _mask_segment(segment):
    """Mask machine tokens in each value of a {count: text} segment.

    Returns (masked_segment_str, mapping) where mapping is {count: {idx: tok}}.
    The segment normally arrives as a ```json {...}``` block string; when it is
    not parseable as that, masking is skipped (mapping empty) and the original
    is sent through unchanged."""
    if not isinstance(segment, str):
        return segment, {}
    try:
        data = json.loads(_strip_json_fence(segment))
    except (json.JSONDecodeError, ValueError, TypeError):
        return segment, {}
    if not isinstance(data, dict):
        return segment, {}

    mapping = {}
    changed = False
    masked = {}
    for key, value in data.items():
        if isinstance(value, str):
            masked_value, m = _mask(value)
            masked[key] = masked_value
            if m:
                mapping[key] = m
                changed = True
        else:
            masked[key] = value
    if not changed:
        return segment, {}
    masked_str = f"```json\n{json.dumps(masked, ensure_ascii=False, indent=4)}\n```"
    return masked_str, mapping


def _unmask_reply(reply, mapping):
    """Restore masked tokens in the model's reply using the per-key mapping.

    Best-effort: if the reply is not parseable JSON, restore sentinels found in
    the raw string against the union of all per-key maps (robust to dropped /
    duplicated sentinels). Returns the unmasked reply string."""
    if not mapping or not isinstance(reply, str):
        return reply
    try:
        data = json.loads(_strip_json_fence(reply))
    except (json.JSONDecodeError, ValueError, TypeError):
        data = None

    if isinstance(data, dict):
        for key, value in list(data.items()):
            if isinstance(value, str) and key in mapping:
                data[key] = _unmask(value, mapping[key])
        return json.dumps(data, ensure_ascii=False)

    # Fallback: unmask against the merged map (last index wins on collision,
    # which is fine since indices are per-key and identical tokens restore same).
    merged = {}
    for m in mapping.values():
        merged.update(m)
    return _unmask(reply, merged)


def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt, glossary_prompt, glossary_terms=None, check_stop_callback=None, context_map=None, options=None):
    """
    Translate text segments with optional glossary support

    Returns:
        tuple: (translation_result, success_status, token_usage)
            - token_usage: dict with 'prompt_tokens', 'completion_tokens', 'total_tokens' or None
    """
    # TRANSPORT-LAYER retry only: a few bounded attempts for transient network /
    # 5xx / 429 failures (translate_online already does AIMD + cooldown per call).
    # Semantic failures (bad JSON / missing lines / failed checks) are NOT retried
    # here — the caller validates the result and re-queues failed items (so we
    # never re-bill a whole batch for one bad line). HardApiError (401/402/422)
    # aborts immediately. The old 1-hour in-call loop is gone.
    import random
    max_attempts = _TRANSPORT_MAX_ATTEMPTS

    # Persistent TM/cache (opt-in): if EVERY item in this segment is already
    # cached for the same params signature, return the assembled translation
    # without an LLM call (zero tokens). Mixed segments fall through and are
    # cached after a successful translation (whole-item reuse; v1 is all-or-none
    # per segment to stay correct without partial-merge fragility).
    _cache_items = None
    _cache_sig_val = None
    if _cache_enabled():
        _cache_items = _seg_items(segments)
        if _cache_items:
            try:
                from core.engine import translation_cache as tc
                _cache_sig_val = _cache_sig(model, system_prompt, user_prompt,
                                            previous_prompt, glossary_terms, options,
                                            previous_text, context_map)
                hits = tc.get_many(_cache_items.values(), _cache_sig_val)
                if hits and all(v in hits for v in _cache_items.values()):
                    merged = {k: hits[v] for k, v in _cache_items.items()}
                    app_logger.info(f"TM: whole-segment cache hit ({len(merged)} items)")
                    return (f"```json\n{json.dumps(merged, ensure_ascii=False, indent=4)}\n```",
                            True, None)
            except Exception as e:  # noqa: BLE001 — cache must never break a translation
                app_logger.warning(f"TM lookup skipped: {e}")

    # PRE-LLM placeholder masking: protect machine tokens (printf specifiers,
    # ${vars}, single-brace ICU placeholders) from being translated/altered.
    segments, _ph_mapping = _mask_segment(segments)

    # Build the prompt ONCE (identical across transport retries -> also helps the
    # provider's prefix cache hit).
    if isinstance(segments, dict):
        try:
            text_to_translate = json.dumps(segments, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Error converting dict to string: {e}")
            text_to_translate = str(segments)
    elif isinstance(segments, list):
        text_to_translate = "\n".join(segments)
    else:
        text_to_translate = segments

    glossary_text = ""
    glossary_prompt_str = str(glossary_prompt) if glossary_prompt else ""
    if glossary_terms:
        glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
        glossary_text = glossary_prompt_str + "\n".join(glossary_lines) + "\n\n"
        from core.llm.online_translation import debug_llm_io
        if debug_llm_io():
            app_logger.info("Glossary used: " +
                            " || ".join(f"{src} ==> {dst}" for src, dst in glossary_terms))
        else:
            app_logger.info(f"Glossary: {len(glossary_terms)} term(s) applied")

    previous_prompt_str = str(previous_prompt) if previous_prompt else ""
    previous_text_str = str(previous_text) if previous_text else ""
    user_prompt_str = str(user_prompt) if user_prompt else ""
    text_to_translate_str = str(text_to_translate) if text_to_translate else ""

    # Optional per-id type context (advisory only; opt-in).
    context_block = ""
    if context_map:
        try:
            if options is not None:
                with_ctx = bool(options.get("with_context"))
            else:
                from core import backend
                with_ctx = bool(backend.get_config("translate_with_context", False))
            if with_ctx:
                context_block = ("The following maps each id to its content type, "
                                 "for disambiguation only — do NOT translate it or "
                                 "include it in the output:\n"
                                 + json.dumps(context_map, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            context_block = ""

    full_user_prompt = f"{context_block}{previous_prompt_str}\n###{previous_text_str}###\n{user_prompt_str}###\n{text_to_translate_str}###\n{glossary_text}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": full_user_prompt},
    ]

    last_result = None
    for attempt in range(1, max_attempts + 1):
        if check_stop_callback:
            check_stop_callback()
        try:
            if not use_online:
                translation_result, api_success, token_usage = translate_offline(messages, model)
            else:
                translation_result, api_success, token_usage = translate_online(
                    api_key, messages, model,
                    mode_params=(options.get("params") if options else None),
                    json_mode=True)   # batch path outputs a JSON object

            if api_success:
                if attempt > 1:
                    app_logger.info(f"Translation succeeded on transport attempt {attempt}")
                if _ph_mapping:
                    translation_result = _unmask_reply(translation_result, _ph_mapping)
                # NOTE: the TM is written by the CALLER *after* validation
                # (cache_store_validated), never here — an unvalidated reply (wrong
                # language / repetition / dropped placeholder) must not pollute it.
                return translation_result, True, token_usage

            last_result = translation_result
            # A non-retryable request error (400/404/422/413): retrying the same
            # input can't help — stop transport retries and defer to the caller
            # (which shrinks the batch / falls back).
            if token_usage and token_usage.get("noretry"):
                app_logger.warning("Non-retryable request error; not retrying transport.")
                return last_result, False, None
            app_logger.warning(f"API call failed (attempt {attempt}/{max_attempts}): {translation_result}")
        except HardApiError:
            raise   # 401/402/422 — retrying can't help, abort the task
        except Exception as e:  # noqa: BLE001
            last_result = f"Error: {e}"
            app_logger.error(f"Translation exception (attempt {attempt}/{max_attempts}): {e}")

        if attempt < max_attempts:   # bounded backoff + jitter, then give up to the caller
            wait_time = min(2 ** (attempt - 1), 10) + random.uniform(0, 0.5)
            interruptible_sleep(wait_time, check_stop_callback)

    app_logger.warning(f"Transport retries exhausted ({max_attempts}); deferring to caller's retry queue.")
    return last_result, False, None

def interruptible_sleep(duration, check_stop_callback=None):
    """Sleep that can be interrupted by checking stop callback"""
    interval = 0.1  # Check every 100ms
    elapsed = 0

    while elapsed < duration:
        if check_stop_callback:
            check_stop_callback()  # This will raise exception if stop is requested

        sleep_time = min(interval, duration - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time


def _plain_translation(result):
    """Return clean translated text from a model reply.

    The translate_online/offline backends wrap output as JSON
    ({"translated_text": "..."} or {"1": "..."}); the real-time voice UI wants
    just the text, with no braces / key names. Falls back to the raw string."""
    s = (result or "").strip()
    if s.startswith("```"):  # strip code fences if any
        import re
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s).strip()
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s
    if isinstance(data, dict) and data:
        for key in ("translated_text", "translation", "text", "1"):
            if isinstance(data.get(key), str):
                return data[key].strip()
        for v in data.values():  # any first string value
            if isinstance(v, str):
                return v.strip()
    return s


def translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key, context=""):
    """
    Simple text translation without complex prompt templates.
    Used by BabelDOC integration for direct text translation.

    Args:
        text: Text to translate
        src_lang: Source language code
        dst_lang: Target language code
        model: Model name to use
        use_online: Whether to use online API
        api_key: API key for online translation
        context: Optional disambiguation hint (e.g. "button label, File menu").
            Used to pick the right meaning of short ambiguous text; never
            translated or echoed.

    Returns:
        tuple: (translation_result, success_status, token_usage)
    """
    if not text or not text.strip():
        return text, True, None

    # Simple system prompt for translation
    system_prompt = f"You are a professional translator. Translate the following text from {src_lang} to {dst_lang}. Output only the translation, nothing else."
    if context and str(context).strip():
        system_prompt += (" Context for disambiguation only (do NOT translate it or"
                          f" include it in the output): {str(context).strip()}")

    # User message is just the text to translate
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        if not use_online:
            translation_result, api_success, token_usage = translate_offline(messages, model)
        else:
            translation_result, api_success, token_usage = translate_online(api_key, messages, model)

        if api_success and translation_result:
            return _plain_translation(translation_result), True, token_usage
        else:
            app_logger.warning(f"Simple translation failed: {translation_result}")
            return text, False, token_usage

    except Exception as e:
        app_logger.error(f"Simple translation error: {e}")
        return text, False, None


def translate_text_simple_stream(text, src_lang, dst_lang, model, use_online, api_key,
                                 usage_sink=None, context=""):
    """Like translate_text_simple but a GENERATOR yielding the translation
    progressively (cumulative string), for live captions' optional stream mode.

    Online only streams; offline or any failure yields the final result once.
    Isolated from the batch document path (which stays stream=False).

    If `usage_sink` (a dict) is given, the total token count is written to
    usage_sink["total_tokens"] once available (streamed via stream_options, or
    from the fallback call) so the caller can bill streamed live captions."""
    def _sink(u):
        if usage_sink is not None and u:
            usage_sink["total_tokens"] = int(u.get("total_tokens", 0) or 0)

    if not text or not text.strip():
        yield text
        return
    if not use_online:
        result, _ok, u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
        _sink(u)
        yield result
        return
    system_prompt = (f"You are a professional translator. Translate the following text "
                     f"from {src_lang} to {dst_lang}. Output only the translation, nothing else.")
    if context and str(context).strip():
        system_prompt += (" Context for disambiguation only (do NOT translate it or"
                          f" include it in the output): {str(context).strip()}")
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": text}]
    try:
        from core.llm.online_translation import load_model_config
        from openai import OpenAI
        cfg = load_model_config(model) or {}
        base_url, api_model = cfg.get("base_url"), cfg.get("model")
        if not (base_url and api_model and api_key):
            result, _ok, u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
            _sink(u)
            yield result
            return
        # include_usage -> the final stream chunk carries a usage object.
        params = {"model": api_model, "messages": messages, "stream": True,
                  "stream_options": {"include_usage": True}}
        # Apply the active mode's sampling (+ provider capability gate) like the
        # batch path — so e.g. precise pins temperature low for live captions too.
        try:
            from core.translation_modes import resolve_sampling
            _temp, _top_p = resolve_sampling(cfg, cfg.get("temperature"), cfg.get("top_p"))
        except Exception:  # noqa: BLE001
            _temp, _top_p = cfg.get("temperature"), cfg.get("top_p")
        if _temp is not None:
            params["temperature"] = _temp
        if _top_p is not None:
            params["top_p"] = _top_p
        client = OpenAI(api_key=api_key, base_url=base_url)
        acc = ""
        for chunk in client.chat.completions.create(**params):
            usage = getattr(chunk, "usage", None)
            if usage is not None:   # final chunk: usage present, choices empty
                _sink({"total_tokens": getattr(usage, "total_tokens", 0) or 0})
            choices = getattr(chunk, "choices", None)
            delta = choices[0].delta.content if choices else None
            if delta:
                acc += delta
                yield _plain_translation(acc)
        if not acc:        # nothing streamed -> fall back to a normal call
            result, _ok, u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
            _sink(u)
            yield result
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Stream translate failed ({e}); falling back.")
        result, _ok, u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
        _sink(u)
        yield result


def polish_translation(translated_json, dst_lang, model, use_online, api_key, check_stop=None, options=None):
    """Second pass for the 'polish' mode: improve the fluency / word choice of an
    already-translated JSON object's values, in the TARGET language, without
    changing meaning, keys, or non-text tokens.

    Returns (text, token_usage). SAFE: returns the ORIGINAL first-pass JSON
    unchanged (with the polish call's token usage, if any) unless the polish
    output is valid JSON with EXACTLY the same keys AND the same placeholders per
    value — so a bad second pass never corrupts/drops a translation or mangles a
    %s / ${var} / {name}."""
    import json as _json
    import re as _re
    if not translated_json or not str(translated_json).strip():
        return translated_json, None
    try:
        orig = _json.loads(translated_json)
        if not isinstance(orig, dict):
            return translated_json, None
    except (ValueError, TypeError):
        return translated_json, None   # not a JSON object we can validate -> skip

    from core.load_prompt import _lang_name
    dst = _lang_name(dst_lang)
    system_prompt = (
        f"You are a professional {dst} copy editor. The input is a JSON object whose "
        f"values are already written in {dst}. Improve their fluency, naturalness, and "
        f"word choice WITHOUT changing the meaning. Return exactly ONE JSON object with "
        f"the SAME keys (do not add, remove, or rename keys), no Markdown code fences. "
        f"Keep every placeholder, variable, tag, escape sequence, URL, number, and code "
        f"fragment EXACTLY as it is.")
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": "Polish the values and output only the JSON:\n" + translated_json}]
    if callable(check_stop):
        check_stop()
    try:
        if use_online:
            from core.llm.online_translation import translate_online
            raw, ok, usage = translate_online(
                api_key, messages, model,
                mode_params=(options.get("params") if options else None))
        else:
            from core.llm.offline_translation import translate_offline
            raw, ok, usage = translate_offline(messages, model)
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Polish pass failed ({e}); keeping first-pass translation.")
        return translated_json, None
    if not ok or not raw:
        return translated_json, usage
    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if not m:
        return translated_json, usage
    try:
        polished = _json.loads(m.group(0))
    except ValueError:
        return translated_json, usage
    if not isinstance(polished, dict) or set(polished.keys()) != set(orig.keys()):
        return translated_json, usage   # key drift -> keep the safe first pass
    # Placeholder integrity: the polish must not add/drop/alter any placeholder
    # in any value (it runs after unmasking, so %s/${var}/{name} are live text).
    from core.engine.translation_qa import _placeholders
    for k in orig:
        if _placeholders(orig[k]) != _placeholders(polished.get(k, "")):
            app_logger.warning("Polish pass changed placeholders; keeping first-pass translation.")
            return translated_json, usage
    return _json.dumps(polished, ensure_ascii=False), usage