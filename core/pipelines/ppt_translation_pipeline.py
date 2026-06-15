# pipeline/ppt_translation_pipeline.py
import json
import os
from lxml import etree

# User-supplied XML: disable entity resolution / DTD / network access (XXE)
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
from zipfile import ZipFile
from .skip_pipeline import should_translate
from core.log_config import app_logger
from typing import Dict, List
import re

def extract_ppt_content_to_json(file_path, temp_dir):
    """
    Extract text content from PowerPoint, processing each paragraph/cell as a single unit.
    """
    try:
        with ZipFile(file_path, 'r') as pptx:
            slides = [name for name in pptx.namelist() 
                     if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
            slides.sort()  # Ensure proper ordering
    except Exception as e:
        app_logger.error(f"Failed to read PPTX file: {e}")
        raise

    content_data = []
    count = 0
    
    # Complete namespace definitions including SmartArt
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
        'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
        'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram'
    }

    try:
        with ZipFile(file_path, 'r') as pptx:
            for slide_index, slide_path in enumerate(slides, start=1):
                try:
                    slide_xml = pptx.read(slide_path)
                    slide_tree = etree.fromstring(slide_xml, parser=_SAFE_PARSER)
                    
                    # Extract text from text boxes (by paragraph)
                    count = _extract_text_boxes(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from tables (by cell paragraph)
                    count = _extract_tables(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from shapes (by shape)
                    count = _extract_shapes(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from notes (by paragraph)
                    notes_path = slide_path.replace('slides/slide', 'notesSlides/notesSlide')
                    if notes_path in pptx.namelist():
                        count = _extract_notes(pptx, notes_path, slide_index, namespaces, content_data, count)
                        
                except Exception as e:
                    app_logger.error(f"Failed to process slide {slide_index}: {e}")
                    continue

            # Extract text from SmartArt diagrams
            count = _extract_smartart(pptx, namespaces, content_data, count)

            # Extract text from chart parts (titles, series/category labels live
            # in ppt/charts/chartN.xml, not in the slide XML)
            count = _extract_chart_parts(pptx, namespaces, content_data, count)

            # Extract static text from slide masters/layouts (footers, logos,
            # custom text boxes). Placeholder PROMPT text ("Click to edit...")
            # is skipped so it is never translated.
            count = _extract_master_layout_parts(pptx, namespaces, content_data, count)

            # Extract shape/picture ALT TEXT (cNvPr descr/title accessibility
            # attributes) from slides, masters and layouts.
            count = _extract_alttext_parts(pptx, namespaces, content_data, count)

            # Extract text from PPT comments (legacy ppt/comments/*.xml and
            # modern threaded comments ppt/comments/modernComment_*.xml).
            count = _extract_comment_parts(pptx, namespaces, content_data, count)

            # Extract static text from notes/handout masters.
            count = _extract_notes_handout_masters(pptx, namespaces, content_data, count)

    except Exception as e:
        app_logger.error(f"Failed to process PPTX content: {e}")
        raise

    # Save content to JSON with better error handling
    try:
        filename = os.path.splitext(os.path.basename(file_path))[0]
        temp_folder = os.path.join(temp_dir, filename)
        os.makedirs(temp_folder, exist_ok=True)
        json_path = os.path.join(temp_folder, "src.json")
        
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(content_data, json_file, ensure_ascii=False, indent=4)
        
        app_logger.info(f"Extracted {len(content_data)} text elements from PowerPoint")
        return json_path
        
    except Exception as e:
        app_logger.error(f"Failed to save JSON file: {e}")
        raise

def _extract_chart_parts(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract translatable text from ppt/charts/chartN.xml parts.

    Covers rich-text runs (a:t - titles, axis titles, data labels) and string
    cache values (c:strCache/c:pt/c:v - series names, category labels).
    Numeric caches are left untouched."""
    chart_files = sorted(name for name in pptx.namelist()
                         if re.match(r"ppt/charts/chart\d+\.xml$", name))
    for chart_file in chart_files:
        try:
            tree = etree.fromstring(pptx.read(chart_file), parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            app_logger.warning(f"Failed to parse {chart_file}: {e}")
            continue

        for kind, nodes in (
            ("a_t", tree.xpath('.//a:t', namespaces=namespaces)),
            ("str_v", tree.xpath('.//c:strCache/c:pt/c:v', namespaces=namespaces)),
        ):
            for node_index, node in enumerate(nodes):
                text = (node.text or "").strip()
                if not text or not should_translate(text):
                    continue
                count += 1
                content_data.append({
                    "count_src": count,
                    "type": "chart_part",
                    "value": node.text,
                    "chart_file": chart_file,
                    "node_kind": kind,
                    "node_index": node_index,
                })
    return count


def _apply_chart_parts_translations(pptx, chart_items: List[Dict], translations: Dict,
                                    temp_folder: str, namespaces: Dict):
    """Write translated chart texts into chart XML parts under temp_folder."""
    items_by_file = {}
    for item in chart_items:
        items_by_file.setdefault(item["chart_file"], []).append(item)

    for chart_file, items in items_by_file.items():
        try:
            tree = etree.fromstring(pptx.read(chart_file), parser=_SAFE_PARSER)
        except (KeyError, etree.XMLSyntaxError) as e:
            app_logger.warning(f"Failed to load {chart_file} for write-back: {e}")
            continue

        nodes_by_kind = {
            "a_t": tree.xpath('.//a:t', namespaces=namespaces),
            "str_v": tree.xpath('.//c:strCache/c:pt/c:v', namespaces=namespaces),
        }
        for item in items:
            translated = translations.get(str(item["count_src"]))
            if not translated:
                continue
            nodes = nodes_by_kind.get(item["node_kind"], [])
            if item["node_index"] < len(nodes):
                nodes[item["node_index"]].text = translated.replace("␊", "\n").replace("␍", "\r")

        out_path = os.path.join(temp_folder, chart_file)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(etree.tostring(tree, xml_declaration=True,
                                   encoding="UTF-8", standalone="yes"))
    app_logger.info(f"Applied chart translations to {len(items_by_file)} chart part(s)")


def _extract_smartart(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from SmartArt diagrams."""
    # Find all diagram drawing files
    diagram_drawings = [name for name in pptx.namelist() 
                       if name.startswith('ppt/diagrams/drawing') and name.endswith('.xml')]
    diagram_drawings.sort()
    
    app_logger.info(f"Found {len(diagram_drawings)} SmartArt diagram files")
    
    for drawing_path in diagram_drawings:
        try:
            # Extract diagram number from path (e.g., drawing1.xml -> 1)
            diagram_match = re.search(r'drawing(\d+)\.xml', drawing_path)
            if not diagram_match:
                continue
            
            diagram_index = int(diagram_match.group(1))
            
            drawing_xml = pptx.read(drawing_path)
            drawing_tree = etree.fromstring(drawing_xml, parser=_SAFE_PARSER)
            
            # Find all shapes with text content in SmartArt
            shapes = drawing_tree.xpath('.//dsp:sp[.//dsp:txBody]', namespaces=namespaces)
            
            for shape_index, shape in enumerate(shapes):
                model_id = shape.get('modelId', '')
                
                # Get text content from txBody elements
                tx_bodies = shape.xpath('.//dsp:txBody', namespaces=namespaces)
                
                for tx_body_index, tx_body in enumerate(tx_bodies):
                    paragraphs = tx_body.xpath('.//a:p', namespaces=namespaces)
                    
                    for p_index, paragraph in enumerate(paragraphs):
                        text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                        
                        if not text_runs:
                            continue
                        
                        # Process runs and preserve exact spacing
                        run_info = _process_text_runs(text_runs, namespaces)
                        
                        if not run_info['merged_text'].strip():
                            continue
                        
                        # Only process if there's meaningful text content and it should be translated
                        if should_translate(run_info['merged_text']):
                            count += 1
                            content_data.append({
                                "count_src": count,
                                "diagram_index": diagram_index,
                                "shape_index": shape_index,
                                "tx_body_index": tx_body_index,
                                "paragraph_index": p_index,
                                "model_id": model_id,
                                "type": "smartart",
                                "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                                "run_texts": run_info['run_texts'],
                                "run_styles": run_info['run_styles'],
                                "run_lengths": run_info['run_lengths'],
                                "drawing_path": drawing_path,
                                "original_text": run_info['merged_text'],  # Store original text for data.xml matching
                                "xpath": f".//dsp:sp[{shape_index + 1}]//dsp:txBody[{tx_body_index + 1}]//a:p[{p_index + 1}]"
                            })
                        
        except Exception as e:
            app_logger.error(f"Failed to extract SmartArt from {drawing_path}: {e}")
            continue
    
    return count

def _extract_text_boxes(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from text boxes, merging runs within the same paragraph."""
    text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
    
    for text_box_index, text_box in enumerate(text_boxes, start=1):
        paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
        
        for p_index, paragraph in enumerate(paragraphs):
            # Collect all text runs in this paragraph
            text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
            
            if not text_runs:
                continue
                
            # Process runs and preserve exact spacing
            run_info = _process_text_runs(text_runs, namespaces)
            
            if not run_info['merged_text'].strip():
                continue
                
            # Only process if there's meaningful text content and it should be translated
            if should_translate(run_info['merged_text']):
                count += 1
                content_data.append({
                    "count_src": count,
                    "slide_index": slide_index,
                    "text_box_index": text_box_index,
                    "paragraph_index": p_index,
                    "type": "text_paragraph",
                    "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                    "run_texts": run_info['run_texts'],
                    "run_styles": run_info['run_styles'],
                    "run_lengths": run_info['run_lengths'],
                    "xpath": f".//p:txBody[{text_box_index}]//a:p[{p_index + 1}]"
                })
    
    return count

def _extract_tables(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from tables, processing each paragraph in each cell separately."""
    tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
    
    for table_index, table in enumerate(tables, start=1):
        rows = table.xpath('.//a:tr', namespaces=namespaces)
        
        for row_index, row in enumerate(rows):
            cells = row.xpath('.//a:tc', namespaces=namespaces)
            
            for cell_index, cell in enumerate(cells):
                # Get all paragraphs in this cell
                paragraphs = cell.xpath('.//a:p', namespaces=namespaces)
                
                for p_index, paragraph in enumerate(paragraphs):
                    # Collect all text runs in this paragraph
                    text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                    
                    if not text_runs:
                        continue
                        
                    # Process runs and preserve exact spacing
                    run_info = _process_text_runs(text_runs, namespaces)
                    
                    if not run_info['merged_text'].strip():
                        continue
                    
                    # Only process if there's meaningful text content and it should be translated
                    if should_translate(run_info['merged_text']):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "table_index": table_index,
                            "row_index": row_index,
                            "cell_index": cell_index,
                            "paragraph_index": p_index,
                            "type": "table_cell_paragraph",
                            "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                            "run_texts": run_info['run_texts'],
                            "run_styles": run_info['run_styles'],
                            "run_lengths": run_info['run_lengths'],
                            "xpath": f".//a:tbl[{table_index}]//a:tr[{row_index + 1}]//a:tc[{cell_index + 1}]//a:p[{p_index + 1}]"
                        })
    
    return count

def _extract_shapes(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from shapes (excluding text boxes), merging runs within the same shape."""
    shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
    
    # Count non-textbox shapes to maintain proper indexing
    non_textbox_shapes = []
    for shape in shapes:
        if not shape.xpath('.//p:txBody', namespaces=namespaces):
            non_textbox_shapes.append(shape)
    
    for shape_index, shape in enumerate(non_textbox_shapes, start=1):
        # Collect all text runs in this shape
        text_runs = shape.xpath('.//a:r', namespaces=namespaces)
        
        if not text_runs:
            continue
            
        # Process runs and preserve exact spacing
        run_info = _process_text_runs(text_runs, namespaces)
        
        if not run_info['merged_text'].strip():
            continue
        
        # Only process if there's meaningful text content and it should be translated
        if should_translate(run_info['merged_text']):
            count += 1
            content_data.append({
                "count_src": count,
                "slide_index": slide_index,
                "shape_index": shape_index,
                "type": "shape",
                "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                "run_texts": run_info['run_texts'],
                "run_styles": run_info['run_styles'],
                "run_lengths": run_info['run_lengths'],
                "xpath": f".//p:sp[not(.//p:txBody)][{shape_index}]"
            })
    
    return count

_MASTER_LAYOUT_RE = re.compile(
    r"ppt/slide(?:Masters/slideMaster|Layouts/slideLayout)\d+\.xml$")


def _ml_translatable_paragraphs(tree, namespaces: Dict):
    """Paragraphs of NON-placeholder shapes in a master/layout, in order.

    Placeholder shapes (those with p:ph) hold editing prompts like "Click to
    edit Master title style" that are never shown, so they are skipped."""
    out = []
    for sp in tree.xpath('.//p:sp', namespaces=namespaces):
        if sp.xpath('.//p:nvSpPr//p:ph', namespaces=namespaces):
            continue
        for paragraph in sp.xpath('.//p:txBody//a:p', namespaces=namespaces):
            out.append((paragraph, paragraph.xpath('.//a:r', namespaces=namespaces)))
    return out


def _extract_master_layout_parts(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract static (non-placeholder) text from slide masters and layouts."""
    parts = sorted(n for n in pptx.namelist() if _MASTER_LAYOUT_RE.match(n))
    for part in parts:
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            app_logger.warning(f"Failed to parse {part}: {e}")
            continue
        for para_index, (paragraph, runs) in enumerate(_ml_translatable_paragraphs(tree, namespaces)):
            if not runs:
                continue
            run_info = _process_text_runs(runs, namespaces)
            if not run_info['merged_text'].strip() or not should_translate(run_info['merged_text']):
                continue
            count += 1
            content_data.append({
                "count_src": count,
                "type": "master_layout",
                "part_path": part,
                "para_index": para_index,
                "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                "run_texts": run_info['run_texts'],
                "run_styles": run_info['run_styles'],
                "run_lengths": run_info['run_lengths'],
            })
    app_logger.info(f"Extracted {sum(1 for i in content_data if i.get('type') == 'master_layout')} "
                    f"master/layout text items")
    return count


def _apply_master_layout_translations(pptx, ml_items: List[Dict], translations: Dict,
                                      temp_folder: str, namespaces: Dict,
                                      alttext_items: List[Dict] = None) -> List[str]:
    """Apply translations to master/layout parts; return modified part paths.

    alttext_items (ppt_alttext on master/layout parts) are applied on the same
    tree so they share the write pass and do not clobber the text translations.
    """
    by_part = {}
    for item in ml_items:
        by_part.setdefault(item["part_path"], []).append(item)
    alt_by_part = {}
    for item in (alttext_items or []):
        alt_by_part.setdefault(item["part_path"], []).append(item)
        by_part.setdefault(item["part_path"], [])  # ensure part is processed

    modified = []
    for part, items in by_part.items():
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
            paragraphs = _ml_translatable_paragraphs(tree, namespaces)
            for item in items:
                translated = translations.get(str(item["count_src"]))
                if not translated:
                    continue
                translated = translated.replace("␊", "\n").replace("␍", "\r")
                idx = item["para_index"]
                if 0 <= idx < len(paragraphs):
                    paragraph, _ = paragraphs[idx]
                    _distribute_text_to_runs(paragraph, translated, item, namespaces)
            if alt_by_part.get(part):
                _apply_alttext_to_tree(tree, alt_by_part[part], translations, namespaces)
            out_path = os.path.join(temp_folder, part)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
            modified.append(part)
        except Exception as e:
            app_logger.error(f"Failed to apply master/layout translations to {part}: {e}")
    return modified


# --- ADDITIVE: alt text, comments, notes/handout masters --------------------

_ALTTEXT_PART_RE = re.compile(
    r"ppt/(?:slides/slide|slideMasters/slideMaster|slideLayouts/slideLayout)\d+\.xml$")

# Comment author/timestamp attributes are not translatable; only the body text.
_LEGACY_COMMENTS_RE = re.compile(r"ppt/comments/comment\d+\.xml$")
_MODERN_COMMENTS_RE = re.compile(r"ppt/comments/modernComment_.*\.xml$")

_NOTES_HANDOUT_MASTER_RE = re.compile(
    r"ppt/(?:notesMasters/notesMaster|handoutMasters/handoutMaster)\d+\.xml$")

# Modern threaded comments namespace (text lives in p188:txBody//a:t).
_PC_NS = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"


def _alttext_cnvpr_nodes(tree, namespaces: Dict):
    """All p:cNvPr nodes in a part, in document order (stable structural index)."""
    return tree.xpath('.//p:cNvPr', namespaces=namespaces)


def _extract_alttext_parts(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract translatable alt text (cNvPr descr/title attributes)."""
    parts = sorted(n for n in pptx.namelist() if _ALTTEXT_PART_RE.match(n))
    for part in parts:
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            app_logger.warning(f"Failed to parse {part}: {e}")
            continue
        for node_index, cnvpr in enumerate(_alttext_cnvpr_nodes(tree, namespaces)):
            for attr in ("descr", "title"):
                text = cnvpr.get(attr)
                if not text or not text.strip() or not should_translate(text):
                    continue
                count += 1
                content_data.append({
                    "count_src": count,
                    "type": "ppt_alttext",
                    "part_path": part,
                    "node_index": node_index,
                    "attr": attr,
                    "value": text.replace("\n", "␊").replace("\r", "␍"),
                })
    return count


def _apply_alttext_to_tree(tree, items: List[Dict], translations: Dict, namespaces: Dict):
    """Write translated alt text into cNvPr descr/title attributes of a parsed tree.

    Applied on the SAME tree already used for text translations so the two
    passes do not clobber each other when sharing a part (e.g. slide XML)."""
    nodes = _alttext_cnvpr_nodes(tree, namespaces)
    for item in items:
        translated = translations.get(str(item["count_src"]))
        if not translated:
            continue
        translated = translated.replace("␊", "\n").replace("␍", "\r")
        idx = item["node_index"]
        if 0 <= idx < len(nodes):
            nodes[idx].set(item["attr"], translated)


def _comment_text_nodes(tree, part: str, namespaces: Dict):
    """Translatable text nodes for a comment part, in document order.

    Legacy ppt/comments/commentN.xml: text is the element text of p:text.
    Modern threaded comments: text lives in a:t runs inside the body."""
    if _MODERN_COMMENTS_RE.match(part):
        ns = dict(namespaces)
        ns["pc"] = _PC_NS
        # Modern comment text is held in a:t runs (txBody); fall back to any a:t.
        return tree.xpath('.//a:t', namespaces=ns)
    # Legacy: p:text element holds the comment body.
    return tree.xpath('.//p:text', namespaces=namespaces)


def _extract_comment_parts(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract translatable text from PPT comment parts (legacy + modern)."""
    parts = sorted(n for n in pptx.namelist()
                   if _LEGACY_COMMENTS_RE.match(n) or _MODERN_COMMENTS_RE.match(n))
    for part in parts:
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            app_logger.warning(f"Failed to parse {part}: {e}")
            continue
        for node_index, node in enumerate(_comment_text_nodes(tree, part, namespaces)):
            text = (node.text or "").strip()
            if not text or not should_translate(text):
                continue
            count += 1
            content_data.append({
                "count_src": count,
                "type": "ppt_comment",
                "part_path": part,
                "node_index": node_index,
                "value": (node.text or "").replace("\n", "␊").replace("\r", "␍"),
            })
    return count


def _apply_comment_translations(pptx, items: List[Dict], translations: Dict,
                                temp_folder: str, namespaces: Dict) -> List[str]:
    """Write translated comment text back into comment parts."""
    by_part = {}
    for item in items:
        by_part.setdefault(item["part_path"], []).append(item)

    modified = []
    for part, part_items in by_part.items():
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
            nodes = _comment_text_nodes(tree, part, namespaces)
            for item in part_items:
                translated = translations.get(str(item["count_src"]))
                if not translated:
                    continue
                translated = translated.replace("␊", "\n").replace("␍", "\r")
                idx = item["node_index"]
                if 0 <= idx < len(nodes):
                    nodes[idx].text = translated
            out_path = os.path.join(temp_folder, part)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(etree.tostring(tree, xml_declaration=True,
                                       encoding="UTF-8", standalone="yes"))
            modified.append(part)
        except Exception as e:
            app_logger.error(f"Failed to apply comment translations to {part}: {e}")
    return modified


def _extract_notes_handout_masters(pptx, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract static (non-placeholder) text from notes/handout masters."""
    parts = sorted(n for n in pptx.namelist() if _NOTES_HANDOUT_MASTER_RE.match(n))
    for part in parts:
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            app_logger.warning(f"Failed to parse {part}: {e}")
            continue
        is_handout = "handoutMaster" in part
        item_type = "ppt_handoutmaster" if is_handout else "ppt_notesmaster"
        for para_index, (paragraph, runs) in enumerate(
                _ml_translatable_paragraphs(tree, namespaces)):
            if not runs:
                continue
            run_info = _process_text_runs(runs, namespaces)
            if not run_info['merged_text'].strip() or not should_translate(run_info['merged_text']):
                continue
            count += 1
            content_data.append({
                "count_src": count,
                "type": item_type,
                "part_path": part,
                "para_index": para_index,
                "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                "run_texts": run_info['run_texts'],
                "run_styles": run_info['run_styles'],
                "run_lengths": run_info['run_lengths'],
            })
    return count


def _apply_notes_handout_master_translations(pptx, items: List[Dict], translations: Dict,
                                             temp_folder: str, namespaces: Dict) -> List[str]:
    """Write translations back into notes/handout master parts."""
    by_part = {}
    for item in items:
        by_part.setdefault(item["part_path"], []).append(item)

    modified = []
    for part, part_items in by_part.items():
        try:
            tree = etree.fromstring(pptx.read(part), parser=_SAFE_PARSER)
            paragraphs = _ml_translatable_paragraphs(tree, namespaces)
            for item in part_items:
                translated = translations.get(str(item["count_src"]))
                if not translated:
                    continue
                translated = translated.replace("␊", "\n").replace("␍", "\r")
                idx = item["para_index"]
                if 0 <= idx < len(paragraphs):
                    paragraph, _ = paragraphs[idx]
                    _distribute_text_to_runs(paragraph, translated, item, namespaces)
            out_path = os.path.join(temp_folder, part)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(etree.tostring(tree, xml_declaration=True,
                                       encoding="UTF-8", standalone="yes"))
            modified.append(part)
        except Exception as e:
            app_logger.error(f"Failed to apply notes/handout master translations to {part}: {e}")
    return modified


def _extract_notes(pptx, notes_path: str, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from slide notes, merging runs within the same paragraph."""
    try:
        notes_xml = pptx.read(notes_path)
        notes_tree = etree.fromstring(notes_xml, parser=_SAFE_PARSER)
        
        # Get all paragraphs in notes
        paragraphs = notes_tree.xpath('.//a:p', namespaces=namespaces)
        
        for p_index, paragraph in enumerate(paragraphs):
            text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
            
            if not text_runs:
                continue
                
            # Process runs and preserve exact spacing
            run_info = _process_text_runs(text_runs, namespaces)
            
            if not run_info['merged_text'].strip():
                continue
            
            # Only process if there's meaningful text content and it should be translated
            if should_translate(run_info['merged_text']):
                count += 1
                content_data.append({
                    "count_src": count,
                    "slide_index": slide_index,
                    "paragraph_index": p_index,
                    "type": "notes",
                    "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                    "run_texts": run_info['run_texts'],
                    "run_styles": run_info['run_styles'],
                    "run_lengths": run_info['run_lengths'],
                    "xpath": f".//a:p[{p_index + 1}]"
                })
                
    except Exception as e:
        app_logger.error(f"Failed to extract notes for slide {slide_index}: {e}")
    
    return count

def _process_text_runs(text_runs, namespaces: Dict) -> Dict:
    """Process text runs and preserve exact spacing and formatting."""
    merged_text = ""
    run_texts = []
    run_styles = []
    run_lengths = []
    
    for text_run in text_runs:
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node and text_node[0].text is not None:
            run_text = text_node[0].text
        else:
            run_text = ""
        
        # Preserve exact text content including spaces
        merged_text += run_text
        run_texts.append(run_text)
        run_lengths.append(len(run_text))
        run_styles.append(_extract_run_style(text_run, namespaces))
    
    return {
        'merged_text': merged_text,
        'run_texts': run_texts,
        'run_styles': run_styles,
        'run_lengths': run_lengths
    }


def _extract_run_style(text_run, namespaces: Dict) -> Dict:
    """Extract comprehensive style information from a text run."""
    style_info = {}
    
    try:
        rpr = text_run.xpath('./a:rPr', namespaces=namespaces)
        if rpr:
            rpr_element = rpr[0]
            
            # Font size
            sz = rpr_element.get('sz')
            if sz:
                style_info['font_size'] = sz
            
            # Bold
            b = rpr_element.get('b')
            if b:
                style_info['bold'] = b
            
            # Italic
            i = rpr_element.get('i')
            if i:
                style_info['italic'] = i
            
            # Underline
            u = rpr_element.get('u')
            if u:
                style_info['underline'] = u
            
            # Font family
            latin = rpr_element.xpath('./a:latin', namespaces=namespaces)
            if latin:
                style_info['font_family'] = latin[0].get('typeface')
            
            # Font color
            solid_fill = rpr_element.xpath('./a:solidFill/a:srgbClr', namespaces=namespaces)
            if solid_fill:
                style_info['color'] = solid_fill[0].get('val')
            
            # Strike through
            strike = rpr_element.get('strike')
            if strike:
                style_info['strike'] = strike
                
    except Exception as e:
        app_logger.warning(f"Failed to extract style information: {e}")
    
    return style_info

def write_translated_content_to_ppt(file_path: str, original_json_path: str, translated_json_path: str, temp_dir, result_dir, src_lang=None, dst_lang=None) -> str:
    """
    Write translated content back to the PowerPoint file while preserving format and structure.

    Args:
        src_lang: Source language code (e.g., 'zh')
        dst_lang: Target language code (e.g., 'ja')
    """
    try:
        # Load original and translated JSON
        with open(original_json_path, "r", encoding="utf-8") as original_file:
            original_data = json.load(original_file)
        with open(translated_json_path, "r", encoding="utf-8") as translated_file:
            translated_data = json.load(translated_file)
    except Exception as e:
        app_logger.error(f"Failed to load JSON files: {e}")
        raise

    # Create a mapping of translations
    translations = {str(item["count_src"]): item["translated"] for item in translated_data}
    app_logger.info(f"Loaded {len(translations)} translations")

    # Prepare output path
    filename = os.path.splitext(os.path.basename(file_path))[0]
    result_folder = result_dir
    os.makedirs(result_folder, exist_ok=True)
    # Use source_lang2target_lang format if available, otherwise fallback to _translated
    if src_lang and dst_lang:
        lang_suffix = f"{src_lang}2{dst_lang}"
    else:
        lang_suffix = "translated"
    result_path = os.path.join(result_folder, f"{filename}_{lang_suffix}.pptx")
    
    # Remove existing file if it exists
    if os.path.exists(result_path):
        os.remove(result_path)

    # Create temporary directory for modified files
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)

    # Define namespaces including SmartArt
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
        'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
        'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram'
    }

    try:
        with ZipFile(file_path, 'r') as pptx:
            slides = [name for name in pptx.namelist() 
                     if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
            slides.sort()
            
            notes_slides = [name for name in pptx.namelist() 
                          if name.startswith('ppt/notesSlides/notesSlide') and name.endswith('.xml')]
            notes_slides.sort()
            
            # Get SmartArt diagram files
            diagram_files = [name for name in pptx.namelist()
                           if name.startswith('ppt/diagrams/') and name.endswith('.xml')]

            # Process each slide
            for slide_index, slide_path in enumerate(slides, start=1):
                try:
                    slide_xml = pptx.read(slide_path)
                    slide_tree = etree.fromstring(slide_xml, parser=_SAFE_PARSER)
                    
                    # Get items for this slide
                    slide_items = [item for item in original_data if item.get('slide_index') == slide_index]
                    
                    # Apply translations to slide
                    _apply_translations_to_slide(slide_tree, slide_items, translations, namespaces)

                    # Apply alt-text (cNvPr descr/title) on the same slide tree
                    slide_alttext = [item for item in original_data
                                     if item.get('type') == 'ppt_alttext'
                                     and item.get('part_path') == slide_path]
                    if slide_alttext:
                        _apply_alttext_to_tree(slide_tree, slide_alttext, translations, namespaces)

                    # Save modified slide
                    modified_slide_path = os.path.join(temp_folder, slide_path)
                    os.makedirs(os.path.dirname(modified_slide_path), exist_ok=True)
                    
                    with open(modified_slide_path, "wb") as modified_slide:
                        modified_slide.write(etree.tostring(slide_tree, xml_declaration=True, 
                                                          encoding="UTF-8", standalone="yes"))
                        
                except Exception as e:
                    app_logger.error(f"Failed to process slide {slide_index}: {e}")
                    continue

            # Process notes slides.
            # Derive the notes path from each slide path exactly as extraction does
            # (slides/slideN -> notesSlides/notesSlideN), so slide_index stays aligned
            # when only some slides have notes.
            for slide_index, slide_path in enumerate(slides, start=1):
                notes_path = slide_path.replace('slides/slide', 'notesSlides/notesSlide')
                if notes_path not in pptx.namelist():
                    continue
                try:
                    notes_xml = pptx.read(notes_path)
                    notes_tree = etree.fromstring(notes_xml, parser=_SAFE_PARSER)
                    
                    # Get notes items for this slide
                    notes_items = [item for item in original_data 
                                 if item.get('slide_index') == slide_index and item['type'] == 'notes']
                    
                    if notes_items:
                        _apply_notes_translations(notes_tree, notes_items, translations, namespaces)
                        
                        # Save modified notes
                        modified_notes_path = os.path.join(temp_folder, notes_path)
                        os.makedirs(os.path.dirname(modified_notes_path), exist_ok=True)
                        
                        with open(modified_notes_path, "wb") as modified_notes:
                            modified_notes.write(etree.tostring(notes_tree, xml_declaration=True, 
                                                              encoding="UTF-8", standalone="yes"))
                            
                except Exception as e:
                    app_logger.error(f"Failed to process notes for slide {slide_index}: {e}")
                    continue

            # Process SmartArt diagrams
            smartart_items = [item for item in original_data if item['type'] == 'smartart']
            if smartart_items:
                _apply_smartart_translations(pptx, smartart_items, translations, temp_folder, namespaces)

            # Process chart parts (titles, series/category labels)
            chart_items = [item for item in original_data if item.get('type') == 'chart_part']
            if chart_items:
                _apply_chart_parts_translations(pptx, chart_items, translations, temp_folder, namespaces)
            chart_files = sorted({item['chart_file'] for item in chart_items})

            # Alt-text items on master/layout parts are applied in the same pass
            # as master/layout static text (slide alt-text was applied in the
            # slide loop above, on the shared slide tree).
            slide_set = set(slides)
            ml_alttext = [item for item in original_data
                          if item.get('type') == 'ppt_alttext'
                          and item.get('part_path') not in slide_set]

            # Process slide master/layout static text (+ master/layout alt-text)
            ml_items = [item for item in original_data if item.get('type') == 'master_layout']
            ml_files = []
            if ml_items or ml_alttext:
                ml_files = _apply_master_layout_translations(pptx, ml_items, translations,
                                                             temp_folder, namespaces,
                                                             alttext_items=ml_alttext)

            # Process PPT comments (legacy + modern threaded)
            comment_items = [item for item in original_data if item.get('type') == 'ppt_comment']
            comment_files = []
            if comment_items:
                comment_files = _apply_comment_translations(pptx, comment_items, translations,
                                                            temp_folder, namespaces)

            # Process notes/handout masters
            nhm_items = [item for item in original_data
                         if item.get('type') in ('ppt_notesmaster', 'ppt_handoutmaster')]
            nhm_files = []
            if nhm_items:
                nhm_files = _apply_notes_handout_master_translations(pptx, nhm_items, translations,
                                                                     temp_folder, namespaces)

            # Create final PowerPoint file (chart/master/layout/comment/master
            # parts ride the same modified-or-original mechanism as diagram files)
            _create_final_pptx(file_path, result_path, temp_folder, slides, notes_slides,
                               diagram_files + chart_files + ml_files
                               + comment_files + nhm_files)
            
    except Exception as e:
        app_logger.error(f"Failed to write translated content: {e}")
        raise

    app_logger.info(f"Translated PowerPoint saved to: {result_path}")
    return result_path

def _apply_smartart_translations(pptx, smartart_items: List[Dict], translations: Dict, 
                                temp_folder: str, namespaces: Dict):
    """Apply translations to SmartArt diagrams."""
    if not smartart_items:
        return
    
    app_logger.info(f"Processing {len(smartart_items)} SmartArt translations")
    
    # Group items by diagram_index
    items_by_diagram = {}
    for item in smartart_items:
        diagram_index = item['diagram_index']
        if diagram_index not in items_by_diagram:
            items_by_diagram[diagram_index] = []
        items_by_diagram[diagram_index].append(item)
    
    for diagram_index, items in items_by_diagram.items():
        drawing_path = f"ppt/diagrams/drawing{diagram_index}.xml"
        data_path = f"ppt/diagrams/data{diagram_index}.xml"
        
        # Process drawing file
        try:
            if drawing_path in pptx.namelist():
                drawing_xml = pptx.read(drawing_path)
                drawing_tree = etree.fromstring(drawing_xml, parser=_SAFE_PARSER)
                
                for item in items:
                    count = str(item['count_src'])
                    translated_text = translations.get(count)
                    
                    if not translated_text:
                        app_logger.warning(f"Missing translation for SmartArt count {count}")
                        continue
                    
                    translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                    
                    # Find the shape using shape_index
                    shapes_with_txbody = drawing_tree.xpath('.//dsp:sp[.//dsp:txBody]', namespaces=namespaces)
                    
                    if item['shape_index'] < len(shapes_with_txbody):
                        shape = shapes_with_txbody[item['shape_index']]
                        
                        # Find the txBody
                        tx_bodies = shape.xpath('.//dsp:txBody', namespaces=namespaces)
                        if item['tx_body_index'] < len(tx_bodies):
                            tx_body = tx_bodies[item['tx_body_index']]
                            
                            # Find the paragraph
                            paragraphs = tx_body.xpath('.//a:p', namespaces=namespaces)
                            if item['paragraph_index'] < len(paragraphs):
                                paragraph = paragraphs[item['paragraph_index']]
                                _distribute_text_to_runs(paragraph, translated_text, item, namespaces)
                                app_logger.info(f"Updated drawing text for diagram {diagram_index}, shape {item['shape_index']}")
                
                # Save modified drawing
                modified_drawing_path = os.path.join(temp_folder, drawing_path)
                os.makedirs(os.path.dirname(modified_drawing_path), exist_ok=True)
                
                with open(modified_drawing_path, "wb") as modified_drawing:
                    modified_drawing.write(etree.tostring(drawing_tree, xml_declaration=True, 
                                                        encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified drawing file: {drawing_path}")
                                                        
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {drawing_path}: {e}")
            continue
        
        # Process corresponding data file
        try:
            if data_path in pptx.namelist():
                data_xml = pptx.read(data_path)
                data_tree = etree.fromstring(data_xml)
                
                for item in items:
                    count = str(item['count_src'])
                    translated_text = translations.get(count)
                    
                    if not translated_text:
                        continue
                    
                    translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                    original_text = item.get('original_text', '')
                    
                    # Find all dgm:pt elements that contain text
                    points = data_tree.xpath('.//dgm:pt[.//a:t]', namespaces=namespaces)
                    
                    # Try to find matching text by content
                    for point in points:
                        point_paragraphs = point.xpath('.//a:p', namespaces=namespaces)
                        for p_idx, point_paragraph in enumerate(point_paragraphs):
                            # Get current text from this paragraph
                            point_text_runs = point_paragraph.xpath('.//a:r', namespaces=namespaces)
                            if point_text_runs:
                                point_run_info = _process_text_runs(point_text_runs, namespaces)
                                # If the original text matches, update this paragraph
                                if point_run_info['merged_text'].strip() == original_text.strip():
                                    _distribute_text_to_runs(point_paragraph, translated_text, item, namespaces)
                                    app_logger.info(f"Updated data text for diagram {diagram_index}: '{original_text}' -> '{translated_text[:50]}...'")
                                    break
                
                # Save modified data
                modified_data_path = os.path.join(temp_folder, data_path)
                os.makedirs(os.path.dirname(modified_data_path), exist_ok=True)
                
                with open(modified_data_path, "wb") as modified_data:
                    modified_data.write(etree.tostring(data_tree, xml_declaration=True, 
                                                     encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified data file: {data_path}")
                                                     
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {data_path}: {e}")
            continue

def _apply_translations_to_slide(slide_tree, slide_items: List[Dict], translations: Dict, namespaces: Dict):
    """Apply translations to a slide tree."""
    for item in slide_items:
        if item['type'] == 'notes':
            continue  # Handle notes separately
            
        count = str(item['count_src'])
        translated_text = translations.get(count)
        
        if not translated_text:
            app_logger.warning(f"Missing translation for count {count}")
            continue
        
        # Restore line breaks
        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
        
        try:
            # Apply translation based on item type
            if item['type'] == 'text_paragraph':
                _apply_text_paragraph_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'table_cell_paragraph':
                _apply_table_cell_paragraph_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'table_cell':  # For backward compatibility
                _apply_table_cell_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'shape':
                _apply_shape_translation(slide_tree, item, translated_text, namespaces)
            # Charts are handled separately via 'chart_part' items
            # (ppt/charts/chartN.xml), not through the slide tree.

        except Exception as e:
            app_logger.error(f"Failed to apply translation for count {count}: {e}")

def _apply_text_paragraph_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a text paragraph, distributing across runs."""
    text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
    
    if item['text_box_index'] <= len(text_boxes):
        text_box = text_boxes[item['text_box_index'] - 1]
        paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
        
        if item['paragraph_index'] < len(paragraphs):
            paragraph = paragraphs[item['paragraph_index']]
            _distribute_text_to_runs(paragraph, translated_text, item, namespaces)

def _apply_table_cell_paragraph_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a table cell paragraph, distributing across runs."""
    tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
    
    if item['table_index'] <= len(tables):
        table = tables[item['table_index'] - 1]
        rows = table.xpath('.//a:tr', namespaces=namespaces)
        
        if item['row_index'] < len(rows):
            row = rows[item['row_index']]
            cells = row.xpath('.//a:tc', namespaces=namespaces)
            
            if item['cell_index'] < len(cells):
                cell = cells[item['cell_index']]
                paragraphs = cell.xpath('.//a:p', namespaces=namespaces)
                
                if item['paragraph_index'] < len(paragraphs):
                    paragraph = paragraphs[item['paragraph_index']]
                    _distribute_text_to_runs(paragraph, translated_text, item, namespaces)

def _apply_table_cell_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a table cell, distributing across runs. (For backward compatibility)"""
    tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
    
    if item['table_index'] <= len(tables):
        table = tables[item['table_index'] - 1]
        rows = table.xpath('.//a:tr', namespaces=namespaces)
        
        if item['row_index'] < len(rows):
            row = rows[item['row_index']]
            cells = row.xpath('.//a:tc', namespaces=namespaces)
            
            if item['cell_index'] < len(cells):
                cell = cells[item['cell_index']]
                _distribute_text_to_runs(cell, translated_text, item, namespaces)

def _apply_shape_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a shape, distributing across runs."""
    shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
    
    # Filter out shapes that are text boxes to maintain proper indexing
    non_textbox_shapes = [shape for shape in shapes 
                         if not shape.xpath('.//p:txBody', namespaces=namespaces)]
    
    if item['shape_index'] <= len(non_textbox_shapes):
        shape = non_textbox_shapes[item['shape_index'] - 1]
        _distribute_text_to_runs(shape, translated_text, item, namespaces)

def _distribute_text_to_runs(parent_element, translated_text: str, item: Dict, namespaces: Dict):
    """Distribute translated text across multiple runs, preserving spacing and structure."""
    text_runs = parent_element.xpath('.//a:r', namespaces=namespaces)
    
    if not text_runs:
        return
    
    original_run_texts = item.get('run_texts', [])
    original_run_lengths = item.get('run_lengths', [])
    
    # If we don't have the original structure, fallback to simple distribution
    if not original_run_texts or len(original_run_texts) != len(text_runs):
        app_logger.warning("Mismatch in run structure, using simple distribution")
        _simple_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Use intelligent distribution based on original structure
    _intelligent_text_distribution(text_runs, translated_text, original_run_texts, original_run_lengths, namespaces)

def _simple_text_distribution(text_runs, translated_text: str, namespaces: Dict):
    """Simple fallback distribution method."""
    if not text_runs:
        return
    
    # Put all translated text in the first run, clear others
    for i, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node:
            if i == 0:
                text_node[0].text = translated_text
            else:
                text_node[0].text = ""

def _intelligent_text_distribution(text_runs, translated_text: str, original_run_texts: List[str], 
                                 original_run_lengths: List[int], namespaces: Dict):
    """Intelligent text distribution that preserves spacing and structure."""
    
    # Calculate total length excluding empty runs
    meaningful_runs = [(i, length) for i, length in enumerate(original_run_lengths) if length > 0]
    total_meaningful_length = sum(length for _, length in meaningful_runs)
    
    if total_meaningful_length == 0:
        _simple_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Handle special cases for spacing
    translated_chars = list(translated_text)
    char_index = 0
    
    for run_index, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if not text_node:
            continue
            
        original_text = original_run_texts[run_index] if run_index < len(original_run_texts) else ""
        original_length = original_run_lengths[run_index] if run_index < len(original_run_lengths) else 0
        
        # Handle empty or whitespace-only runs
        if original_length == 0 or not original_text.strip():
            # If original run was empty or whitespace, keep it empty
            # unless it was purely whitespace and we need to preserve spacing
            if original_text and not original_text.strip():
                # This run contained only whitespace, try to preserve some spacing
                if char_index < len(translated_chars) and translated_chars[char_index] == ' ':
                    text_node[0].text = ' '
                    char_index += 1
                else:
                    text_node[0].text = ""
            else:
                text_node[0].text = ""
            continue
        
        # Calculate how much text this run should get
        if run_index == len(text_runs) - 1:
            # Last meaningful run gets all remaining text
            remaining_text = ''.join(translated_chars[char_index:])
            text_node[0].text = remaining_text
        else:
            # Calculate proportional distribution
            proportion = original_length / total_meaningful_length
            target_length = max(1, int(len(translated_text) * proportion))
            
            # Try to break at word boundaries
            run_text = ""
            chars_taken = 0
            
            while chars_taken < target_length and char_index < len(translated_chars):
                char = translated_chars[char_index]
                run_text += char
                chars_taken += 1
                char_index += 1
                
                # If we've reached the target length, try to extend to a word boundary
                if chars_taken >= target_length and char_index < len(translated_chars):
                    # Look ahead for word boundary
                    if char != ' ' and translated_chars[char_index] != ' ':
                        # Continue until we find a space or reach the end
                        while (char_index < len(translated_chars) and 
                               translated_chars[char_index] != ' ' and 
                               chars_taken < target_length * 1.5):  # Don't go too far
                            char = translated_chars[char_index]
                            run_text += char
                            chars_taken += 1
                            char_index += 1
                    break
            
            text_node[0].text = run_text

def _apply_notes_translations(notes_tree, notes_items: List[Dict], translations: Dict, namespaces: Dict):
    """Apply translations to notes."""
    for item in notes_items:
        count = str(item['count_src'])
        translated_text = translations.get(count)
        
        if not translated_text:
            app_logger.warning(f"Missing translation for notes count {count}")
            continue
        
        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
        
        try:
            paragraphs = notes_tree.xpath('.//a:p', namespaces=namespaces)
            
            if item['paragraph_index'] < len(paragraphs):
                paragraph = paragraphs[item['paragraph_index']]
                _distribute_text_to_runs(paragraph, translated_text, item, namespaces)
                    
        except Exception as e:
            app_logger.error(f"Failed to apply notes translation for count {count}: {e}")

def _create_final_pptx(original_path: str, result_path: str, temp_folder: str, 
                      slides: List[str], notes_slides: List[str], diagram_files: List[str]):
    """Create the final translated PowerPoint file."""
    with ZipFile(original_path, 'r') as original_pptx:
        with ZipFile(result_path, 'w') as new_pptx:
            # Copy all files except slides, notes, and diagrams that we've modified
            exclude_files = set(slides + notes_slides + diagram_files)
            
            for item in original_pptx.infolist():
                if item.filename not in exclude_files:
                    try:
                        new_pptx.writestr(item, original_pptx.read(item.filename))
                    except Exception as e:
                        app_logger.warning(f"Failed to copy file {item.filename}: {e}")
            
            # Add modified slides
            for slide in slides:
                modified_slide_path = os.path.join(temp_folder, slide)
                if os.path.exists(modified_slide_path):
                    try:
                        new_pptx.write(modified_slide_path, slide)
                    except Exception as e:
                        app_logger.error(f"Failed to add modified slide {slide}: {e}")
                        # Fallback to original slide
                        try:
                            new_pptx.writestr(slide, original_pptx.read(slide))
                        except Exception as fallback_e:
                            app_logger.error(f"Failed to add original slide as fallback: {fallback_e}")
                else:
                    app_logger.warning(f"Modified slide not found: {modified_slide_path}. Using original.")
                    try:
                        new_pptx.writestr(slide, original_pptx.read(slide))
                    except Exception as e:
                        app_logger.error(f"Failed to add original slide: {e}")
            
            # Add modified notes (or original if no modification)
            for notes in notes_slides:
                modified_notes_path = os.path.join(temp_folder, notes)
                if os.path.exists(modified_notes_path):
                    try:
                        new_pptx.write(modified_notes_path, notes)
                    except Exception as e:
                        app_logger.error(f"Failed to add modified notes {notes}: {e}")
                        # Fallback to original notes
                        try:
                            new_pptx.writestr(notes, original_pptx.read(notes))
                        except Exception as fallback_e:
                            app_logger.error(f"Failed to add original notes as fallback: {fallback_e}")
                else:
                    # Use original notes if no modified version exists
                    try:
                        new_pptx.writestr(notes, original_pptx.read(notes))
                    except Exception as e:
                        app_logger.error(f"Failed to add original notes: {e}")
            
            # Add modified diagram files (or original if no modification)
            for diagram_file in diagram_files:
                modified_diagram_path = os.path.join(temp_folder, diagram_file)
                if os.path.exists(modified_diagram_path):
                    try:
                        new_pptx.write(modified_diagram_path, diagram_file)
                        app_logger.info(f"Added modified SmartArt file: {diagram_file}")
                    except Exception as e:
                        app_logger.error(f"Failed to add modified diagram {diagram_file}: {e}")
                        # Fallback to original diagram
                        try:
                            new_pptx.writestr(diagram_file, original_pptx.read(diagram_file))
                        except Exception as fallback_e:
                            app_logger.error(f"Failed to add original diagram as fallback: {fallback_e}")
                else:
                    # Use original diagram if no modified version exists
                    try:
                        new_pptx.writestr(diagram_file, original_pptx.read(diagram_file))
                    except Exception as e:
                        app_logger.error(f"Failed to add original diagram: {e}")