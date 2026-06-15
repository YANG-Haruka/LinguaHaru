"""Translation coverage report.

After a translation finishes, summarize WHAT got translated, broken down by
content category, and how many segments fell back to the original (untranslated).
This builds user trust ("能翻的都翻" verification).

Pure / unit-testable: ``summarize(src_json_path, dst_json_path)`` reads the run's
extracted source (``src.json``: items with ``count_src`` / ``type`` / ``value``)
and the restored result (``dst_translated.json``: items with ``count_src`` /
``type`` / ``original`` / ``translated``) and returns a plain dict. It never
raises — missing files or bad JSON yield a zeroed report.
"""

import json

from core.log_config import app_logger

# Friendly Chinese category labels (the report groups raw item "type" strings
# into these buckets). Kept as plain constants — they're data values in the
# report dict, surfaced verbatim by both frontends.
CAT_BODY = "正文"
CAT_TABLE = "表格"
CAT_COMMENT = "批注"
CAT_HEADER_FOOTER = "页眉/页脚"
CAT_IMAGE_CAPTION = "图片说明"
CAT_MASTER = "母版/版式"
CAT_SUBTITLE = "字幕"
CAT_METADATA = "元数据"
CAT_TOC = "目录"
CAT_FORMULA_FIELD = "公式/字段"
CAT_OTHER = "其它"

# Raw pipeline "type" string -> friendly category. Built by grepping every
# `"type": "..."` across core/pipelines/. Unknown types fall through to CAT_OTHER
# (and are logged), so the map can grow without breaking the report.
TYPE_TO_CATEGORY = {
    # ---- 正文 (body text) ----
    "text": CAT_BODY,
    "paragraph": CAT_BODY,
    "text_paragraph": CAT_BODY,
    "sdt_paragraph": CAT_BODY,
    "shape": CAT_BODY,
    "textbox": CAT_BODY,
    "group_textbox": CAT_BODY,
    "notes": CAT_BODY,
    "footnote": CAT_BODY,
    "endnote": CAT_BODY,
    "front_matter": CAT_BODY,
    # markdown HTML-embedded content is still body prose
    "html_content": CAT_BODY,
    "html_simple": CAT_BODY,
    "html_complex_content": CAT_BODY,

    # ---- 表格 (tables) ----
    "cell": CAT_TABLE,
    "table_cell": CAT_TABLE,
    "table_cell_paragraph": CAT_TABLE,
    "sdt_table_cell": CAT_TABLE,
    "header_footer_table_cell": CAT_TABLE,
    "html_table_cell": CAT_TABLE,
    "html_table": CAT_TABLE,

    # ---- 批注 (comments) ----
    "comment": CAT_COMMENT,
    "excel_comment": CAT_COMMENT,
    "excel_threadedcomment": CAT_COMMENT,
    "ppt_comment": CAT_COMMENT,

    # ---- 页眉/页脚 (headers / footers) ----
    "header_footer": CAT_HEADER_FOOTER,
    "header_footer_textbox": CAT_HEADER_FOOTER,
    "excel_headerfooter": CAT_HEADER_FOOTER,

    # ---- 图片说明 (image alt text / captions) ----
    "attr": CAT_IMAGE_CAPTION,
    "word_alttext": CAT_IMAGE_CAPTION,
    "ppt_alttext": CAT_IMAGE_CAPTION,
    "excel_alttext": CAT_IMAGE_CAPTION,
    "odt_imagealt": CAT_IMAGE_CAPTION,

    # ---- 母版/版式 (slide masters / layouts) ----
    "master_layout": CAT_MASTER,
    "ppt_notesmaster": CAT_MASTER,
    "ppt_handoutmaster": CAT_MASTER,

    # ---- 字幕 (subtitles) ----
    "subtitle": CAT_SUBTITLE,
    "srt": CAT_SUBTITLE,
    "vtt": CAT_SUBTITLE,
    "ass": CAT_SUBTITLE,

    # ---- 元数据 (metadata) ----
    "opf_meta": CAT_METADATA,
    "odt_meta": CAT_METADATA,
    "ncx_nav": CAT_METADATA,
    "sheet_name": CAT_METADATA,
    "is_sheet_name": CAT_METADATA,

    # ---- 目录 (table of contents / numbering) ----
    "numbering_level_text": CAT_TOC,
    "numbering_text_node": CAT_TOC,
    "toc": CAT_TOC,

    # ---- 公式/字段 (charts / smartart / data fields) ----
    "chart": CAT_FORMULA_FIELD,
    "chart_part": CAT_FORMULA_FIELD,
    "excel_chart": CAT_FORMULA_FIELD,
    "smartart": CAT_FORMULA_FIELD,
    "excel_smartart": CAT_FORMULA_FIELD,
    "excel_datavalidation": CAT_FORMULA_FIELD,
    "excel_drawing": CAT_FORMULA_FIELD,
}


def _zeroed():
    return {"total": 0, "translated": 0, "fallback": 0, "by_category": {}}


def _load(path):
    """Load a JSON list of dict items; [] on any failure (never raises)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def category_for(item_type):
    """Map a raw pipeline ``type`` to a friendly category (CAT_OTHER if unknown)."""
    return TYPE_TO_CATEGORY.get(item_type, CAT_OTHER)


def summarize(src_json_path, dst_json_path):
    """Compute a coverage report for one finished translation.

    Returns a dict:
      - ``total``: extracted segments (from src.json; dst is used if src absent)
      - ``translated``: segments with a real translation (non-empty AND differs
        from the original — an unchanged value means it fell back to source)
      - ``fallback``: segments left as the original (empty translation, or equal
        to the original)
      - ``by_category``: {friendly_category: count} over ALL extracted segments

    Robust: missing files / bad JSON -> a zeroed report, never raises.
    """
    try:
        dst = _load(dst_json_path)
        src = _load(src_json_path)

        # Prefer dst for per-item translated/fallback accounting (it carries
        # both original + translated). Fall back to src for category counts /
        # total when dst is unavailable (e.g. a crashed run).
        by_category = {}
        unknown_types = set()

        # Category counts + total come from the extracted source when present,
        # else from the restored result.
        category_source = src if src else dst
        total = 0
        for item in category_source:
            if not isinstance(item, dict):
                continue
            total += 1
            item_type = item.get("type", "text")
            if item_type not in TYPE_TO_CATEGORY:
                unknown_types.add(item_type)
            cat = category_for(item_type)
            by_category[cat] = by_category.get(cat, 0) + 1

        # Translated vs fallback from the restored result.
        translated = 0
        fallback = 0
        counted = 0
        for item in dst:
            if not isinstance(item, dict):
                continue
            counted += 1
            translated_value = item.get("translated", "")
            original_value = item.get("original", "")
            if (isinstance(translated_value, str) and translated_value.strip()
                    and translated_value != original_value):
                translated += 1
            else:
                fallback += 1

        # If dst was missing, we can't tell translated from fallback — report
        # everything as fallback against the source total (conservative).
        if counted == 0 and total:
            fallback = total

        if unknown_types:
            app_logger.info(
                "Coverage: unmatched item type(s) -> 其它: "
                + ", ".join(sorted(unknown_types)))

        return {
            "total": total,
            "translated": translated,
            "fallback": fallback,
            "by_category": by_category,
        }
    except Exception as e:  # noqa: BLE001 — coverage must never raise
        app_logger.warning(f"Coverage summarize failed: {e}")
        return _zeroed()


def format_line(report):
    """One-line human summary, e.g.
    '120 segments — 正文 80, 表格 20, 批注 10; 0 未翻译'."""
    parts = ", ".join(f"{cat} {n}" for cat, n in report.get("by_category", {}).items() if n)
    total = report.get("total", 0)
    fallback = report.get("fallback", 0)
    body = f"{total} segments"
    if parts:
        body += f" — {parts}"
    body += f"; {fallback} 未翻译"
    return body
