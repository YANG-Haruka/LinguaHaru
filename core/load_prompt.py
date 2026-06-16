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

        # Inject the language names via str.replace (NOT str.format) so the prompt
        # bodies can contain literal braces in placeholder EXAMPLES ({name}, {0},
        # ${var}, {{token}}) without needing to be doubled/escaped.
        system_prompt = (system_prompt
                         .replace("{Text_Target_Language}", _lang_name(dst_lang))
                         .replace("{Text_Source_Language}", _lang_name(src_lang, source=True)))

        # Append the active translation mode's one-line behavior hint (precise /
        # natural / polish / subtitle), so the chosen mode actually shapes the
        # output beyond sampling params.
        try:
            from core.translation_modes import active_prompt_hint
            hint = active_prompt_hint()
            if hint:
                system_prompt = f"{system_prompt}\n\n{hint}"
        except Exception:  # noqa: BLE001
            pass

        return system_prompt, user_prompt, previous_prompt, previous_text_default, glossary_prompt
