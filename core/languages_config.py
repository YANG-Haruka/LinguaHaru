"""Language map + i18n label tables.

UI strings live in per-locale JSON files under ``config/locales/<lang>.json``
(proper i18n) and are loaded into ``LABEL_TRANSLATIONS`` at import. English is the
source of truth: any key missing in another language falls back to the English
value. To add or change a label, edit the locale JSON files — not this module.
"""

import os
import json
import shutil

from core.paths import PROMPTS_DIR


def create_custom_language_prompt_file(custom_language_name):
    """Create a new prompt file for a custom language by copying en.json"""
    prompts_dir = PROMPTS_DIR
    source_file = os.path.join(prompts_dir, "en.json")
    target_file = os.path.join(prompts_dir, f"{custom_language_name}.json")

    try:
        # Ensure the prompts directory exists
        os.makedirs(prompts_dir, exist_ok=True)

        # Copy en.json to the new custom language file
        if os.path.exists(source_file):
            shutil.copy2(source_file, target_file)
            return True, f"Created prompt file for {custom_language_name}"
        else:
            return False, "Source file en.json not found"
    except Exception as e:
        return False, f"Error creating prompt file: {str(e)}"


def add_custom_language(custom_language_name):
    """Add a custom language to the system"""
    if not custom_language_name or custom_language_name.strip() == "":
        return False, "Language name cannot be empty"

    custom_language_name = custom_language_name.strip()

    # Create the prompt file
    success, message = create_custom_language_prompt_file(custom_language_name)

    if success:
        return True, f"Custom language '{custom_language_name}' added successfully"
    else:
        return False, message


def get_available_languages():
    """Read language files from config/prompts directory and return display names"""
    prompts_dir = PROMPTS_DIR
    available_languages = []

    if os.path.exists(prompts_dir):
        # Get all .json files in the prompts directory
        for filename in os.listdir(prompts_dir):
            if filename.endswith(".json"):
                # Get language code without extension
                lang_code = os.path.splitext(filename)[0]

                # Find the display name from LANGUAGE_MAP
                display_name_found = False
                for display_name, code in LANGUAGE_MAP.items():
                    if code == lang_code:
                        available_languages.append(display_name)
                        display_name_found = True
                        break

                # If language code not found in LANGUAGE_MAP, add it directly
                if not display_name_found:
                    available_languages.append(lang_code)

    # If no languages found, return default list
    if not available_languages:
        available_languages = [
            "English", "中文", "繁體中文", "日本語", "Español",
            "Français", "Deutsch", "Italiano", "Português",
            "Русский", "한국어", "ภาษาไทย", "Tiếng Việt"
        ]

    return sorted(set(available_languages))


def get_language_code(display_name):
    """Get language code from display name, supporting custom languages"""
    # First check if it's in the existing LANGUAGE_MAP
    if display_name in LANGUAGE_MAP:
        return LANGUAGE_MAP[display_name]

    # If not found, assume the display name is the language code; lowercase it
    # to match the file naming convention
    return display_name.lower()


LANGUAGE_MAP = {
    "日本語": "ja",
    "中文": "zh",
    "繁體中文": "zh-Hant",
    "English": "en",
    "Español": "es",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "Português": "pt",
    "Русский": "ru",
    "한국어": "ko",
    "ภาษาไทย": "th",
    "Tiếng Việt": "vi",
}

# --------------------------------------------------------------------------- #
# i18n labels — loaded from config/locales/*.json
# --------------------------------------------------------------------------- #
# locale JSONs are data, kept under config/locales/ (this module lives in core/).
_LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "locales")


def _load_label_translations():
    """Load every config/locales/<lang>.json into {lang: {key: text}}, then
    backfill missing keys in each language from English (source of truth)."""
    tables = {}
    try:
        for filename in sorted(os.listdir(_LOCALES_DIR)):
            if not filename.endswith(".json"):
                continue
            lang = os.path.splitext(filename)[0]
            try:
                with open(os.path.join(_LOCALES_DIR, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                tables[lang] = data if isinstance(data, dict) else {}
            except Exception:
                tables[lang] = {}
    except FileNotFoundError:
        pass

    en = tables.get("en", {})
    for lang, labels in tables.items():
        if lang == "en":
            continue
        for key, value in en.items():
            labels.setdefault(key, value)
    return tables


LABEL_TRANSLATIONS = _load_label_translations()
