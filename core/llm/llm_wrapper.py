from core.log_config import app_logger
from core.llm.online_translation import translate_online, HardApiError
from core.llm.offline_translation import translate_offline
from core.engine.placeholder_mask import mask as _mask, unmask as _unmask
import json
import re
import time


def _strip_json_fence(s):
    """Return the JSON body of a ```json ...``` block (or the string itself)."""
    s = (s or "").strip().lstrip("﻿")
    s = re.sub(r'^```json\s*\n?|\n?```$', '', s, flags=re.MULTILINE).strip()
    return s


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
    # Set 1-hour time limit (3600 seconds)
    max_retry_time = 3600
    start_time = time.time()

    # Track attempts for logging
    current_attempt = 0
    wait_time = 1

    # PRE-LLM placeholder masking: protect machine tokens (printf specifiers,
    # ${vars}, single-brace ICU placeholders) from being translated/altered.
    # Mapping survives this whole call; replies are unmasked before returning.
    # No-op fast path when no value contains a detectable token.
    segments, _ph_mapping = _mask_segment(segments)

    while (time.time() - start_time) < max_retry_time:
        # Check for stop request at the beginning of each iteration
        if check_stop_callback:
            check_stop_callback()
            
        current_attempt += 1
        
        # Handle dictionary segments
        if isinstance(segments, dict):
            try:
                text_to_translate = json.dumps(segments, ensure_ascii=False)
            except Exception as e:
                app_logger.error(f"Error converting dict to string: {e}")
                text_to_translate = str(segments)
        elif isinstance(segments, list):
            text_to_translate = "\n".join(segments)
        else:
            text_to_translate = segments
        
        # Prepare glossary
        glossary_text = ""
        glossary_prompt_str = str(glossary_prompt) if glossary_prompt else ""
        if glossary_terms and len(glossary_terms) > 0:
            # Only log glossary info on first attempt
            if current_attempt == 1:
                glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
                glossary_text = glossary_prompt_str + "\n".join(glossary_lines) + "\n\n"
                
                glossary_info = "Glossary used:\n"
                glossary_info += " || ".join([f"{src} ==> {dst}" for src, dst in glossary_terms])
                app_logger.info(glossary_info)
            else:
                glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
                glossary_text = glossary_prompt_str + "\n".join(glossary_lines) + "\n\n"
        
        # Prepare components
        previous_prompt_str = str(previous_prompt) if previous_prompt else ""
        previous_text_str = str(previous_text) if previous_text else ""
        user_prompt_str = str(user_prompt) if user_prompt else ""
        text_to_translate_str = str(text_to_translate) if text_to_translate else ""
        
        # Optional per-id type context (opt-in via config; advisory only — output
        # shape is unchanged). Helps disambiguate the same text in different roles
        # (e.g. a button label vs a heading).
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

        # Calculate time status
        elapsed_time = time.time() - start_time
        remaining_time = max_retry_time - elapsed_time

        # Construct full prompt
        try:
            full_user_prompt = f"{context_block}{previous_prompt_str}\n###{previous_text_str}###\n{user_prompt_str}###\n{text_to_translate_str}###\n{glossary_text}"
        except Exception as e:
            app_logger.error(f"Error constructing prompt (attempt {current_attempt}): {e}")
            
            # Check remaining time
            if remaining_time <= 0:
                app_logger.error("Failed to construct prompt after 1 hour of retries.")
                return None, False, None

            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            # Interruptible sleep
            interruptible_sleep(wait_time, check_stop_callback)
            continue
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_user_prompt},
        ]
        
        try:
            # Perform translation - now returns (result, status, token_usage)
            if not use_online:
                translation_result, api_success, token_usage = translate_offline(messages, model)
            else:
                translation_result, api_success, token_usage = translate_online(
                    api_key, messages, model,
                    mode_params=(options.get("params") if options else None))

            # If API call was successful, return the result
            if api_success:
                if current_attempt > 1:
                    app_logger.info(f"Translation succeeded on attempt {current_attempt} after {int(elapsed_time)}s")
                if _ph_mapping:
                    translation_result = _unmask_reply(translation_result, _ph_mapping)
                return translation_result, True, token_usage

            # API call failed (network error, service down, etc.)
            app_logger.warning(f"API call failed (attempt {current_attempt}): {translation_result}")

            # Update time remaining
            elapsed_time = time.time() - start_time
            remaining_time = max_retry_time - elapsed_time

            # Check if we've run out of time
            if remaining_time <= 0:
                app_logger.error(f"Failed to translate after 1 hour ({current_attempt} attempts).")
                return translation_result, False, None

            # Wait before retry with exponential backoff
            wait_time = min(wait_time * 2, 10, remaining_time)
            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            # Interruptible sleep
            interruptible_sleep(wait_time, check_stop_callback)

        except HardApiError:
            # Bad key/config/quota - retrying cannot help, abort the task
            raise
        except Exception as e:
            # Update time remaining
            elapsed_time = time.time() - start_time
            remaining_time = max_retry_time - elapsed_time

            # Check if we've run out of time
            if remaining_time <= 0:
                app_logger.error(f"Translation failed after 1 hour ({current_attempt} attempts): {e}")
                return f"Translation failed after 1 hour: {str(e)}", False, None

            app_logger.error(f"Translation exception (attempt {current_attempt}): {e}")

            # Wait before retry (don't wait longer than remaining time)
            wait_time = min(wait_time * 2, 10, remaining_time)
            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            # Interruptible sleep
            interruptible_sleep(wait_time, check_stop_callback)

    # If we reach here, time limit exceeded
    app_logger.error(f"Failed to translate after 1 hour ({current_attempt} attempts).")
    return None, False, None

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


def translate_text_simple_stream(text, src_lang, dst_lang, model, use_online, api_key):
    """Like translate_text_simple but a GENERATOR yielding the translation
    progressively (cumulative string), for live captions' optional stream mode.

    Online only streams; offline or any failure yields the final result once.
    Isolated from the batch document path (which stays stream=False)."""
    if not text or not text.strip():
        yield text
        return
    if not use_online:
        result, _ok, _u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
        yield result
        return
    system_prompt = (f"You are a professional translator. Translate the following text "
                     f"from {src_lang} to {dst_lang}. Output only the translation, nothing else.")
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": text}]
    try:
        from core.llm.online_translation import load_model_config
        from openai import OpenAI
        cfg = load_model_config(model) or {}
        base_url, api_model = cfg.get("base_url"), cfg.get("model")
        if not (base_url and api_model and api_key):
            result, _ok, _u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
            yield result
            return
        params = {"model": api_model, "messages": messages, "stream": True}
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
            choices = getattr(chunk, "choices", None)
            delta = choices[0].delta.content if choices else None
            if delta:
                acc += delta
                yield _plain_translation(acc)
        if not acc:        # nothing streamed -> fall back to a normal call
            result, _ok, _u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
            yield result
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Stream translate failed ({e}); falling back.")
        result, _ok, _u = translate_text_simple(text, src_lang, dst_lang, model, use_online, api_key)
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