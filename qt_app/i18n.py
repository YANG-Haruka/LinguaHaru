"""Tiny i18n helper for the Qt UI.

Reuses config.languages_config.LABEL_TRANSLATIONS (the same label dictionary the
Gradio web app uses). tr(key, lang) returns the localized string for the chosen
UI language, falling back to English, then to the key itself - so a missing key
never crashes the UI.

The chosen UI language is persisted to system_config.json under "qt_ui_lang"
(see backend.get_config / set_config); helpers here just resolve strings.
"""

from config.languages_config import LABEL_TRANSLATIONS

# UI languages we expose in the selector (those with the most label coverage).
UI_LANGS = ["en", "zh", "zh-Hant", "ja"]

# Display names for the language selector ComboBox.
UI_LANG_NAMES = {
    "en": "English",
    "zh": "简体中文",
    "zh-Hant": "繁體中文",
    "ja": "日本語",
}


def tr(key, lang="en"):
    """Localized label for ``key`` in ``lang``.

    Falls back to the English label, then to ``key`` itself, so an absent key
    is returned verbatim rather than raising."""
    labels = LABEL_TRANSLATIONS.get(lang) or {}
    if key in labels:
        return labels[key]
    en = LABEL_TRANSLATIONS.get("en", {})
    return en.get(key, key)


def lang_display_name(lang):
    return UI_LANG_NAMES.get(lang, lang)


def lang_from_display_name(display):
    for code, name in UI_LANG_NAMES.items():
        if name == display:
            return code
    return "en"
