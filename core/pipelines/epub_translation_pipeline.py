# pipeline/epub_translation_pipeline.py
# EPUB translation: text is extracted per block element from the XHTML
# content documents, translated by the standard pipeline, then written back.
# All other zip members (css, images, fonts, opf, ncx) pass through
# untouched. The mimetype member is kept first and uncompressed as the EPUB
# spec requires.
import copy
import json
import os
import posixpath
import re
import zipfile

from lxml import etree

# Inline anchors are preserved through translation with marker placeholders
# (same scheme as the Word pipeline); the link text itself IS translated
HLINK_RE = re.compile(r"\{\{HLINK_(\d+)\}\}(.*?)\{\{/HLINK_\1\}\}", re.DOTALL)
# Text-less inline elements (img, br, ...) are preserved the same way: a
# self-closing {{INLINE_n}} marker stands in for the element, which is
# re-inserted verbatim at write-back. Losing an image would be data loss.
INLINE_RE = re.compile(r"\{\{INLINE_(\d+)\}\}")
# Combined scan used at write-back to rebuild a block in one pass
_TOKEN_RE = re.compile(
    r"\{\{HLINK_(\d+)\}\}(.*?)\{\{/HLINK_\1\}\}|\{\{INLINE_(\d+)\}\}", re.DOTALL)

from .skip_pipeline import should_translate
from core.log_config import app_logger

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


def _plain_text(text):
    """Strip HLINK/INLINE markers, leaving the original visible text."""
    plain = HLINK_RE.sub(lambda m: m.group(2), text)
    plain = INLINE_RE.sub("", plain)
    return plain.strip()


def _local_name(el):
    return etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""


def _has_block_descendant(el):
    return any(_local_name(d) in BLOCK_TAGS for d in el.iterdescendants())


def _iter_blocks(root):
    """Translatable block elements in document order.

    Nested blocks (e.g. a <p> inside an <li>) are yielded for the innermost
    block only, so no text is extracted twice. A block that CONTAINS nested
    blocks but also has direct head text (e.g. <li>head<ul>...</ul></li>)
    is yielded too, so the head text is not silently left untranslated;
    such blocks are handled head-text-only (see "head" items)."""
    for el in root.iter():
        if _local_name(el) not in BLOCK_TAGS:
            continue
        if _has_block_descendant(el):
            if (el.text or "").strip():
                yield el
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
                head = _has_block_descendant(el)
                if head:
                    # Mixed container: only its direct head text is
                    # translated; nested blocks are separate items
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
                    "doc_index": doc_index,
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

    app_logger.info(f"EPUB: extracted {count} text blocks from {file_path}")
    return json_path


def _extract_block(el):
    """Block text with inline anchors wrapped in {{HLINK_n}} markers and
    text-less inline elements (img, br, ...) replaced by {{INLINE_n}}.

    Returns (text, links, inlines) where links holds each anchor's
    attributes and inlines the serialized elements, both for rebuilding at
    write-back."""
    links = []
    inlines = []

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
            elif not "".join(child.itertext()).strip():
                # No text content (img, br, hr, empty span): keep verbatim
                clone = copy.deepcopy(child)
                clone.tail = None
                index = len(inlines)
                inlines.append(etree.tostring(clone, encoding="unicode"))
                out += f"{{{{INLINE_{index}}}}}"
            else:
                out += render(child)
            out += child.tail or ""
        return out

    return render(el), links, inlines


def _apply_to_block(el, translated, links=None, inlines=None):
    """Write the translated text into a block element.

    Anchors and text-less inline elements recorded at extraction are rebuilt
    at their marker positions. Other simple blocks keep their structure;
    remaining mixed-content blocks are replaced wholesale - losing inline
    tags beats keeping the source language."""
    children = [c for c in el if isinstance(c.tag, str)]

    # Any recorded inline element forces the rebuild path even if the
    # translation dropped its marker: the fallback below re-appends it
    has_markers = (links and "{{HLINK_" in translated) or bool(inlines)
    if has_markers:
        for child in children:
            el.remove(child)
        ns_prefix = (el.tag.rsplit("}", 1)[0] + "}"
                     if isinstance(el.tag, str) and el.tag.startswith("{") else "")
        el.text = ""
        last_node = None
        pos = 0
        used_inlines = set()
        for match in _TOKEN_RE.finditer(translated):
            leading = translated[pos:match.start()]
            if last_node is None:
                el.text += leading
            else:
                last_node.tail = (last_node.tail or "") + leading
            if match.group(1) is not None:  # {{HLINK_n}}text{{/HLINK_n}}
                anchor = etree.SubElement(el, ns_prefix + "a")
                link_index = int(match.group(1))
                if links and link_index < len(links):
                    for key, value in links[link_index].get("attrib", {}).items():
                        anchor.set(key, value)
                anchor.text = match.group(2)
                last_node = anchor
            else:  # {{INLINE_n}}
                inline_index = int(match.group(3))
                if inlines and inline_index < len(inlines):
                    node = etree.fromstring(inlines[inline_index])
                    el.append(node)
                    last_node = node
                    used_inlines.add(inline_index)
            pos = match.end()
        trailing = translated[pos:]
        if last_node is None:
            el.text += trailing
        else:
            last_node.tail = (last_node.tail or "") + trailing
        # A dropped {{INLINE_n}} marker must not lose the element (an image,
        # typically): re-append any that the translation failed to carry
        for inline_index, raw in enumerate(inlines or []):
            if inline_index not in used_inlines:
                el.append(etree.fromstring(raw))
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


def _insert_original_sibling(el, original_text):
    """Insert a sibling block right after ``el`` carrying the original text.

    Used by bilingual mode: the translated block stays in place and the
    untouched source text follows it in a fresh element of the same tag (a
    plain following <p>/<li>/... with only text). Returns the new element so
    callers can keep iterating past it. The new block has no attributes so it
    does not inherit ids/anchors that must stay unique."""
    parent = el.getparent()
    if parent is None:
        return None
    sibling = etree.SubElement(parent, el.tag)
    # Place it immediately after el (SubElement appends to the end)
    parent.remove(sibling)
    parent.insert(parent.index(el) + 1, sibling)
    sibling.text = original_text
    sibling.tail = el.tail
    el.tail = "\n"
    return sibling


def write_translated_content_to_epub(file_path, original_json_path, translated_json_path,
                                     temp_dir, result_dir, src_lang=None, dst_lang=None,
                                     bilingual_mode=False):
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
                            # Original visible text (markers stripped) for the
                            # bilingual sibling, captured before we mutate el
                            original_plain = _plain_text(item["value"])
                            if item.get("head"):
                                # Head text of a mixed container: replace
                                # only the direct text, keep nested blocks
                                el.text = translated
                            else:
                                _apply_to_block(el, translated, item.get("links"),
                                                item.get("inlines"))
                            if (bilingual_mode and original_plain
                                    and translated.strip() != original_plain.strip()):
                                _insert_original_sibling(el, original_plain)
                    data = etree.tostring(root, xml_declaration=True, encoding="utf-8")

                # mimetype must stay uncompressed (and it is first in
                # infolist order, which zout preserves)
                compress = (zipfile.ZIP_STORED if info.filename == "mimetype"
                            else zipfile.ZIP_DEFLATED)
                zout.writestr(info, data, compress_type=compress)

    app_logger.info(f"Translated EPUB saved to: {result_path}")
    return result_path
