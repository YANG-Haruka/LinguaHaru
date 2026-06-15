"""Styling for the TRANSLATED text in bilingual (双语对照) output, so it stands
out from the original (the #1 ask: same formatting made them hard to tell apart).

Two config knobs (both ends expose them under the bilingual toggle):
  - bilingual_bold   (bool, default True)  — bold the translation
  - bilingual_color  (hex string w/o '#', e.g. "C00000"; "" = no color)

`style_markup(text, family)` wraps the translation with the right markup for a
markup-based format family. DOCX/XLSX style runs/fonts directly via the helpers
below (markup can't be embedded there)."""

from core import backend


def options():
    """(bold, color_hex_without_hash). color is '' when disabled."""
    c = backend.read_config()
    return bool(c.get("bilingual_bold", True)), str(c.get("bilingual_color", "") or "").strip().lstrip("#")


def enabled():
    bold, color = options()
    return bold or bool(color)


def style_markup(text, family):
    """Wrap translated `text` with bold/color markup for a markup family:
    'html' | 'epub' | 'md' | 'srt' | 'vtt'. Returns text unchanged if styling is
    off. VTT gets bold only (inline color needs ::cue stylesheets)."""
    bold, color = options()
    if not text or (not bold and not color):
        return text
    if family in ("html", "epub"):
        if color:
            text = f'<span style="color:#{color}">{text}</span>'
        if bold:
            text = f"<b>{text}</b>"
    elif family == "srt":
        if color:
            text = f'<font color="#{color}">{text}</font>'
        if bold:
            text = f"<b>{text}</b>"
    elif family == "vtt":
        if bold:
            text = f"<b>{text}</b>"
    elif family == "md":
        if color:
            text = f'<span style="color:#{color}">{text}</span>'
        if bold:
            text = f"**{text}**"
    return text


def docx_color():
    """An RGBColor for the translated runs, or None. (python-docx import is lazy
    so importing this module never pulls docx in unrelated code paths.)"""
    _bold, color = options()
    if not color or len(color) != 6:
        return None
    try:
        from docx.shared import RGBColor
        return RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    except Exception:  # noqa: BLE001
        return None
