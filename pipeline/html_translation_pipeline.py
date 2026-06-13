# pipeline/html_translation_pipeline.py
# Standalone HTML translation, reusing the EPUB block logic: innermost block
# elements are translated as units; simple blocks keep inline wrappers,
# mixed-content blocks are replaced wholesale.
import json
import os

from lxml import html as lxml_html

from .epub_translation_pipeline import (
    _iter_blocks, _apply_to_block, _extract_block, _has_block_descendant,
    _insert_original_sibling, _plain_text, HLINK_RE, INLINE_RE)
from .skip_pipeline import should_translate
from .txt_translation_pipeline import read_file_with_encoding
from config.log_config import app_logger


def _parse_html(content):
    return lxml_html.fromstring(content)


def extract_html_content_to_json(file_path, temp_dir):
    content, encoding = read_file_with_encoding(file_path)
    root = _parse_html(content)

    content_data = []
    count = 0
    for block_index, el in enumerate(_iter_blocks(root)):
        head = _has_block_descendant(el)
        if head:
            # Mixed container (e.g. <li>head<ul>...</ul></li>): only its
            # direct head text is translated; nested blocks are own items
            text, links, inlines = (el.text or ""), None, None
        else:
            text, links, inlines = _extract_block(el)
        text = text.strip()
        plain = HLINK_RE.sub(lambda m: m.group(2), text)
        plain = INLINE_RE.sub("", plain)
        if not plain.strip() or not should_translate(plain.strip()):
            continue
        count += 1
        item = {
            "count_src": count,
            "type": "text",
            "value": text,
            "block_index": block_index,
        }
        if head:
            item["head"] = True
        if links:
            item["links"] = links
        if inlines:
            item["inlines"] = inlines
        content_data.append(item)

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)

    app_logger.info(f"HTML: extracted {count} text blocks")
    return json_path


def write_translated_content_to_html(file_path, original_json_path, translated_json_path,
                                     temp_dir, result_dir, src_lang=None, dst_lang=None,
                                     bilingual_mode=False):
    content, encoding = read_file_with_encoding(file_path)
    root = _parse_html(content)

    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}
    by_block = {item["block_index"]: item for item in original_data}

    # Materialize before mutating (live-iterator pitfall)
    for block_index, el in enumerate(list(_iter_blocks(root))):
        item = by_block.get(block_index)
        if not item:
            continue
        translated = translations.get(item["count_src"])
        if translated:
            original_plain = _plain_text(item["value"])
            if item.get("head"):
                el.text = translated
            else:
                _apply_to_block(el, translated, item.get("links"), item.get("inlines"))
            if (bilingual_mode and original_plain
                    and translated.strip() != original_plain.strip()):
                _insert_original_sibling(el, original_plain)

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    extension = os.path.splitext(file_path)[1].lower() or ".html"
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}{extension}")

    output = lxml_html.tostring(root, encoding="unicode", doctype="<!DOCTYPE html>")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(output)

    app_logger.info(f"Translated HTML saved to: {result_path}")
    return result_path
