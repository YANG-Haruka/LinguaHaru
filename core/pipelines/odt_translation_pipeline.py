# pipeline/odt_translation_pipeline.py
# OpenDocument Text (.odt - LibreOffice/WPS) translation. Paragraphs and
# headings in content.xml are translated as units (this covers body text,
# tables and frames, since their text lives in nested text:p elements).
# Simple paragraphs keep their structure; paragraphs with inline spans are
# replaced wholesale. All other zip members pass through untouched, with
# mimetype kept first and uncompressed as ODF requires.
import json
import os
import zipfile

from lxml import etree

from .epub_translation_pipeline import HLINK_RE
from .skip_pipeline import should_translate
from core.log_config import app_logger

TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
PARA_TAGS = {f"{{{TEXT_NS}}}p", f"{{{TEXT_NS}}}h"}
A_TAG = f"{{{TEXT_NS}}}a"

# User-supplied XML: disable entity resolution / DTD / network access (XXE)
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)


def _iter_paragraphs(root):
    for el in root.iter():
        if el.tag in PARA_TAGS:
            yield el


def _extract_paragraph(el):
    """Paragraph text with text:a hyperlinks wrapped in {{HLINK_n}} markers
    (same scheme as the EPUB pipeline); link text itself IS translated.

    Returns (text, links) where links holds each anchor's attributes for
    rebuilding at write-back. Other inline elements (text:span etc.) are
    flattened: the paragraph is replaced wholesale on write-back."""
    links = []

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
            else:
                out += render(child)
            out += child.tail or ""
        return out

    return render(el), links


def _apply_to_paragraph(el, translated, links=None):
    children = [c for c in el if isinstance(c.tag, str)]

    if links and "{{HLINK_" in translated:
        # Rebuild text:a anchors at their marker positions
        for child in children:
            el.remove(child)
        el.text = ""
        last_node = None
        pos = 0
        for match in HLINK_RE.finditer(translated):
            leading = translated[pos:match.start()]
            if last_node is None:
                el.text += leading
            else:
                last_node.tail = (last_node.tail or "") + leading
            anchor = etree.SubElement(el, A_TAG)
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
    for child in children:
        el.remove(child)
    el.text = translated


def extract_odt_content_to_json(file_path, temp_dir):
    with zipfile.ZipFile(file_path) as zf:
        root = etree.fromstring(zf.read("content.xml"), parser=_SAFE_PARSER)

    content_data = []
    count = 0
    for index, el in enumerate(_iter_paragraphs(root)):
        text, links = _extract_paragraph(el)
        text = text.strip()
        plain = HLINK_RE.sub(lambda m: m.group(2), text)
        if not plain.strip() or not should_translate(plain.strip()):
            continue
        count += 1
        item = {
            "count_src": count,
            "type": "text",
            "value": text,
            "para_index": index,
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

    app_logger.info(f"ODT: extracted {count} paragraphs")
    return json_path


def write_translated_content_to_odt(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}
    by_index = {item["para_index"]: item for item in original_data}

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}.odt")

    with zipfile.ZipFile(file_path) as zin:
        with zipfile.ZipFile(result_path, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "content.xml":
                    root = etree.fromstring(data, parser=_SAFE_PARSER)
                    # Materialize before mutating (live-iterator pitfall)
                    for index, el in enumerate(list(_iter_paragraphs(root))):
                        item = by_index.get(index)
                        if not item:
                            continue
                        translated = translations.get(item["count_src"])
                        if translated:
                            _apply_to_paragraph(el, translated, item.get("links"))
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                compress = (zipfile.ZIP_STORED if info.filename == "mimetype"
                            else zipfile.ZIP_DEFLATED)
                zout.writestr(info, data, compress_type=compress)

    app_logger.info(f"Translated ODT saved to: {result_path}")
    return result_path
