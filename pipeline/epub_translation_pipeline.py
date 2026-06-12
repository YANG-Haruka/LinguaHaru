# pipeline/epub_translation_pipeline.py
# EPUB translation: text is extracted per block element from the XHTML
# content documents, translated by the standard pipeline, then written back.
# All other zip members (css, images, fonts, opf, ncx) pass through
# untouched. The mimetype member is kept first and uncompressed as the EPUB
# spec requires.
import json
import os
import posixpath
import re
import zipfile

from lxml import etree

# Inline anchors are preserved through translation with marker placeholders
# (same scheme as the Word pipeline); the link text itself IS translated
HLINK_RE = re.compile(r"\{\{HLINK_(\d+)\}\}(.*?)\{\{/HLINK_\1\}\}", re.DOTALL)

from .skip_pipeline import should_translate
from config.log_config import app_logger

# Block-level elements whose text is translated as one unit
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th",
              "caption", "blockquote", "figcaption", "dt", "dd", "title"}

CONTENT_EXTENSIONS = (".xhtml", ".html", ".htm")


def _content_documents(zf):
    """Content document names in zip order (spine order approximation)."""
    return [n for n in zf.namelist()
            if posixpath.splitext(n)[1].lower() in CONTENT_EXTENSIONS]


def _parse_doc(data):
    # XHTML may or may not be well-formed XML; recover mode handles both
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    return etree.fromstring(data, parser=parser)


def _local_name(el):
    return etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""


def _iter_blocks(root):
    """Translatable block elements in document order.

    Nested blocks (e.g. a <p> inside an <li>) are yielded for the innermost
    block only, so no text is extracted twice."""
    for el in root.iter():
        if _local_name(el) not in BLOCK_TAGS:
            continue
        # Skip if a descendant is itself a block (translate the leaves)
        if any(_local_name(d) in BLOCK_TAGS for d in el.iterdescendants()):
            continue
        yield el


def extract_epub_content_to_json(file_path, temp_dir):
    content_data = []
    count = 0

    with zipfile.ZipFile(file_path) as zf:
        for doc_index, name in enumerate(_content_documents(zf)):
            root = _parse_doc(zf.read(name))
            if root is None:
                continue
            for block_index, el in enumerate(_iter_blocks(root)):
                text, links = _extract_block(el)
                text = text.strip()
                plain = HLINK_RE.sub(lambda m: m.group(2), text)
                if not plain.strip() or not should_translate(plain.strip()):
                    continue
                count += 1
                item = {
                    "count_src": count,
                    "type": "text",
                    "value": text,
                    "doc_index": doc_index,
                    "block_index": block_index,
                }
                if links:
                    item["links"] = links
                content_data.append(item)

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)

    app_logger.info(f"EPUB: extracted {count} text blocks from {file_path}")
    return json_path


def _extract_block(el):
    """Block text with inline anchors wrapped in {{HLINK_n}} markers.

    Returns (text, links) where links holds each anchor's attributes for
    rebuilding at write-back."""
    links = []

    def render(node):
        out = node.text or ""
        for child in node:
            if not isinstance(child.tag, str):
                out += child.tail or ""
                continue
            if _local_name(child) == "a":
                index = len(links)
                links.append({"attrib": dict(child.attrib)})
                inner = "".join(child.itertext())
                out += f"{{{{HLINK_{index}}}}}{inner}{{{{/HLINK_{index}}}}}"
            else:
                out += render(child)
            out += child.tail or ""
        return out

    return render(el), links


def _apply_to_block(el, translated, links=None):
    """Write the translated text into a block element.

    Anchors recorded at extraction are rebuilt at their marker positions.
    Other simple blocks keep their structure; remaining mixed-content blocks
    are replaced wholesale - losing inline tags beats keeping the source
    language."""
    children = [c for c in el if isinstance(c.tag, str)]

    if links and "{{HLINK_" in translated:
        for child in children:
            el.remove(child)
        ns_prefix = (el.tag.rsplit("}", 1)[0] + "}"
                     if isinstance(el.tag, str) and el.tag.startswith("{") else "")
        el.text = ""
        last_node = None
        pos = 0
        for match in HLINK_RE.finditer(translated):
            leading = translated[pos:match.start()]
            if last_node is None:
                el.text += leading
            else:
                last_node.tail = (last_node.tail or "") + leading
            anchor = etree.SubElement(el, ns_prefix + "a")
            link_index = int(match.group(1))
            if link_index < len(links):
                for key, value in links[link_index].get("attrib", {}).items():
                    anchor.set(key, value)
            anchor.text = match.group(2)
            last_node = anchor
            pos = match.end()
        trailing = translated[pos:]
        if last_node is None:
            el.text += trailing
        else:
            last_node.tail = (last_node.tail or "") + trailing
        return

    if not children:
        el.text = translated
        return
    single_inline = (len(children) == 1 and not (el.text or "").strip()
                     and not (children[0].tail or "").strip())
    if single_inline:
        # e.g. <p><em>whole text</em></p> - keep the inline wrapper
        inner = children[0]
        if not [c for c in inner if isinstance(c.tag, str)]:
            inner.text = translated
            inner.tail = None
            return
    for child in children:
        el.remove(child)
    el.text = translated


def write_translated_content_to_epub(file_path, original_json_path, translated_json_path,
                                     temp_dir, result_dir, src_lang=None, dst_lang=None):
    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}

    # Group items per content document
    items_by_doc = {}
    for item in original_data:
        items_by_doc.setdefault(item["doc_index"], {})[item["block_index"]] = item

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}.epub")

    with zipfile.ZipFile(file_path) as zin:
        content_names = {name: i for i, name in enumerate(_content_documents(zin))}
        with zipfile.ZipFile(result_path, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)

                doc_index = content_names.get(info.filename)
                doc_items = items_by_doc.get(doc_index) if doc_index is not None else None
                if doc_items:
                    root = _parse_doc(data)
                    # Materialize before mutating: _apply_to_block removes
                    # children, which would derail a live root.iter()
                    for block_index, el in enumerate(list(_iter_blocks(root))):
                        item = doc_items.get(block_index)
                        if not item:
                            continue
                        translated = translations.get(item["count_src"])
                        if translated:
                            _apply_to_block(el, translated, item.get("links"))
                    data = etree.tostring(root, xml_declaration=True, encoding="utf-8")

                # mimetype must stay uncompressed (and it is first in
                # infolist order, which zout preserves)
                compress = (zipfile.ZIP_STORED if info.filename == "mimetype"
                            else zipfile.ZIP_DEFLATED)
                zout.writestr(info, data, compress_type=compress)

    app_logger.info(f"Translated EPUB saved to: {result_path}")
    return result_path
