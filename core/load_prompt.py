import json
import os

from core.languages_config import LANGUAGE_MAP
from core.paths import PROMPTS_DIR

# code -> readable native name (reverse of LANGUAGE_MAP), e.g. "en" -> "English".
_CODE_TO_NAME = {code: name for name, code in LANGUAGE_MAP.items()}


def _lang_name(code, source=False):
    """Human-readable language name for a code, for the prompt.

    Using a name ("English", "中文") instead of a bare code ("en", "zh") reads
    better for the model. "auto" means detect the source language."""
    if code == "auto":
        return "the source language (detect it automatically)" if source else "auto"
    return _CODE_TO_NAME.get(code, code)


def load_prompt(src_lang, dst_lang):
    """Load the translation prompt from a JSON file based on the target language."""
    # The prompt template lives in the TARGET language's file. Source may be
    # "auto" (auto-detect); target is always concrete (fall back to en if not).
    lang_code = dst_lang if dst_lang and dst_lang != "auto" else "en"
    prompt_path = os.path.join(PROMPTS_DIR, f"{lang_code}.json")

    with open(prompt_path, "r", encoding="utf-8") as file:
        prompt_data = json.load(file)

        # Extract prompts
        system_prompt = prompt_data.get("system_prompt", "")
        user_prompt = prompt_data.get("user_prompt", "Translate the following text:")
        previous_prompt = prompt_data.get("previous_prompt", "Context for disambiguation only. Do not translate it or include it in the output:")
        previous_text_default = prompt_data.get("previous_text_default", {})
        glossary_prompt = prompt_data.get("glossary_prompt", {})

        # Source-language name: use the target file's LOCALIZED auto-source label
        # when the source is auto-detect, so a non-English prompt doesn't get an
        # English clause spliced in ("从 the source language...").
        if not src_lang or src_lang == "auto":
            src_name = prompt_data.get("auto_source_label") or _lang_name("auto", source=True)
        else:
            src_name = _lang_name(src_lang)

        # Inject the language names via str.replace (NOT str.format) so the prompt
        # bodies can contain literal braces in placeholder EXAMPLES ({name}, {0},
        # ${var}, {{token}}) without needing to be doubled/escaped.
        system_prompt = (system_prompt
                         .replace("{Text_Target_Language}", _lang_name(dst_lang))
                         .replace("{Text_Source_Language}", src_name))

        # Append the active mode's behavior hint + advanced (tone/length/style)
        # modifiers — all taken from the TARGET-language prompt file so no English
        # is mixed into a non-English prompt. Falls back to the English versions in
        # core.translation_modes only if a file lacks the localized maps.
        try:
            from core import translation_modes as _tmod
            from core import backend as _backend
            mode = _tmod.get_active_mode()
            mode_hint = (prompt_data.get("mode_hints") or {}).get(mode) or _tmod.active_prompt_hint()
            if mode_hint:
                system_prompt = f"{system_prompt}\n\n{mode_hint}"

            adv_parts = []
            tone = str(_backend.get_config("translation_tone", "") or "")
            length = str(_backend.get_config("translation_length", "") or "")
            style = str(_backend.get_config("translation_style", "") or "").strip()
            th = (prompt_data.get("tone_hints") or {}).get(tone)
            lh = (prompt_data.get("length_hints") or {}).get(length)
            if th:
                adv_parts.append(th)
            if lh:
                adv_parts.append(lh)
            if style:
                adv_parts.append(style)
            if not th and not lh:   # localized maps missing -> English fallback
                fb = _tmod.active_advanced_hint()
                if fb:
                    adv_parts = [fb]
            if adv_parts:
                system_prompt = f"{system_prompt}\n" + " ".join(adv_parts)
        except Exception:  # noqa: BLE001
            pass

        return system_prompt, user_prompt, previous_prompt, previous_text_default, glossary_prompt
