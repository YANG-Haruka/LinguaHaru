# pipeline/odt_translation_pipeline.py
# OpenDocument Text (.odt - LibreOffice/WPS) translation. Paragraphs and
# headings in content.xml are translated as units (this covers body text,
# tables and frames, since their text lives in nested text:p elements).
# Simple paragraphs keep their structure; paragraphs with inline spans are
# replaced wholesale. All other zip members pass through untouched, with
# mimetype kept first and uncompressed as ODF requires.
import copy
import json
import os
import re
import zipfile

from lxml import etree

from .epub_translation_pipeline import HLINK_RE, INLINE_RE
from .skip_pipeline import should_translate
from core.log_config import app_logger

TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
PARA_TAGS = {f"{{{TEXT_NS}}}p", f"{{{TEXT_NS}}}h"}
A_TAG = f"{{{TEXT_NS}}}a"
SPAN_TAG = f"{{{TEXT_NS}}}span"
SPAN_RE = re.compile(r"\{\{/?SPAN_\d+\}\}")

# Drawing/image namespaces. A <draw:frame> (image) anchored inside a text:p is
# preserved verbatim through a {{INLINE_n}} marker so it is never lost on the
# wholesale-replace write-back; its <svg:title>/<svg:desc> alt text is
# translated as a separate "odt_imagealt" item type.
_DRAW_NS = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
_SVG_NS = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
DRAW_FRAME_TAG = f"{{{_DRAW_NS}}}frame"
_SVG_TITLE_TAG = f"{{{_SVG_NS}}}title"
_SVG_DESC_TAG = f"{{{_SVG_NS}}}desc"
_IMAGE_ALT_TAGS = {_SVG_TITLE_TAG, _SVG_DESC_TAG}

# Combined scan used at write-back to rebuild a paragraph in one pass
_TOKEN_RE = re.compile(
    r"\{\{(HLINK|SPAN)_(\d+)\}\}(.*?)\{\{/\1_\2\}\}|\{\{INLINE_(\d+)\}\}", re.DOTALL)

# Paragraph-bearing members: content.xml (body) + styles.xml (headers/footers
# live in master-page <style:header>/<style:footer> as text:p elements).
PARA_MEMBERS = ("content.xml", "styles.xml")

# meta.xml metadata worth translating (title/subject/description/keywords);
# author/creator names, dates and generator strings are left untouched.
_DC_NS = "http://purl.org/dc/elements/1.1/"
_META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
_META_TAGS = {f"{{{_DC_NS}}}title", f"{{{_DC_NS}}}subject",
              f"{{{_DC_NS}}}description", f"{{{_META_NS}}}keyword"}

# User-supplied XML: disable entity resolution / DTD / network access (XXE)
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)


def _iter_paragraphs(root):
    for el in root.iter():
        if el.tag in PARA_TAGS:
            yield el


def _is_inline(child):
    """Kept verbatim behind {{INLINE_n}}: everything except links and styled
    spans with text - images, spaces/tabs, fields (page number, date: their
    text is generated), footnotes/annotations and text boxes (their own
    text:p are translated as separate items)."""
    if child.tag == A_TAG:
        return False
    return child.tag != SPAN_TAG or not "".join(child.itertext()).strip()


def _extract_paragraph(el):
    """Paragraph text with text:a hyperlinks wrapped in {{HLINK_n}} markers
    (same scheme as the EPUB pipeline); link text itself IS translated.
    Text-less inline drawing elements (draw:frame images, etc.) are replaced
    by a {{INLINE_n}} marker and serialized verbatim, so the wholesale-replace
    write-back never deletes an image.

    text:span is wrapped in {{SPAN_n}}..{{/SPAN_n}} so bold/italic stays on the
    translated words.

    Returns (text, links, inlines, spans): attributes of each anchor/span and
    the serialized inline elements, for rebuilding at write-back."""
    links = []
    inlines = []
    spans = []

    def render(node):
        out = node.text or ""
        for child in node:
            if not isinstance(child.tag, str):
                out += child.tail or ""
                continue
            if child.tag == A_TAG:
                index = len(links)
                links.append({"attrib": dict(child.attrib)})
                inner = "".join(child.itertext())
                out += f"{{{{HLINK_{index}}}}}{inner}{{{{/HLINK_{index}}}}}"
            elif _is_inline(child):
                # Image frame / text-less inline element: keep verbatim so it
                # survives the wholesale-replace rebuild (losing an image is
                # data loss). svg:title/svg:desc inside it are translated
                # separately via the odt_imagealt structural index.
                clone = copy.deepcopy(child)
                clone.tail = None
                index = len(inlines)
                inlines.append(etree.tostring(clone, encoding="unicode"))
                out += f"{{{{INLINE_{index}}}}}"
            else:  # text:span
                index = len(spans)
                spans.append({"attrib": dict(child.attrib)})
                out += f"{{{{SPAN_{index}}}}}{render(child)}{{{{/SPAN_{index}}}}}"
            out += child.tail or ""
        return out

    return render(el), links, inlines, spans


def _live_inlines(el):
    """Re-serialize the paragraph's inline marker elements from the LIVE tree,
    in the same order _extract_paragraph emitted {{INLINE_n}} markers. This
    picks up any svg:title/svg:desc alt text translated in place on the frame
    before the paragraph is rebuilt."""
    out = []

    def walk(node):
        for child in node:
            if not isinstance(child.tag, str):
                continue
            if child.tag == A_TAG:
                continue
            if _is_inline(child):
                clone = copy.deepcopy(child)
                clone.tail = None
                out.append(etree.tostring(clone, encoding="unicode"))
            else:
                walk(child)

    walk(el)
    return out


def _build(parent, text, links, spans, inlines, used_inlines):
    """Fill `parent` from marker text: {{HLINK_n}}/{{SPAN_n}} pairs become
    text:a / text:span (spans recursively), {{INLINE_n}} the verbatim element."""
    parent.text = ""
    last_node = None
    pos = 0

    def add(s):
        if last_node is None:
            parent.text += s
        else:
            last_node.tail = (last_node.tail or "") + s

    for match in _TOKEN_RE.finditer(text):
        add(text[pos:match.start()])
        pos = match.end()
        if match.group(1) is not None:  # {{HLINK_n}}..{{/HLINK_n}} / {{SPAN_n}}..
            is_link = match.group(1) == "HLINK"
            node = etree.SubElement(parent, A_TAG if is_link else SPAN_TAG)
            records = links if is_link else spans
            index = int(match.group(2))
            if records and index < len(records):
                for key, value in records[index].get("attrib", {}).items():
                    node.set(key, value)
            if is_link:
                node.text = match.group(3)
            else:
                _build(node, match.group(3), links, spans, inlines, used_inlines)
            last_node = node
        else:  # {{INLINE_n}}
            inline_index = int(match.group(4))
            if inlines and inline_index < len(inlines):
                node = etree.fromstring(inlines[inline_index], parser=_SAFE_PARSER)
                parent.append(node)
                last_node = node
                used_inlines.add(inline_index)
    add(text[pos:])


def _apply_to_paragraph(el, translated, links=None, inlines=None, spans=None):
    children = [c for c in el if isinstance(c.tag, str)]

    # Any recorded inline element forces the rebuild path even if the
    # translation dropped its marker: the fallback below re-appends it so an
    # image is never lost.
    has_markers = ((links and "{{HLINK_" in translated)
                   or (spans and "{{SPAN_" in translated) or bool(inlines))
    if has_markers:
        # Prefer LIVE inline elements over the extraction-time JSON strings:
        # any svg:title/svg:desc alt text translated in-place on this tree is
        # already on the live frame, whereas the stored strings are untouched
        # source. Live order matches extraction order (same render walk).
        if inlines:
            live = _live_inlines(el)
            if len(live) == len(inlines):
                inlines = live
        # Rebuild text:a anchors, spans and inline frames at their markers
        for child in children:
            el.remove(child)
        used_inlines = set()
        _build(el, translated, links, spans, inlines, used_inlines)
        # A dropped {{INLINE_n}} marker must not lose the element (an image,
        # typically): re-append any that the translation failed to carry
        for inline_index, raw in enumerate(inlines or []):
            if inline_index not in used_inlines:
                el.append(etree.fromstring(raw, parser=_SAFE_PARSER))
        return

    if not children:
        el.text = SPAN_RE.sub("", translated)
        return
    for child in children:
        el.remove(child)
    el.text = SPAN_RE.sub("", translated)


def _iter_image_alts(root):
    """svg:title / svg:desc elements (image alt text inside draw:frame), in
    document order. The running index over this is purely structural so it maps
    back at write-back regardless of which slots end up translated."""
    for el in root.iter():
        if el.tag in _IMAGE_ALT_TAGS:
            yield el


def _iter_meta(root):
    for el in root.iter():
        if el.tag in _META_TAGS and (el.text or "").strip():
            yield el


def extract_odt_content_to_json(file_path, temp_dir):
    content_data = []
    count = 0

    with zipfile.ZipFile(file_path) as zf:
        members = set(zf.namelist())

        # Paragraph text from content.xml (body) and styles.xml (headers/footers)
        for member in PARA_MEMBERS:
            if member not in members:
                continue
            root = etree.fromstring(zf.read(member), parser=_SAFE_PARSER)
            for index, el in enumerate(_iter_paragraphs(root)):
                text, links, inlines, spans = _extract_paragraph(el)
                text = text.strip()
                plain = HLINK_RE.sub(lambda m: m.group(2), text)
                plain = SPAN_RE.sub("", INLINE_RE.sub("", plain))
                if not plain.strip() or not should_translate(plain.strip()):
                    continue
                count += 1
                item = {
                    "count_src": count,
                    "type": "text",
                    "value": text,
                    "member": member,
                    "para_index": index,
                }
                if links:
                    item["links"] = links
                if inlines:
                    item["inlines"] = inlines
                if spans:
                    item["spans"] = spans
                content_data.append(item)

            # Image alt text (svg:title / svg:desc inside draw:frame)
            for alt_index, el in enumerate(_iter_image_alts(root)):
                alt = (el.text or "").strip()
                if not alt or not should_translate(alt):
                    continue
                count += 1
                content_data.append({
                    "count_src": count,
                    "type": "odt_imagealt",
                    "value": alt,
                    "member": member,
                    "alt_index": alt_index,
                })

        # Document metadata from meta.xml
        if "meta.xml" in members:
            meta_root = etree.fromstring(zf.read("meta.xml"), parser=_SAFE_PARSER)
            for meta_index, el in enumerate(_iter_meta(meta_root)):
                text = el.text.strip()
                if not should_translate(text):
                    continue
                count += 1
                content_data.append({
                    "count_src": count,
                    "type": "odt_meta",
                    "value": text,
                    "member": "meta.xml",
                    "meta_index": meta_index,
                })

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)

    app_logger.info(f"ODT: extracted {count} paragraphs")
    return json_path


def write_translated_content_to_odt(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}
    # Paragraph items grouped per member (older JSON without "member" defaults
    # to content.xml); metadata items grouped separately.
    para_by_member = {}
    imagealt_by_member = {}
    meta_items = {}
    for item in original_data:
        if item.get("type") == "odt_meta":
            meta_items[item["meta_index"]] = item
        elif item.get("type") == "odt_imagealt":
            member = item.get("member", "content.xml")
            imagealt_by_member.setdefault(member, {})[item["alt_index"]] = item
        else:
            member = item.get("member", "content.xml")
            para_by_member.setdefault(member, {})[item["para_index"]] = item

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}.odt")

    with zipfile.ZipFile(file_path) as zin:
        with zipfile.ZipFile(result_path, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                by_index = para_by_member.get(info.filename)
                alt_by_index = imagealt_by_member.get(info.filename)
                if info.filename in PARA_MEMBERS and (by_index or alt_by_index):
                    root = etree.fromstring(data, parser=_SAFE_PARSER)
                    # Image alt write-back first (independent of paragraph
                    # mutation, which removes/re-inserts the frame verbatim;
                    # doing alts first avoids depending on that ordering)
                    if alt_by_index:
                        for alt_index, el in enumerate(_iter_image_alts(root)):
                            item = alt_by_index.get(alt_index)
                            if item and translations.get(item["count_src"]):
                                el.text = translations[item["count_src"]]
                    # Materialize before mutating (live-iterator pitfall).
                    # Reversed: a paragraph nested in a footnote/text box/
                    # annotation is translated before its parent re-inserts
                    # that element from the live tree.
                    for index, el in reversed(list(enumerate(_iter_paragraphs(root)))):
                        item = (by_index or {}).get(index)
                        if not item:
                            continue
                        translated = translations.get(item["count_src"])
                        if translated:
                            _apply_to_paragraph(el, translated, item.get("links"),
                                                item.get("inlines"), item.get("spans"))
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                elif info.filename == "meta.xml" and meta_items:
                    root = etree.fromstring(data, parser=_SAFE_PARSER)
                    for meta_index, el in enumerate(_iter_meta(root)):
                        item = meta_items.get(meta_index)
                        if item and translations.get(item["count_src"]):
                            el.text = translations[item["count_src"]]
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                compress = (zipfile.ZIP_STORED if info.filename == "mimetype"
                            else zipfile.ZIP_DEFLATED)
                zout.writestr(info, data, compress_type=compress)

    app_logger.info(f"Translated ODT saved to: {result_path}")
    return result_path
