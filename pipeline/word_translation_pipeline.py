# pipeline/word_translation_pipeline.py
import json
import os
import re
from lxml import etree
from zipfile import ZipFile, ZIP_DEFLATED
from .skip_pipeline import should_translate
from config.log_config import app_logger
from textProcessing.text_separator import safe_convert_to_int
import shutil
import tempfile

def extract_word_content_to_json(file_path):
    """Extract translatable content from Word document to JSON"""
    temp_dir = None
    try:
        # Create temporary directory for processing
        temp_dir = tempfile.mkdtemp()
        
        # Extract entire docx archive
        with ZipFile(file_path, 'r') as docx:
            docx.extractall(temp_dir)
        
        # Read main document
        document_xml_path = os.path.join(temp_dir, 'word', 'document.xml')
        with open(document_xml_path, 'rb') as f:
            document_xml = f.read()
        
        # Read numbering.xml if exists
        numbering_xml = None
        numbering_xml_path = os.path.join(temp_dir, 'word', 'numbering.xml')
        if os.path.exists(numbering_xml_path):
            with open(numbering_xml_path, 'rb') as f:
                numbering_xml = f.read()
        
        # Read styles.xml if exists
        styles_xml = None
        styles_xml_path = os.path.join(temp_dir, 'word', 'styles.xml')
        if os.path.exists(styles_xml_path):
            with open(styles_xml_path, 'rb') as f:
                styles_xml = f.read()
        
        # Get all header and footer files
        word_dir = os.path.join(temp_dir, 'word')
        header_footer_files = {}
        if os.path.exists(word_dir):
            for filename in os.listdir(word_dir):
                if filename.startswith('header') or filename.startswith('footer'):
                    filepath = os.path.join(word_dir, filename)
                    with open(filepath, 'rb') as f:
                        header_footer_files[f'word/{filename}'] = f.read()

        # Complete namespaces including all possible schemas and SmartArt
        namespaces = {
            'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
            'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
            'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
            'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'v': 'urn:schemas-microsoft-com:vml',
            'w10': 'urn:schemas-microsoft-com:office:word',
            'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
            'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
            'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
            'wpc': 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas',
            'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',
            'wpi': 'http://schemas.microsoft.com/office/word/2010/wordprocessingInk',
            'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
            'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
            'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram'
        }
        
        document_tree = etree.fromstring(document_xml)
        
        # Parse numbering and styles information
        numbering_info = {}
        styles_info = {}
        
        if numbering_xml:
            numbering_info = parse_numbering_xml(numbering_xml, namespaces)
        
        if styles_xml:
            styles_info = parse_styles_xml(styles_xml, namespaces)

        content_data = []
        item_id = 0
        
        # Extract translatable content from numbering.xml first
        if numbering_xml:
            numbering_items = extract_numbering_translatable_content(numbering_xml, namespaces)
            for numbering_item in numbering_items:
                item_id += 1
                numbering_item["id"] = item_id
                numbering_item["count_src"] = item_id
                content_data.append(numbering_item)
        
        # Extract SmartArt content using ZipFile object
        with ZipFile(file_path, 'r') as docx:
            smartart_items = extract_smartart_content(docx, namespaces)
            for smartart_item in smartart_items:
                item_id += 1
                smartart_item["id"] = item_id
                smartart_item["count_src"] = item_id
                content_data.append(smartart_item)
        
        # Process main document content
        item_id = process_document_content(
            document_tree, content_data, item_id, numbering_info, styles_info, namespaces
        )
        
        # Process headers and footers
        for hf_file, hf_xml in header_footer_files.items():
            hf_tree = etree.fromstring(hf_xml)
            hf_type = "header" if "header" in hf_file else "footer"
            hf_number = os.path.basename(hf_file).split('.')[0]
            
            item_id = process_header_footer_content(
                hf_tree, content_data, item_id, numbering_info, styles_info, 
                namespaces, hf_type, hf_file, hf_number
            )

        # Save extraction data and temp directory path
        filename = os.path.splitext(os.path.basename(file_path))[0]
        temp_folder = os.path.join("temp", filename)
        os.makedirs(temp_folder, exist_ok=True)
        
        # Save temp directory path for later use
        temp_dir_info_path = os.path.join(temp_folder, "temp_dir_path.txt")
        with open(temp_dir_info_path, "w", encoding="utf-8") as f:
            f.write(temp_dir)
        
        json_path = os.path.join(temp_folder, "src.json")
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(content_data, json_file, ensure_ascii=False, indent=4)

        app_logger.info(f"Extracted {len(content_data)} content items from document: {filename}")
        return json_path
        
    except Exception as e:
        # Clean up temp directory on error
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise e

def extract_smartart_content(docx, namespaces):
    """Extract translatable content from SmartArt diagrams in Word document"""
    smartart_items = []
    
    try:
        # Find all diagram drawing files
        diagram_drawings = [name for name in docx.namelist() 
                           if name.startswith('word/diagrams/drawing') and name.endswith('.xml')]
        diagram_drawings.sort()
        
        app_logger.info(f"Found {len(diagram_drawings)} SmartArt diagram files in Word document")
        
        count = 0
        
        for drawing_path in diagram_drawings:
            try:
                # Extract diagram number from path (e.g., drawing1.xml -> 1)
                diagram_match = re.search(r'drawing(\d+)\.xml', drawing_path)
                if not diagram_match:
                    continue
                
                diagram_index = int(diagram_match.group(1))
                
                drawing_xml = docx.read(drawing_path)
                drawing_tree = etree.fromstring(drawing_xml)
                
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
                            run_info = process_smartart_text_runs(text_runs, namespaces)
                            
                            if not run_info['merged_text'].strip():
                                continue
                            
                            # Only process if there's meaningful text content and it should be translated
                            if should_translate_enhanced(run_info['merged_text']):
                                count += 1
                                smartart_item = {
                                    "type": "smartart",
                                    "diagram_index": diagram_index,
                                    "shape_index": shape_index,
                                    "tx_body_index": tx_body_index,
                                    "paragraph_index": p_index,
                                    "model_id": model_id,
                                    "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                                    "run_texts": run_info['run_texts'],
                                    "run_styles": run_info['run_styles'],
                                    "run_lengths": run_info['run_lengths'],
                                    "drawing_path": drawing_path,
                                    "original_text": run_info['merged_text'],  # Store original text for data.xml matching
                                    "xpath": f".//dsp:sp[{shape_index + 1}]//dsp:txBody[{tx_body_index + 1}]//a:p[{p_index + 1}]"
                                }
                                smartart_items.append(smartart_item)
                                app_logger.debug(f"Extracted SmartArt text: '{run_info['merged_text'][:50]}...'")
                            
            except Exception as e:
                app_logger.error(f"Failed to extract SmartArt from {drawing_path}: {e}")
                continue
        
        app_logger.info(f"Extracted {len(smartart_items)} translatable SmartArt text items")
        
    except Exception as e:
        app_logger.error(f"Error extracting SmartArt content: {e}")
    
    return smartart_items

def process_smartart_text_runs(text_runs, namespaces):
    """Process SmartArt text runs and preserve exact spacing and formatting"""
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
        run_styles.append(extract_smartart_run_style(text_run, namespaces))
    
    return {
        'merged_text': merged_text,
        'run_texts': run_texts,
        'run_styles': run_styles,
        'run_lengths': run_lengths
    }

def extract_smartart_run_style(text_run, namespaces):
    """Extract comprehensive style information from a SmartArt text run"""
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
        app_logger.warning(f"Failed to extract SmartArt style information: {e}")
    
    return style_info

def parse_styles_xml(styles_xml, namespaces):
    """Parse styles.xml to understand style definitions"""
    styles_info = {}
    
    if not styles_xml:
        return styles_info
    
    try:
        styles_tree = etree.fromstring(styles_xml)
        
        # Parse style definitions
        styles = styles_tree.xpath('//w:style', namespaces=namespaces)
        for style in styles:
            style_id = style.get(f'{{{namespaces["w"]}}}styleId')
            style_type = style.get(f'{{{namespaces["w"]}}}type')
            
            if style_id:
                styles_info[style_id] = {
                    'type': style_type,
                    'name': None,
                    'basedOn': None,
                    'next': None
                }
                
                # Get style name
                name_nodes = style.xpath('.//w:name', namespaces=namespaces)
                if name_nodes:
                    styles_info[style_id]['name'] = name_nodes[0].get(f'{{{namespaces["w"]}}}val')
                
                # Get basedOn
                basedOn_nodes = style.xpath('.//w:basedOn', namespaces=namespaces)
                if basedOn_nodes:
                    styles_info[style_id]['basedOn'] = basedOn_nodes[0].get(f'{{{namespaces["w"]}}}val')
                
                # Get next
                next_nodes = style.xpath('.//w:next', namespaces=namespaces)
                if next_nodes:
                    styles_info[style_id]['next'] = next_nodes[0].get(f'{{{namespaces["w"]}}}val')
        
    except Exception as e:
        app_logger.error(f"Error parsing styles.xml: {e}")
    
    return styles_info

def process_document_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces):
    """Process main document content with better structure handling"""
    
    # First, process SDT (Structured Document Tags) content like TOC
    item_id = process_sdt_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces)
    
    # Get all body elements (including nested ones)
    body_elements = get_all_body_elements(document_tree, namespaces)
    
    for element_index, element in enumerate(body_elements):
        element_type = element.tag.split('}')[-1]
        
        if element_type == 'p':
            item_id = process_paragraph_element(
                element, content_data, item_id, element_index, 
                numbering_info, styles_info, namespaces
            )
        
        elif element_type == 'tbl':
            item_id = process_table_element(
                element, content_data, item_id, element_index, 
                numbering_info, styles_info, namespaces
            )
        
        elif element_type == 'sdt':
            # Skip SDT elements as they're processed separately
            continue
    
    # Process textboxes separately to avoid duplication
    textbox_items = extract_textbox_content(document_tree, namespaces)
    for textbox_item in textbox_items:
        item_id += 1
        textbox_item["id"] = item_id
        textbox_item["count_src"] = item_id
        content_data.append(textbox_item)
    
    return item_id

def process_sdt_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces):
    """Process Structured Document Tags (SDT) content, especially TOC"""
    
    # Find all SDT elements
    sdt_elements = document_tree.xpath('.//w:sdt', namespaces=namespaces)
    
    for sdt_index, sdt in enumerate(sdt_elements):
        # Check if this is a TOC SDT
        is_toc_sdt = False
        sdt_props = sdt.xpath('./w:sdtPr', namespaces=namespaces)
        
        if sdt_props:
            # Check for Table of Contents gallery
            doc_part_objs = sdt_props[0].xpath('.//w:docPartObj', namespaces=namespaces)
            for doc_part_obj in doc_part_objs:
                gallery = doc_part_obj.xpath('.//w:docPartGallery', namespaces=namespaces)
                if gallery and gallery[0].get(f'{{{namespaces["w"]}}}val') == 'Table of Contents':
                    is_toc_sdt = True
                    break
        
        # Process SDT content
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        if sdt_content:
            # Process paragraphs within SDT
            sdt_paragraphs = sdt_content[0].xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            for para_index, paragraph in enumerate(sdt_paragraphs):
                # Enhanced TOC detection for SDT content
                is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, is_toc_sdt)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
                    full_text = toc_title_text
                    field_info = None
                else:
                    # Extract normal paragraph text
                    numbering_props = paragraph.xpath('.//w:numPr', namespaces=namespaces)
                    paragraph_numbering_info = None
                    if numbering_props:
                        paragraph_numbering_info = extract_paragraph_numbering_info(
                            numbering_props[0], numbering_info, namespaces)
                    
                    full_text, field_info = extract_paragraph_text_with_variables(paragraph, namespaces, paragraph_numbering_info, True)
                    toc_structure = None
                
                if full_text and full_text.strip() and should_translate_enhanced(full_text):
                    item_id += 1
                    item_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "sdt_paragraph",
                        "sdt_index": sdt_index,
                        "paragraph_index": para_index,
                        "is_toc_sdt": is_toc_sdt,
                        "is_toc": is_toc,
                        "value": full_text.replace("\n", "␊").replace("\r", "␍"),
                        "original_pPr": extract_paragraph_properties(paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(paragraph, namespaces),
                        "sdt_props": extract_sdt_properties(sdt, namespaces)
                    }
                    
                    if field_info:
                        item_data["field_info"] = field_info
                    
                    if is_toc:
                        item_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(item_data)
                    app_logger.debug(f"Extracted SDT paragraph {item_id}: '{full_text[:50]}...'")
            
            # Process tables within SDT
            sdt_tables = sdt_content[0].xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            for table_index, table in enumerate(sdt_tables):
                table_props = extract_table_properties(table, namespaces)
                item_id = process_sdt_table_recursive(
                    table, content_data, item_id, sdt_index, table_index,
                    numbering_info, styles_info, namespaces, table_props, is_toc_sdt
                )
    
    return item_id

def process_sdt_table_recursive(table, content_data, item_id, sdt_index, table_index, numbering_info, styles_info, namespaces, table_props, is_toc_sdt, nesting_level=0):
    """Process tables within SDT recursively"""
    
    rows = table.xpath('./w:tr', namespaces=namespaces)
    
    for row_idx, row in enumerate(rows):
        row_props = extract_row_properties(row, namespaces)
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        for cell_idx, cell in enumerate(cells):
            cell_props = extract_cell_properties(cell, namespaces)
            
            # Process cell paragraphs
            cell_paragraphs = cell.xpath('./w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                # Enhanced TOC detection for paragraphs in SDT tables
                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, is_toc_sdt)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                    cell_text = toc_title_text
                    cell_field_info = None
                else:
                    cell_text, cell_field_info = extract_paragraph_text_with_variables(cell_paragraph, namespaces)
                    toc_structure = None
                
                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                    item_id += 1
                    cell_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "sdt_table_cell",
                        "sdt_index": sdt_index,
                        "table_index": table_index,
                        "row": row_idx,
                        "col": cell_idx,
                        "paragraph_index": para_idx,
                        "nesting_level": nesting_level,
                        "is_toc_sdt": is_toc_sdt,
                        "is_toc": is_toc,
                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                        "table_props": table_props,
                        "row_props": row_props,
                        "cell_props": cell_props,
                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                    }
                    
                    if cell_field_info:
                        cell_data["field_info"] = cell_field_info
                    
                    if is_toc:
                        cell_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(cell_data)
                    app_logger.debug(f"Extracted SDT table cell {item_id}: '{cell_text[:50]}...'")
            
            # Process nested tables
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            for nested_table_idx, nested_table in enumerate(nested_tables):
                nested_table_props = extract_table_properties(nested_table, namespaces)
                item_id = process_sdt_table_recursive(
                    nested_table, content_data, item_id, sdt_index,
                    f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}",
                    numbering_info, styles_info, namespaces, 
                    nested_table_props, is_toc_sdt, nesting_level + 1
                )
    
    return item_id

def extract_sdt_properties(sdt, namespaces):
    """Extract SDT properties for format preservation"""
    sdt_props = {}
    
    sdt_pr = sdt.xpath('./w:sdtPr', namespaces=namespaces)
    if sdt_pr:
        sdt_props['sdtPr_xml'] = etree.tostring(sdt_pr[0], encoding='unicode')
    
    sdt_end_pr = sdt.xpath('./w:sdtEndPr', namespaces=namespaces)
    if sdt_end_pr:
        sdt_props['sdtEndPr_xml'] = etree.tostring(sdt_end_pr[0], encoding='unicode')
    
    return sdt_props

def detect_toc_paragraph_enhanced(paragraph, namespaces, is_in_toc_sdt=False):
    """Enhanced TOC detection that handles SDT and complex structures"""
    
    # If paragraph is in a TOC SDT, it's likely a TOC entry
    if is_in_toc_sdt:
        # Check for hyperlink structure typical of TOC entries
        hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
        if hyperlinks:
            hyperlink = hyperlinks[0]
            anchor = hyperlink.get(f'{{{namespaces["w"]}}}anchor', '')
            
            # Check for bookmark anchors
            if anchor and (anchor.startswith('bookmark') or anchor.startswith('_Toc') or anchor.startswith('_Ref')):
                # Check for typical TOC pattern with tabs and page numbers
                paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
                if has_toc_pattern_enhanced(paragraph_text) or has_tab_structure(paragraph, namespaces):
                    toc_info = {
                        'style': 'sdt_hyperlink_based',
                        'level': detect_toc_level_from_sdt_formatting(paragraph, namespaces),
                        'detection_method': 'sdt_hyperlink_pattern',
                        'anchor': anchor,
                        'in_sdt': True
                    }
                    return True, toc_info
        
        # Even without hyperlinks, if in TOC SDT and has tab structure, likely TOC
        if has_tab_structure(paragraph, namespaces):
            paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
            if paragraph_text.strip():  # Has actual content
                toc_info = {
                    'style': 'sdt_tab_based',
                    'level': detect_toc_level_from_sdt_formatting(paragraph, namespaces),
                    'detection_method': 'sdt_tab_structure',
                    'in_sdt': True
                }
                return True, toc_info
    
    # Fallback to original TOC detection
    return detect_toc_paragraph(paragraph, namespaces)

def has_toc_pattern_enhanced(text):
    """Enhanced pattern detection for TOC entries"""
    if not text or len(text.strip()) < 2:
        return False
    
    # Clean the text first - remove page numbers from the end for pattern matching
    text_clean = text.strip()
    
    # Remove page numbers from the end
    text_clean = re.sub(r'\s*\.\d+\s*$', '', text_clean)  # Remove .57, .123 etc
    text_clean = re.sub(r'\s*\d+\s*$', '', text_clean)    # Remove trailing numbers
    text_clean = re.sub(r'\.{3,}\s*$', '', text_clean)    # Remove trailing dots
    text_clean = text_clean.strip()
    
    if len(text_clean) < 2:
        return False
    
    # Enhanced TOC patterns including Spanish and other languages
    patterns = [
        r'.+\.{3,}\s*\d+$',          # Text...123
        r'.+\t+\d+$',                # Text    123 (with tabs)
        r'.+\s{5,}\d+$',             # Text     123 (with many spaces)
        r'.+\.\s*\.+\s*\d+$',        # Text. ... 123
        r'.+\s+\d+$',                # Text 123 (simple space + number)
        r'^\d+\.?\d*\s+.+\s+\d+$',   # 1.1 Text 123 (numbered sections)
        r'^[A-Z][A-ZÁÉÍÓÚÜÑ\s]+\s+\d+$',  # UPPERCASE TEXT 123 (Spanish uppercase)
        r'^\w+.*\w+\s+\d+$',         # General word + number pattern
        r'.+\s*\.\d+$',              # Text .57 (dot + number at end)
    ]
    
    # Test against original text
    for pattern in patterns:
        if re.search(pattern, text.strip(), re.IGNORECASE):
            return True
    
    # If we have meaningful text content after cleaning, it's likely a TOC entry
    # especially if it contains section numbers or has proper structure
    if len(text_clean) > 5:  # Reasonable minimum length for TOC entry
        # Check for section numbering patterns
        if re.match(r'^\d+\.?\d*\s+', text_clean):  # Starts with number
            return True
        
        # Check if it has typical TOC content (letters and spaces, not just symbols)
        letter_count = sum(1 for c in text_clean if c.isalpha())
        if letter_count > 3:  # Has substantial text content
            return True
    
    return False

def has_tab_structure(paragraph, namespaces):
    """Check if paragraph has tab structure typical of TOC"""
    tabs = paragraph.xpath('.//w:tab', namespaces=namespaces)
    return len(tabs) > 0

def detect_toc_level_from_sdt_formatting(paragraph, namespaces):
    """Detect TOC level from SDT paragraph formatting"""
    # Check indentation
    ind_elements = paragraph.xpath('.//w:ind', namespaces=namespaces)
    if ind_elements:
        left_indent = ind_elements[0].get(f'{{{namespaces["w"]}}}left', '0')
        try:
            indent_value = safe_convert_to_int(left_indent)
            # Estimate level based on indentation
            level = max(1, (indent_value // 400) + 1)  # Adjusted for typical SDT indentation
            return min(level, 9)
        except:
            pass
    
    # Check style-based level
    style_elements = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if style_elements:
        style_val = style_elements[0].get(f'{{{namespaces["w"]}}}val', '').lower()
        level_match = re.search(r'(\d+)', style_val)
        if level_match:
            return safe_convert_to_int(level_match.group(1))
    
    return 1

def extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces):
    """Enhanced TOC title extraction that handles complex SDT structures"""
    
    # First try the original method
    title_text, structure = extract_toc_title_with_complete_structure(paragraph, namespaces)
    
    # Enhanced processing for SDT TOC entries
    if not title_text or len(title_text.strip()) < 2:
        # Try alternative extraction for complex structures
        title_text, structure = extract_toc_title_alternative(paragraph, namespaces)
    
    return title_text, structure

def extract_toc_title_alternative(paragraph, namespaces):
    """Alternative method for extracting TOC title from complex structures"""
    
    # Get all text content first
    all_text_nodes = paragraph.xpath('.//w:t', namespaces=namespaces)
    all_run_texts = []
    
    # Collect text from each run separately to maintain structure
    all_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    for run in all_runs:
        text_nodes = run.xpath('.//w:t', namespaces=namespaces)
        run_text = ''.join(node.text or '' for node in text_nodes)
        all_run_texts.append(run_text)
    
    # Join all text to get the full content
    full_text = ''.join(all_run_texts)
    
    # Identify title vs page number and other elements
    title_parts = []
    
    for i, run_text in enumerate(all_run_texts):
        if not run_text.strip():
            # Empty or whitespace - add to title to maintain spacing
            if title_parts or i < len(all_run_texts) - 3:  # Don't add trailing spaces
                title_parts.append(run_text)
            continue
        
        # Check if this is a page number
        if is_likely_page_number(run_text):
            # This is a page number, stop adding to title
            break
        
        # Check if this is dot leaders
        if is_dot_leader(run_text):
            # This is dot leaders, stop adding to title
            break
        
        # Check if this is a tab
        run = all_runs[i] if i < len(all_runs) else None
        if run is not None and run.xpath('.//w:tab', namespaces=namespaces):
            # This is a tab, stop adding to title
            break
        
        # Add to title
        title_parts.append(run_text)
    
    # Join title parts and clean up
    title_text = ''.join(title_parts).strip()
    
    # Remove trailing dots that might be part of leaders
    title_text = re.sub(r'\.+$', '', title_text).strip()
    
    # Remove any remaining page number patterns from the end
    title_text = re.sub(r'\s*\.\d+\s*$', '', title_text).strip()
    title_text = re.sub(r'\s*\d+\s*$', '', title_text).strip()
    
    # Create simplified structure
    structure = {
        'total_runs': len(all_runs),
        'title_runs': [],
        'tab_runs': [],
        'leader_runs': [],
        'page_number_runs': [],
        'field_runs': [],
        'hyperlink_info': None,
        'run_details': [],
        'extraction_method': 'alternative'
    }
    
    app_logger.debug(f"Extracted TOC title (alternative): '{title_text}'")
    
    return title_text, structure

def get_all_body_elements(document_tree, namespaces):
    """Get all body elements including those in nested structures"""
    # Get direct body children first
    body = document_tree.xpath('.//w:body', namespaces=namespaces)
    if not body:
        return []
    
    # Get all paragraphs and tables, excluding those in textboxes and SDT content
    elements = body[0].xpath('./*[self::w:p or self::w:tbl][not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:txbxContent) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    return elements

def process_paragraph_element(paragraph, content_data, item_id, element_index, numbering_info, styles_info, namespaces):
    """Process a single paragraph element"""
    
    # Enhanced heading detection
    heading_styles = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    is_heading = False
    heading_level = None
    
    if heading_styles:
        style_val = heading_styles[0].get(f'{{{namespaces["w"]}}}val', '')
        if any(pattern in style_val.lower() for pattern in ['heading', 'title', 'caption', 'subtitle']):
            is_heading = True
            level_match = re.search(r'(\d+)', style_val)
            if level_match:
                heading_level = safe_convert_to_int(level_match.group(1))
    
    # Check for numbering
    numbering_props = paragraph.xpath('.//w:numPr', namespaces=namespaces)
    has_numbering = bool(numbering_props)
    paragraph_numbering_info = None
    
    if has_numbering:
        paragraph_numbering_info = extract_paragraph_numbering_info(
            numbering_props[0], numbering_info, namespaces)
    
    # Enhanced TOC detection
    is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
    
    # Extract text excluding textbox content but including page variables
    if is_toc:
        toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
        full_text = toc_title_text
        field_info = None
    else:
        full_text, field_info = extract_paragraph_text_with_variables(paragraph, namespaces, paragraph_numbering_info, True)
        toc_structure = None
    
    if full_text and full_text.strip() and should_translate_enhanced(full_text):
        item_id += 1
        item_data = {
            "id": item_id,
            "count_src": item_id,
            "type": "paragraph",
            "is_heading": is_heading,
            "heading_level": heading_level,
            "has_numbering": has_numbering,
            "numbering_info": paragraph_numbering_info,
            "element_index": element_index,
            "style_info": get_comprehensive_paragraph_style_info(paragraph, namespaces),
            "value": full_text.replace("\n", "␊").replace("\r", "␍"),
            "original_pPr": extract_paragraph_properties(paragraph, namespaces),
            "original_structure": extract_paragraph_structure(paragraph, namespaces)
        }
        
        if field_info:
            item_data["field_info"] = field_info
        
        if is_toc:
            item_data.update({
                "is_toc": True,
                "toc_info": toc_info,
                "toc_structure": toc_structure
            })
        
        content_data.append(item_data)
        app_logger.debug(f"Extracted paragraph {item_id}: '{full_text[:50]}...'")
    
    return item_id

def process_table_element(table, content_data, item_id, element_index, numbering_info, styles_info, namespaces):
    """Process a table element with support for nested tables"""
    
    # Get table properties for format preservation
    table_props = extract_table_properties(table, namespaces)
    
    # Process all rows and cells, including nested tables
    item_id = process_table_rows_recursive(
        table, content_data, item_id, element_index, 
        numbering_info, styles_info, namespaces, table_props
    )
    
    return item_id

def process_table_rows_recursive(table, content_data, item_id, table_index, numbering_info, styles_info, namespaces, table_props, nesting_level=0):
    """Recursively process table rows and handle nested tables"""
    
    rows = table.xpath('./w:tr', namespaces=namespaces)
    
    for row_idx, row in enumerate(rows):
        # Get row properties
        row_props = extract_row_properties(row, namespaces)
        
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        for cell_idx, cell in enumerate(cells):
            # Get cell properties
            cell_props = extract_cell_properties(cell, namespaces)
            
            # Process cell content (paragraphs)
            cell_paragraphs = cell.xpath('./w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                # Enhanced TOC detection for table cells
                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, False)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                    cell_text = toc_title_text
                    cell_field_info = None
                else:
                    cell_text, cell_field_info = extract_paragraph_text_with_variables(cell_paragraph, namespaces, extract_paragraph_numbering_info(cell_paragraph.xpath('.//w:numPr', namespaces=namespaces)[0] if cell_paragraph.xpath('.//w:numPr', namespaces=namespaces) else None, numbering_info, namespaces) if cell_paragraph.xpath('.//w:numPr', namespaces=namespaces) else None, True)
                    toc_structure = None
                
                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                    item_id += 1
                    cell_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "table_cell",
                        "table_index": table_index,
                        "row": row_idx,
                        "col": cell_idx,
                        "paragraph_index": para_idx,
                        "nesting_level": nesting_level,
                        "is_toc": is_toc,
                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                        "table_props": table_props,
                        "row_props": row_props,
                        "cell_props": cell_props,
                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                    }
                    
                    if cell_field_info:
                        cell_data["field_info"] = cell_field_info
                    
                    if is_toc:
                        cell_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(cell_data)
                    app_logger.debug(f"Extracted table cell {item_id}: '{cell_text[:50]}...'")
            
            # Check for nested tables in this cell
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            for nested_table_idx, nested_table in enumerate(nested_tables):
                nested_table_props = extract_table_properties(nested_table, namespaces)
                item_id = process_table_rows_recursive(
                    nested_table, content_data, item_id, 
                    f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}",
                    numbering_info, styles_info, namespaces, 
                    nested_table_props, nesting_level + 1
                )
    
    return item_id

def extract_table_properties(table, namespaces):
    """Extract table properties for format preservation"""
    table_props = {}
    
    # Get table properties element
    tblPr = table.xpath('./w:tblPr', namespaces=namespaces)
    if tblPr:
        table_props['tblPr_xml'] = etree.tostring(tblPr[0], encoding='unicode')
    
    # Get table grid
    tblGrid = table.xpath('./w:tblGrid', namespaces=namespaces)
    if tblGrid:
        table_props['tblGrid_xml'] = etree.tostring(tblGrid[0], encoding='unicode')
    
    return table_props

def extract_row_properties(row, namespaces):
    """Extract row properties for format preservation"""
    row_props = {}
    
    trPr = row.xpath('./w:trPr', namespaces=namespaces)
    if trPr:
        row_props['trPr_xml'] = etree.tostring(trPr[0], encoding='unicode')
    
    return row_props

def extract_cell_properties(cell, namespaces):
    """Extract cell properties for format preservation"""
    cell_props = {}
    
    tcPr = cell.xpath('./w:tcPr', namespaces=namespaces)
    if tcPr:
        cell_props['tcPr_xml'] = etree.tostring(tcPr[0], encoding='unicode')
    
    return cell_props

def extract_paragraph_properties(paragraph, namespaces):
    """Extract paragraph properties for exact format preservation"""
    pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
    if pPr:
        return etree.tostring(pPr[0], encoding='unicode')
    return None

def extract_paragraph_structure(paragraph, namespaces):
    """Extract complete paragraph structure information"""
    structure = {
        'total_runs': 0,
        'runs_info': [],
        'has_fields': False,
        'has_drawings': False
    }
    
    # Get all runs excluding textbox content
    runs = paragraph.xpath('./w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    structure['total_runs'] = len(runs)
    
    for run_idx, run in enumerate(runs):
        run_info = {
            'index': run_idx,
            'has_text': bool(run.xpath('.//w:t', namespaces=namespaces)),
            'has_fields': bool(run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)),
            'has_drawings': bool(run.xpath('.//w:drawing | .//w:pict | .//mc:AlternateContent', namespaces=namespaces)),
            'has_breaks': bool(run.xpath('.//w:br | .//w:cr | .//w:tab', namespaces=namespaces)),
            'rPr_xml': None
        }
        
        # Extract run properties
        rPr = run.xpath('./w:rPr', namespaces=namespaces)
        if rPr:
            run_info['rPr_xml'] = etree.tostring(rPr[0], encoding='unicode')
        
        structure['runs_info'].append(run_info)
        
        if run_info['has_fields']:
            structure['has_fields'] = True
        if run_info['has_drawings']:
            structure['has_drawings'] = True
    
    return structure

def get_comprehensive_paragraph_style_info(paragraph, namespaces):
    """Extract comprehensive style information from paragraph"""
    style_info = {}
    
    # Get paragraph style
    pStyle_nodes = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if pStyle_nodes:
        style_info['paragraph_style'] = pStyle_nodes[0].get(f'{{{namespaces["w"]}}}val', '')
    
    # Get all paragraph properties
    pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
    if pPr:
        # Get justification
        jc_nodes = pPr[0].xpath('.//w:jc', namespaces=namespaces)
        if jc_nodes:
            style_info['justification'] = jc_nodes[0].get(f'{{{namespaces["w"]}}}val', '')
        
        # Get indentation
        ind_nodes = pPr[0].xpath('.//w:ind', namespaces=namespaces)
        if ind_nodes:
            style_info['indentation'] = {}
            for attr in ['left', 'right', 'firstLine', 'hanging']:
                val = ind_nodes[0].get(f'{{{namespaces["w"]}}}{attr}')
                if val:
                    style_info['indentation'][attr] = val
        
        # Get spacing
        spacing_nodes = pPr[0].xpath('.//w:spacing', namespaces=namespaces)
        if spacing_nodes:
            style_info['spacing'] = {}
            for attr in ['before', 'after', 'line', 'lineRule']:
                val = spacing_nodes[0].get(f'{{{namespaces["w"]}}}{attr}')
                if val:
                    style_info['spacing'][attr] = val
    
    return style_info

def process_header_footer_content(hf_tree, content_data, item_id, numbering_info, styles_info, namespaces, hf_type, hf_file, hf_number):
    """Process header/footer content"""
    
    # Process SDT content in header/footer first
    item_id = process_sdt_content(hf_tree, content_data, item_id, numbering_info, styles_info, namespaces)
    
    # Process paragraphs in header/footer
    hf_paragraphs = hf_tree.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    for p_idx, paragraph in enumerate(hf_paragraphs):
        numbering_props = paragraph.xpath('.//w:numPr', namespaces=namespaces)
        paragraph_numbering_info = None
        if numbering_props:
            paragraph_numbering_info = extract_paragraph_numbering_info(
                numbering_props[0], numbering_info, namespaces)
        
        # Enhanced TOC detection for header/footer
        is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
        
        if is_toc:
            toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
            paragraph_text = toc_title_text
            field_info = None
        else:
            paragraph_text, field_info = extract_paragraph_text_with_variables(
                paragraph, namespaces, paragraph_numbering_info, True)
            toc_structure = None
        
        if paragraph_text and paragraph_text.strip() and should_translate_enhanced(paragraph_text):
            item_id += 1
            item_data = {
                "id": item_id,
                "count_src": item_id,
                "type": "header_footer",
                "hf_type": hf_type,
                "hf_file": hf_file,
                "hf_number": hf_number,
                "paragraph_index": p_idx,
                "has_numbering": bool(numbering_props),
                "numbering_info": paragraph_numbering_info,
                "is_toc": is_toc,
                "value": paragraph_text.replace("\n", "␊").replace("\r", "␍"),
                "original_pPr": extract_paragraph_properties(paragraph, namespaces),
                "original_structure": extract_paragraph_structure(paragraph, namespaces)
            }
            
            if field_info:
                item_data["field_info"] = field_info
            
            if is_toc:
                item_data.update({
                    "toc_info": toc_info,
                    "toc_structure": toc_structure
                })
            
            content_data.append(item_data)
    
    # Process textboxes in header/footer
    hf_textbox_items = extract_textbox_content(hf_tree, namespaces)
    for textbox_item in hf_textbox_items:
        item_id += 1
        textbox_item["id"] = item_id
        textbox_item["count_src"] = item_id
        textbox_item["type"] = "header_footer_textbox"
        textbox_item["hf_type"] = hf_type
        textbox_item["hf_file"] = hf_file
        textbox_item["hf_number"] = hf_number
        content_data.append(textbox_item)
    
    # Process tables in header/footer (including nested tables)
    hf_tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    for tbl_idx, table in enumerate(hf_tables):
        table_props = extract_table_properties(table, namespaces)
        item_id = process_header_footer_table_recursive(
            table, content_data, item_id, tbl_idx, numbering_info, styles_info, 
            namespaces, hf_type, hf_file, hf_number, table_props
        )
    
    return item_id

def process_header_footer_table_recursive(table, content_data, item_id, table_index, numbering_info, styles_info, namespaces, hf_type, hf_file, hf_number, table_props, nesting_level=0):
    """Process header/footer tables recursively"""
    
    rows = table.xpath('./w:tr', namespaces=namespaces)
    
    for row_idx, row in enumerate(rows):
        row_props = extract_row_properties(row, namespaces)
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        for cell_idx, cell in enumerate(cells):
            cell_props = extract_cell_properties(cell, namespaces)
            
            # Process cell paragraphs
            cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
            for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                # Enhanced TOC detection for header/footer table cells
                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, False)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                    cell_text = toc_title_text
                    cell_field_info = None
                else:
                    cell_text, cell_field_info = extract_paragraph_text_with_variables(cell_paragraph, namespaces)
                    toc_structure = None
                
                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                    item_id += 1
                    cell_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "header_footer_table_cell",
                        "hf_type": hf_type,
                        "hf_file": hf_file,
                        "hf_number": hf_number,
                        "table_index": table_index,
                        "row": row_idx,
                        "col": cell_idx,
                        "paragraph_index": para_idx,
                        "nesting_level": nesting_level,
                        "is_toc": is_toc,
                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                        "table_props": table_props,
                        "row_props": row_props,
                        "cell_props": cell_props,
                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                    }
                    
                    if cell_field_info:
                        cell_data["field_info"] = cell_field_info
                    
                    if is_toc:
                        cell_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(cell_data)
            
            # Process nested tables
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            for nested_table_idx, nested_table in enumerate(nested_tables):
                nested_table_props = extract_table_properties(nested_table, namespaces)
                item_id = process_header_footer_table_recursive(
                    nested_table, content_data, item_id, 
                    f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}",
                    numbering_info, styles_info, namespaces, 
                    hf_type, hf_file, hf_number, nested_table_props, nesting_level + 1
                )
    
    return item_id

def detect_toc_paragraph(paragraph, namespaces):
    """Enhanced TOC detection that identifies various TOC styles and formats"""
    # Check for TOC styles
    toc_styles = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if toc_styles:
        style_val = toc_styles[0].get(f'{{{namespaces["w"]}}}val', '').lower()
        
        # Common TOC style patterns
        toc_patterns = [
            'toc',           # Standard TOC styles
            'tableofcontents',
            'contents',
            'outline',
            'index'
        ]
        
        if any(pattern in style_val for pattern in toc_patterns):
            toc_info = {
                'style': style_val,
                'level': extract_toc_level_from_style(style_val),
                'detection_method': 'style'
            }
            return True, toc_info
    
    # Check for TOC field codes
    toc_fields = paragraph.xpath('.//w:instrText[contains(text(), "TOC")]', namespaces=namespaces)
    if toc_fields:
        toc_info = {
            'style': 'field_based',
            'level': None,
            'detection_method': 'field',
            'field_instruction': toc_fields[0].text if toc_fields[0].text else ''
        }
        return True, toc_info
    
    # Check for hyperlink-based TOC (common in generated TOCs)
    hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
    if hyperlinks:
        # Look for patterns that suggest this is a TOC entry
        hyperlink = hyperlinks[0]
        anchor = hyperlink.get(f'{{{namespaces["w"]}}}anchor', '')
        
        # TOC hyperlinks often have anchor patterns like _Toc123456 or similar
        if anchor and (anchor.startswith('_Toc') or anchor.startswith('_Ref') or anchor.startswith('bookmark')):
            # Check if the paragraph contains typical TOC elements (dots, page numbers)
            paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
            if has_toc_pattern_enhanced(paragraph_text):
                toc_info = {
                    'style': 'hyperlink_based',
                    'level': detect_toc_level_from_formatting(paragraph, namespaces),
                    'detection_method': 'hyperlink_pattern',
                    'anchor': anchor
                }
                return True, toc_info
    
    # Check for tab and dot leader patterns typical of TOC
    paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
    if has_toc_pattern_enhanced(paragraph_text):
        # Additional check: look for tab characters and indentation
        tabs = paragraph.xpath('.//w:tab', namespaces=namespaces)
        if tabs:
            toc_info = {
                'style': 'pattern_based',
                'level': detect_toc_level_from_formatting(paragraph, namespaces),
                'detection_method': 'pattern_analysis'
            }
            return True, toc_info
    
    return False, None

def extract_toc_level_from_style(style_val):
    """Extract TOC level from style name"""
    # Look for numbers in style name (e.g., TOC1, TOC2, etc.)
    level_match = re.search(r'(\d+)', style_val)
    return safe_convert_to_int(level_match.group(1)) if level_match else 1

def detect_toc_level_from_formatting(paragraph, namespaces):
    """Detect TOC level from paragraph formatting like indentation"""
    ind_elements = paragraph.xpath('.//w:ind', namespaces=namespaces)
    if ind_elements:
        left_indent = ind_elements[0].get(f'{{{namespaces["w"]}}}left', '0')
        try:
            indent_value = safe_convert_to_int(left_indent)
            # Estimate level based on indentation (assuming 720 twips per level)
            level = max(1, (indent_value // 720) + 1)
            return min(level, 9)  # Cap at level 9
        except:
            return 1
    return 1

def extract_paragraph_text_only(paragraph, namespaces):
    """Extract only the text content without processing fields or variables"""
    text_nodes = paragraph.xpath('.//w:t', namespaces=namespaces)
    return ''.join(node.text or '' for node in text_nodes)

def extract_toc_title_with_complete_structure(paragraph, namespaces):
    """Extract only the title text from TOC entry, preserving complete structure for accurate restoration"""
    all_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    structure = {
        'total_runs': len(all_runs),
        'title_runs': [],           # Indices of runs containing title text
        'tab_runs': [],             # Indices of runs containing tabs
        'leader_runs': [],          # Indices of runs containing dot leaders
        'page_number_runs': [],     # Indices of runs containing page numbers
        'field_runs': [],           # Indices of runs containing fields
        'hyperlink_info': None,     # Hyperlink information
        'run_details': []           # Detailed information about each run
    }
    
    title_text = ""
    
    # Check for hyperlink structure
    hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
    if hyperlinks:
        hyperlink = hyperlinks[0]
        structure['hyperlink_info'] = {
            'anchor': hyperlink.get(f'{{{namespaces["w"]}}}anchor', ''),
            'tooltip': hyperlink.get(f'{{{namespaces["w"]}}}tooltip', ''),
            'start_run_index': None,
            'end_run_index': None
        }
        
        # Find which runs are inside the hyperlink
        hyperlink_runs = hyperlink.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        for run in hyperlink_runs:
            if run in all_runs:
                run_index = all_runs.index(run)
                if structure['hyperlink_info']['start_run_index'] is None:
                    structure['hyperlink_info']['start_run_index'] = run_index
                structure['hyperlink_info']['end_run_index'] = run_index
    
    # First pass: identify all runs and their content
    run_contents = []
    for run_idx, run in enumerate(all_runs):
        run_detail = {
            'index': run_idx,
            'type': 'text',           # text, tab, leader, page_number, field
            'content': '',
            'is_in_hyperlink': False,
            'formatting': None
        }
        
        # Check if run is in hyperlink
        if structure['hyperlink_info']:
            start_idx = structure['hyperlink_info']['start_run_index']
            end_idx = structure['hyperlink_info']['end_run_index']
            if start_idx is not None and end_idx is not None:
                if start_idx <= run_idx <= end_idx:
                    run_detail['is_in_hyperlink'] = True
        
        # Get run formatting
        rPr_elements = run.xpath('./w:rPr', namespaces=namespaces)
        if rPr_elements:
            run_detail['formatting'] = etree.tostring(rPr_elements[0], encoding='unicode')
        
        # Check for tabs
        if run.xpath('.//w:tab', namespaces=namespaces):
            run_detail['type'] = 'tab'
            structure['tab_runs'].append(run_idx)
        
        # Check for field codes
        elif run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces):
            run_detail['type'] = 'field'
            structure['field_runs'].append(run_idx)
            
            # Extract field content for analysis
            field_content = ""
            for child in run:
                if child.tag.endswith('fldSimple'):
                    field_content = child.get(f'{{{namespaces["w"]}}}instr', '')
                elif child.tag.endswith('instrText'):
                    field_content = child.text or ''
            run_detail['content'] = field_content
        
        # Check for regular text
        else:
            text_content = ""
            for child in run:
                if child.tag.endswith('t'):
                    text_content += child.text or ""
            
            run_detail['content'] = text_content
            run_contents.append((run_idx, text_content))
        
        structure['run_details'].append(run_detail)
    
    # Second pass: analyze text runs with context
    # Look for page numbers from the end, as they typically appear at the end
    for i in range(len(run_contents) - 1, -1, -1):
        run_idx, text_content = run_contents[i]
        run_detail = structure['run_details'][run_idx]
        
        if text_content.strip():
            # Check if this looks like a page number
            if is_likely_page_number(text_content):
                run_detail['type'] = 'page_number'
                structure['page_number_runs'].append(run_idx)
            elif is_dot_leader(text_content):
                run_detail['type'] = 'leader'
                structure['leader_runs'].append(run_idx)
            else:
                # Check if this is isolated punctuation or numbering that shouldn't be translated
                if is_isolated_punctuation_or_numbering(text_content):
                    run_detail['type'] = 'leader'  # Treat as leader to exclude from title
                    structure['leader_runs'].append(run_idx)
                else:
                    # This is likely title text
                    run_detail['type'] = 'title'
                    structure['title_runs'].append(run_idx)
    
    # Third pass: extract title text in order
    title_runs_sorted = sorted([idx for idx in structure['title_runs']])
    for run_idx in title_runs_sorted:
        run_detail = structure['run_details'][run_idx]
        title_text += run_detail['content']
    
    # Clean up title text
    title_text = title_text.strip()
    title_text = re.sub(r'\s+', ' ', title_text)
    
    app_logger.debug(f"Extracted TOC title: '{title_text}', Structure: {len(structure['title_runs'])} title runs, "
                    f"{len(structure['tab_runs'])} tab runs, {len(structure['page_number_runs'])} page number runs")
    
    return title_text, structure

def is_isolated_punctuation_or_numbering(text):
    """Check if text is isolated punctuation or numbering that shouldn't be part of title"""
    text = text.strip()
    if not text:
        return False
    
    # Single punctuation marks
    if len(text) == 1 and text in '.,;:!?()[]{}"\'-_=+*&^%$#@~`|\\/<>':
        return True
    
    # Multiple dots (leaders)
    if re.match(r'^\.{2,}$', text):
        return True
    
    # Numbers with dots that look like section numbering at the end
    if re.match(r'^\d+\.?$', text) and len(text) <= 4:
        return True
    
    # Page number patterns that might be misclassified
    if re.match(r'^\.\d+$', text):
        return True
    
    return False

def is_likely_page_number(text):
    """Check if text is likely a page number"""
    text = text.strip()
    if not text:
        return False
    
    # Simple number
    if text.isdigit() and 1 <= safe_convert_to_int(text) <= 9999:
        return True
    
    # Roman numerals
    if re.match(r'^[ivxlcdm]+$', text.lower()) or re.match(r'^[IVXLCDM]+$', text):
        return True
    
    # Page number with prefix/suffix (like "- 5 -", "Page 5", etc.)
    if re.match(r'^[-\s]*\d+[-\s]*$', text):
        return True
    
    # Page number with dot prefix (like ".57", ".123")
    if re.match(r'^\.\d+$', text):
        return True
    
    # Page number with various separators (like "...57", "..57", etc.)
    if re.match(r'^\.{2,}\d+$', text):
        return True
    
    # Page number in parentheses or brackets
    if re.match(r'^[\(\[\{]\s*\d+\s*[\)\]\}]$', text):
        return True
    
    # Page number with surrounding dashes or spaces
    if re.match(r'^[-\s\.]*\d+[-\s\.]*$', text):
        return True
    
    return False

def is_dot_leader(text):
    """Check if text consists of dot leaders or similar"""
    text = text.strip()
    if not text:
        return False
    
    # Mostly dots with possible spaces
    dot_count = text.count('.')
    total_chars = len(text.replace(' ', '').replace('\t', ''))
    
    if total_chars > 0 and dot_count / total_chars > 0.7:  # More than 70% dots
        return True
    
    return False

def extract_paragraph_text_with_variables(paragraph, namespaces, numbering_info=None, exclude_textbox_runs=True):
    """Extract paragraph text including page variables but excluding textbox content"""
    full_text = ""
    field_info = []
    
    # Get all runs in the paragraph
    if exclude_textbox_runs:
        all_runs = paragraph.xpath('.//w:r', namespaces=namespaces)
        runs = []
        for run in all_runs:
            # Skip runs inside textboxes
            if run.xpath('ancestor::wps:txbx', namespaces=namespaces):
                continue
            if run.xpath('ancestor::w:txbxContent', namespaces=namespaces):
                continue
            if run.xpath('ancestor::v:textbox', namespaces=namespaces):
                continue
            
            # Skip runs containing textboxes
            if run.xpath('.//w:drawing', namespaces=namespaces):
                continue
            if run.xpath('.//w:pict', namespaces=namespaces):
                continue
            if run.xpath('.//mc:AlternateContent', namespaces=namespaces):
                continue
            
            runs.append(run)
    else:
        runs = paragraph.xpath('.//w:r', namespaces=namespaces)
    
    for run_idx, run in enumerate(runs):
        # Check if this run contains only numbering text
        if is_numbering_run(run, namespaces, numbering_info):
            continue
        
        # Handle field characters and field instructions
        if run.xpath('.//w:fldChar | .//w:instrText', namespaces=namespaces):
            field_result = process_field_run(run, namespaces, run_idx)
            if field_result:
                full_text += field_result['display_text']
                field_info.append(field_result)
            continue
        
        # Handle simple fields
        if run.xpath('.//w:fldSimple', namespaces=namespaces):
            field_result = process_simple_field_run(run, namespaces, run_idx)
            if field_result:
                full_text += field_result['display_text']
                field_info.append(field_result)
            continue
        
        # Process all child elements in order, but only text-related ones
        for child in run:
            tag_name = child.tag.split('}')[-1]  # Get local name without namespace
            
            if tag_name == 't':
                if child.text:
                    full_text += child.text
            elif tag_name == 'br':
                full_text += "\n"
            elif tag_name == 'tab':
                full_text += "\t"
            elif tag_name == 'cr':
                full_text += "\r"
            # Ignore other elements like rPr (run properties), drawing, etc.
    
    # Clean up text by removing leading numbering patterns
    cleaned_text = remove_leading_numbering_patterns(full_text)
    
    return cleaned_text.lstrip(), field_info if field_info else None

def process_field_run(run, namespaces, run_idx):
    """Process a run containing field characters or field instructions"""
    fld_chars = run.xpath('.//w:fldChar', namespaces=namespaces)
    instr_texts = run.xpath('.//w:instrText', namespaces=namespaces)
    
    if fld_chars:
        fld_char_type = fld_chars[0].get(f'{{{namespaces["w"]}}}fldCharType', '')
        if fld_char_type == 'begin':
            return {
                'type': 'field_begin',
                'run_index': run_idx,
                'display_text': '{{FIELD_BEGIN}}',
                'element_info': 'fldChar_begin'
            }
        elif fld_char_type == 'end':
            return {
                'type': 'field_end',
                'run_index': run_idx,
                'display_text': '{{FIELD_END}}',
                'element_info': 'fldChar_end'
            }
        elif fld_char_type == 'separate':
            return {
                'type': 'field_separate',
                'run_index': run_idx,
                'display_text': '{{FIELD_SEP}}',
                'element_info': 'fldChar_separate'
            }
    
    if instr_texts:
        instr_text = instr_texts[0].text if instr_texts[0].text else ''
        # Create a readable placeholder for common field instructions
        if 'PAGE' in instr_text:
            display_text = '{{PAGE_NUMBER}}'
        elif 'NUMPAGES' in instr_text:
            display_text = '{{TOTAL_PAGES}}'
        elif 'DATE' in instr_text:
            display_text = '{{DATE}}'
        elif 'TIME' in instr_text:
            display_text = '{{TIME}}'
        else:
            display_text = f'{{FIELD:{instr_text}}}'
        
        return {
            'type': 'field_instruction',
            'run_index': run_idx,
            'display_text': display_text,
            'element_info': f'instrText:{instr_text}',
            'instruction': instr_text
        }
    
    return None

def process_simple_field_run(run, namespaces, run_idx):
    """Process a run containing simple fields"""
    fld_simples = run.xpath('.//w:fldSimple', namespaces=namespaces)
    
    if fld_simples:
        instr = fld_simples[0].get(f'{{{namespaces["w"]}}}instr', '')
        
        # Create a readable placeholder for common field instructions
        if 'PAGE' in instr:
            display_text = '{{PAGE_NUMBER}}'
        elif 'NUMPAGES' in instr:
            display_text = '{{TOTAL_PAGES}}'
        elif 'DATE' in instr:
            display_text = '{{DATE}}'
        elif 'TIME' in instr:
            display_text = '{{TIME}}'
        else:
            display_text = f'{{FIELD:{instr}}}'
        
        # Store the complete original field element for exact reconstruction
        original_field_xml = etree.tostring(fld_simples[0], encoding='unicode')
        
        return {
            'type': 'simple_field',
            'run_index': run_idx,
            'display_text': display_text,
            'element_info': f'fldSimple:{instr}',
            'instruction': instr,
            'original_field_xml': original_field_xml
        }
    
    return None

def extract_textbox_content(tree, namespaces):
    """Extract content from all textboxes in the document, avoiding duplication"""
    textbox_items = []
    
    # Find all textboxes in the document (both new format and VML fallback)
    wps_textboxes = tree.xpath('.//wps:txbx', namespaces=namespaces)
    vml_textboxes = tree.xpath('.//v:textbox', namespaces=namespaces)
    
    # Process WPS textboxes
    for textbox_idx, textbox in enumerate(wps_textboxes):
        textbox_item = process_single_textbox(textbox, textbox_idx, "wps", tree, namespaces)
        if textbox_item:
            textbox_items.append(textbox_item)
    
    # Process VML textboxes (avoid duplication by checking if they have corresponding WPS version)
    for textbox_idx, textbox in enumerate(vml_textboxes):
        # Check if this is a fallback textbox (has corresponding WPS version)
        parent_alternateContent = textbox.xpath('ancestor::mc:AlternateContent', namespaces=namespaces)
        if parent_alternateContent:
            # This is a fallback, skip it as we already processed the WPS version
            continue
        
        textbox_item = process_single_textbox(textbox, textbox_idx, "vml", tree, namespaces)
        if textbox_item:
            textbox_items.append(textbox_item)
    
    return textbox_items

def process_single_textbox(textbox, textbox_idx, textbox_format, tree, namespaces):
    """Process a single textbox (either WPS or VML format)"""
    # Get textbox content
    if textbox_format == "wps":
        textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    else:  # vml
        textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    
    if not textbox_content:
        return None
    
    # Determine textbox type and positioning
    textbox_type = "inline"
    positioning_info = {}
    paragraph_context = None
    
    # Find parent drawing element
    parent_drawing = textbox.xpath('ancestor::w:drawing', namespaces=namespaces)
    parent_pict = textbox.xpath('ancestor::w:pict', namespaces=namespaces)
    
    if parent_drawing:
        # Check if it's inline or floating
        anchor_elements = parent_drawing[0].xpath('.//wp:anchor', namespaces=namespaces)
        inline_elements = parent_drawing[0].xpath('.//wp:inline', namespaces=namespaces)
        
        if anchor_elements:
            textbox_type = "floating"
            anchor = anchor_elements[0]
            
            # Extract positioning information
            position_h = anchor.xpath('.//wp:positionH', namespaces=namespaces)
            position_v = anchor.xpath('.//wp:positionV', namespaces=namespaces)
            wrap_elements = anchor.xpath('.//wp:wrapSquare | .//wp:wrapTopAndBottom | .//wp:wrapNone', namespaces=namespaces)
            
            if position_h:
                positioning_info['horizontal'] = {
                    'relative_from': position_h[0].get('relativeFrom'),
                    'align': position_h[0].xpath('.//wp:align', namespaces=namespaces)[0].text if position_h[0].xpath('.//wp:align', namespaces=namespaces) else None,
                    'pos_offset': position_h[0].xpath('.//wp:posOffset', namespaces=namespaces)[0].text if position_h[0].xpath('.//wp:posOffset', namespaces=namespaces) else None
                }
            
            if position_v:
                positioning_info['vertical'] = {
                    'relative_from': position_v[0].get('relativeFrom'),
                    'align': position_v[0].xpath('.//wp:align', namespaces=namespaces)[0].text if position_v[0].xpath('.//wp:align', namespaces=namespaces) else None,
                    'pos_offset': position_v[0].xpath('.//wp:posOffset', namespaces=namespaces)[0].text if position_v[0].xpath('.//wp:posOffset', namespaces=namespaces) else None
                }
            
            if wrap_elements:
                wrap_type = wrap_elements[0].tag.split('}')[-1]
                positioning_info['wrap_type'] = wrap_type
        
        # Find the paragraph that contains this textbox
        parent_paragraph = parent_drawing[0].xpath('ancestor::w:p', namespaces=namespaces)
        if parent_paragraph:
            # Find the index of this paragraph in the main document
            all_main_elements = get_all_body_elements(tree, namespaces)
            for elem_idx, elem in enumerate(all_main_elements):
                if elem == parent_paragraph[0]:
                    paragraph_context = elem_idx
                    break
    
    elif parent_pict:
        # VML textbox
        textbox_type = "floating"  # Assume VML textboxes are floating
        
        # Find the paragraph that contains this textbox
        parent_paragraph = parent_pict[0].xpath('ancestor::w:p', namespaces=namespaces)
        if parent_paragraph:
            # Find the index of this paragraph in the main document
            all_main_elements = get_all_body_elements(tree, namespaces)
            for elem_idx, elem in enumerate(all_main_elements):
                if elem == parent_paragraph[0]:
                    paragraph_context = elem_idx
                    break
    
    # Extract text content from textbox
    textbox_paragraphs = textbox_content[0].xpath('.//w:p', namespaces=namespaces)
    textbox_text = ""
    textbox_field_info = []
    
    for para_idx, paragraph in enumerate(textbox_paragraphs):
        # Enhanced TOC detection for textbox content
        is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
        
        if is_toc:
            toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
            para_text = toc_title_text
            para_field_info = None
        else:
            # Extract text from paragraph including variables (don't exclude textbox runs since we're already processing textbox)
            para_text, para_field_info = extract_paragraph_text_with_variables(paragraph, namespaces, None, False)
            toc_structure = None
        
        if para_text:
            textbox_text += para_text
            if para_idx < len(textbox_paragraphs) - 1:
                textbox_text += "\n"
            
            if para_field_info:
                textbox_field_info.extend(para_field_info)
    
    if textbox_text.strip() and should_translate_enhanced(textbox_text):
        textbox_item = {
            "type": "textbox",
            "textbox_type": textbox_type,
            "textbox_format": textbox_format,
            "textbox_index": textbox_idx,
            "positioning_info": positioning_info,
            "paragraph_context": paragraph_context,
            "value": textbox_text.replace("\n", "␊").replace("\r", "␍")
        }
        
        if textbox_field_info:
            textbox_item["field_info"] = textbox_field_info
        
        app_logger.debug(f"Extracted textbox {textbox_idx}: '{textbox_text[:50]}...'")
        return textbox_item
    
    return None

def extract_numbering_translatable_content(numbering_xml, namespaces):
    """Extract translatable content from numbering.xml, preserving variable placeholders"""
    translatable_items = []
    
    if not numbering_xml:
        return translatable_items
    
    try:
        numbering_tree = etree.fromstring(numbering_xml)
        
        # Extract translatable text from abstractNum definitions
        abstract_nums = numbering_tree.xpath('//w:abstractNum', namespaces=namespaces)
        for abstract_num in abstract_nums:
            abstract_num_id = abstract_num.get(f'{{{namespaces["w"]}}}abstractNumId')
            
            # Process each level in the abstract numbering
            levels = abstract_num.xpath('.//w:lvl', namespaces=namespaces)
            for level in levels:
                level_id = level.get(f'{{{namespaces["w"]}}}ilvl')
                
                # Extract lvlText which might contain translatable content
                lvl_text_nodes = level.xpath('.//w:lvlText', namespaces=namespaces)
                for lvl_text_node in lvl_text_nodes:
                    lvl_text_val = lvl_text_node.get(f'{{{namespaces["w"]}}}val', '')
                    
                    # Check if this level text contains translatable content
                    if lvl_text_val and contains_translatable_content(lvl_text_val):
                        # Create special instruction for translator to preserve variables
                        translation_instruction = create_translation_instruction_for_numbering(lvl_text_val)
                        
                        translatable_items.append({
                            "type": "numbering_level_text",
                            "abstract_num_id": abstract_num_id,
                            "level_id": level_id,
                            "original_lvl_text": lvl_text_val,
                            "element_xpath": f'//w:abstractNum[@w:abstractNumId="{abstract_num_id}"]//w:lvl[@w:ilvl="{level_id}"]//w:lvlText',
                            "value": translation_instruction,
                            "preserve_variables": True
                        })
                        app_logger.debug(f"Extracted numbering level text: '{lvl_text_val}' -> '{translation_instruction}'")
                
                # Extract text from w:t elements within the level
                text_nodes = level.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    if text_node.text and text_node.text.strip():
                        text_content = text_node.text.strip()
                        if should_translate_enhanced(text_content):
                            translatable_items.append({
                                "type": "numbering_text_node",
                                "abstract_num_id": abstract_num_id,
                                "level_id": level_id,
                                "text_node_path": get_element_path(text_node, numbering_tree),
                                "original_text": text_content,
                                "value": text_content.replace("\n", "␊").replace("\r", "␍")
                            })
                            app_logger.debug(f"Extracted numbering text node: '{text_content}'")
        
    except Exception as e:
        app_logger.error(f"Error extracting translatable content from numbering.xml: {e}")
    
    return translatable_items

def contains_translatable_content(lvl_text_val):
    """Check if level text contains content that needs translation"""
    if not lvl_text_val:
        return False
    
    # Extract non-variable parts
    text_without_variables = re.sub(r'%\d+', '', lvl_text_val)
    text_without_punctuation = re.sub(r'[.\-:;,()[\]{}]', '', text_without_variables)
    cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation).strip()
    
    if len(cleaned_text) > 0 and not cleaned_text.isdigit():
        has_meaningful_content = any(
            c.isalpha() or 
            '\u4e00' <= c <= '\u9fff' or  # Chinese
            '\u3131' <= c <= '\u318e' or  # Korean Jamo
            '\uac00' <= c <= '\ud7a3' or  # Korean Hangul
            '\u3040' <= c <= '\u309f' or  # Japanese Hiragana
            '\u30a0' <= c <= '\u30ff'     # Japanese Katakana
            for c in cleaned_text
        )
        return has_meaningful_content
    
    return False

def create_translation_instruction_for_numbering(lvl_text_val):
    """Create translation instruction that preserves variable placeholders"""
    if not lvl_text_val:
        return lvl_text_val
    
    variables = re.findall(r'%\d+', lvl_text_val)
    instruction = f"Translate this numbering format while preserving ALL variable placeholders exactly as they are: '{lvl_text_val}'"
    
    if variables:
        variables_str = ", ".join(variables)
        instruction += f" (Keep these variables unchanged: {variables_str})"
    
    return lvl_text_val

def get_element_path(element, root):
    """Get XPath-like path for an element within the document"""
    path = []
    current = element
    
    while current is not None and current != root:
        parent = current.getparent()
        if parent is not None:
            siblings = [child for child in parent if child.tag == current.tag]
            if len(siblings) > 1:
                index = siblings.index(current)
                path.append(f"{current.tag.split('}')[-1]}[{index}]")
            else:
                path.append(current.tag.split('}')[-1])
        else:
            path.append(current.tag.split('}')[-1])
        current = parent
    
    return "/".join(reversed(path))

def parse_numbering_xml(numbering_xml, namespaces):
    """Parse numbering.xml to understand numbering definitions"""
    numbering_info = {}
    
    if not numbering_xml:
        return numbering_info
    
    try:
        numbering_tree = etree.fromstring(numbering_xml)
        
        # Parse abstractNum definitions
        abstract_nums = numbering_tree.xpath('//w:abstractNum', namespaces=namespaces)
        for abstract_num in abstract_nums:
            abstract_num_id = abstract_num.get(f'{{{namespaces["w"]}}}abstractNumId')
            if abstract_num_id:
                numbering_info[f'abstract_{abstract_num_id}'] = {
                    'type': 'abstract',
                    'levels': {}
                }
                
                # Parse levels
                levels = abstract_num.xpath('.//w:lvl', namespaces=namespaces)
                for level in levels:
                    level_id = level.get(f'{{{namespaces["w"]}}}ilvl')
                    if level_id:
                        level_info = {
                            'numFmt': None,
                            'lvlText': None,
                            'start': None
                        }
                        
                        # Get number format
                        numFmt = level.xpath('.//w:numFmt', namespaces=namespaces)
                        if numFmt:
                            level_info['numFmt'] = numFmt[0].get(f'{{{namespaces["w"]}}}val')
                        
                        # Get level text
                        lvlText = level.xpath('.//w:lvlText', namespaces=namespaces)
                        if lvlText:
                            level_info['lvlText'] = lvlText[0].get(f'{{{namespaces["w"]}}}val')
                        
                        # Get start value
                        start = level.xpath('.//w:start', namespaces=namespaces)
                        if start:
                            level_info['start'] = start[0].get(f'{{{namespaces["w"]}}}val')
                        
                        numbering_info[f'abstract_{abstract_num_id}']['levels'][level_id] = level_info
        
        # Parse num definitions
        nums = numbering_tree.xpath('//w:num', namespaces=namespaces)
        for num in nums:
            num_id = num.get(f'{{{namespaces["w"]}}}numId')
            if num_id:
                abstract_num_id_refs = num.xpath('.//w:abstractNumId', namespaces=namespaces)
                if abstract_num_id_refs:
                    abstract_num_id = abstract_num_id_refs[0].get(f'{{{namespaces["w"]}}}val')
                    numbering_info[f'num_{num_id}'] = {
                        'type': 'num',
                        'abstractNumId': abstract_num_id
                    }
        
    except Exception as e:
        app_logger.error(f"Error parsing numbering.xml: {e}")
    
    return numbering_info

def extract_paragraph_numbering_info(numPr_element, numbering_info, namespaces):
    """Extract numbering information from paragraph's numPr element"""
    if numPr_element is None:
        return None
    
    result = {
        'has_numbering': True,
        'numId': None,
        'ilvl': None,
        'numbering_definition': None
    }
    
    # Extract numId
    numId_nodes = numPr_element.xpath('.//w:numId', namespaces=namespaces)
    if numId_nodes:
        result['numId'] = numId_nodes[0].get(f'{{{namespaces["w"]}}}val')
    
    # Extract ilvl (level)
    ilvl_nodes = numPr_element.xpath('.//w:ilvl', namespaces=namespaces)
    if ilvl_nodes:
        result['ilvl'] = ilvl_nodes[0].get(f'{{{namespaces["w"]}}}val')
    
    # Look up numbering definition
    if result['numId'] and numbering_info:
        num_key = f"num_{result['numId']}"
        if num_key in numbering_info:
            abstract_num_id = numbering_info[num_key].get('abstractNumId')
            if abstract_num_id:
                abstract_key = f"abstract_{abstract_num_id}"
                if abstract_key in numbering_info:
                    result['numbering_definition'] = numbering_info[abstract_key]
    
    return result

def is_numbering_run(run, namespaces, numbering_info=None):
    """Check if a run contains only numbering text that should be excluded"""
    if not numbering_info or not numbering_info.get('has_numbering'):
        return False
    
    # Get text from the run
    text_nodes = run.xpath('.//w:t', namespaces=namespaces)
    run_text = ''.join(node.text or '' for node in text_nodes).strip()
    
    if not run_text:
        return False
    
    # Check for common numbering patterns
    numbering_patterns = [
        r'^\d+\.$',  # 1.
        r'^\d+\)$',  # 1)
        r'^[a-zA-Z]\.$',  # a.
        r'^[a-zA-Z]\)$',  # a)
        r'^[ivxlcdm]+\.$',  # i., ii., iii., etc.
        r'^[IVXLCDM]+\.$',  # I., II., III., etc.
        r'^•$',  # bullet
        r'^-$',  # dash
        r'^\*$',  # asterisk
    ]
    
    for pattern in numbering_patterns:
        if re.match(pattern, run_text):
            return True
    
    return False

def remove_leading_numbering_patterns(text):
    """Remove leading numbering patterns from text"""
    if not text:
        return text
    
    # Patterns to remove from the beginning of text
    patterns = [
        r'^\d+\.\s*',  # 1. 
        r'^\d+\)\s*',  # 1) 
        r'^[a-zA-Z]\.\s*',  # a. 
        r'^[a-zA-Z]\)\s*',  # a) 
        r'^[ivxlcdm]+\.\s*',  # i., ii., iii., etc.
        r'^[IVXLCDM]+\.\s*',  # I., II., III., etc.
        r'^•\s*',  # bullet
        r'^-\s*',  # dash
        r'^\*\s*',  # asterisk
    ]
    
    for pattern in patterns:
        text = re.sub(pattern, '', text, count=1)
    
    return text

def should_translate_enhanced(text):
    """Enhanced translation check - more inclusive than original"""
    if not text or not text.strip():
        return False
    
    # Remove field placeholders for analysis
    clean_text = text.strip()
    clean_text = re.sub(r'\{\{[^}]+\}\}', '', clean_text)
    clean_text = clean_text.strip()
    
    # Skip very short text (likely symbols or numbers only)
    if len(clean_text) < 1:
        return False
    
    # Skip pure numbers
    if clean_text.isdigit():
        return False
    
    # Skip pure punctuation
    if all(c in '.,;:!?()[]{}"\'-_=+*&^%$#@~`|\\/<>' for c in clean_text):
        return False
    
    # Check if text contains meaningful content (letters, CJK characters, etc.)
    has_meaningful_content = any(c.isalpha() or '\u4e00' <= c <= '\u9fff' or '\u3131' <= c <= '\u318e' or '\uac00' <= c <= '\ud7a3' for c in clean_text)
    
    if has_meaningful_content:
        return True
    
    # Fallback to original should_translate if available
    try:
        return should_translate(text)
    except:
        return True  # Default to translate if function fails

def write_translated_content_to_word(file_path, original_json_path, translated_json_path):
    """Write translated content back to Word document with complete file structure preservation"""
    
    # Load translation data
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    translations = {}
    for item in translated_data:
        item_id = str(item.get("id", item.get("count_src")))
        if item_id and "translated" in item:
            translations[item_id] = item["translated"]
    
    # Get temp directory path
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    temp_dir_info_path = os.path.join(temp_folder, "temp_dir_path.txt")
    
    temp_dir = None
    if os.path.exists(temp_dir_info_path):
        with open(temp_dir_info_path, "r", encoding="utf-8") as f:
            temp_dir = f.read().strip()
    
    if not temp_dir or not os.path.exists(temp_dir):
        # Fallback: create new temp directory
        temp_dir = tempfile.mkdtemp()
        with ZipFile(file_path, 'r') as docx:
            docx.extractall(temp_dir)
    
    try:
        # Complete namespaces including SmartArt
        namespaces = {
            'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
            'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
            'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
            'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'v': 'urn:schemas-microsoft-com:vml',
            'w10': 'urn:schemas-microsoft-com:office:word',
            'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
            'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
            'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
            'wpc': 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas',
            'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',
            'wpi': 'http://schemas.microsoft.com/office/word/2010/wordprocessingInk',
            'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
            'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
            'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram'
        }
        
        # Load and update main document
        document_xml_path = os.path.join(temp_dir, 'word', 'document.xml')
        with open(document_xml_path, 'rb') as f:
            document_xml = f.read()
        document_tree = etree.fromstring(document_xml)
        
        # Load and update numbering.xml if exists
        numbering_tree = None
        numbering_xml_path = os.path.join(temp_dir, 'word', 'numbering.xml')
        if os.path.exists(numbering_xml_path):
            with open(numbering_xml_path, 'rb') as f:
                numbering_xml = f.read()
            numbering_tree = etree.fromstring(numbering_xml)
            numbering_info = parse_numbering_xml(numbering_xml, namespaces)
            update_numbering_xml_with_translations(numbering_tree, original_data, translations, namespaces)
        
        # Load header/footer files
        header_footer_trees = {}
        word_dir = os.path.join(temp_dir, 'word')
        if os.path.exists(word_dir):
            for filename in os.listdir(word_dir):
                if filename.startswith('header') or filename.startswith('footer'):
                    filepath = os.path.join(word_dir, filename)
                    with open(filepath, 'rb') as f:
                        hf_content = f.read()
                    header_footer_trees[f'word/{filename}'] = etree.fromstring(hf_content)
        
        # Apply SmartArt translations
        update_smartart_with_translations(temp_dir, original_data, translations, namespaces)
        
        # Get all SDT elements
        all_sdt_elements = document_tree.xpath('.//w:sdt', namespaces=namespaces)
        
        # Get all document elements
        all_main_elements = get_all_body_elements(document_tree, namespaces)
        
        # Get all textboxes for processing
        all_wps_textboxes = document_tree.xpath('.//wps:txbx', namespaces=namespaces)
        all_vml_textboxes = document_tree.xpath('.//v:textbox', namespaces=namespaces)

        # Process translations
        for item in original_data:
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if not translated_text:
                continue
                
            # Skip numbering and SmartArt items as they're handled separately
            if item["type"] in ["numbering_level_text", "numbering_text_node", "smartart"]:
                continue
                
            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
            
            if item["type"] == "sdt_paragraph":
                update_sdt_paragraph_with_enhanced_preservation(
                    item, translated_text, all_sdt_elements, namespaces
                )
            
            elif item["type"] == "sdt_table_cell":
                update_sdt_table_cell_with_enhanced_preservation(
                    item, translated_text, all_sdt_elements, namespaces
                )
            
            elif item["type"] == "paragraph":
                update_paragraph_with_enhanced_preservation(
                    item, translated_text, all_main_elements, namespaces
                )
                
            elif item["type"] == "table_cell":
                update_table_cell_with_enhanced_preservation(
                    item, translated_text, all_main_elements, namespaces
                )
            
            elif item["type"] == "textbox":
                update_textbox_with_enhanced_preservation(
                    item, translated_text, all_wps_textboxes, all_vml_textboxes, namespaces
                )
            
            elif item["type"] == "header_footer":
                update_header_footer_paragraph_with_enhanced_preservation(
                    item, translated_text, header_footer_trees, namespaces
                )
            
            elif item["type"] == "header_footer_textbox":
                update_header_footer_textbox_with_enhanced_preservation(
                    item, translated_text, header_footer_trees, namespaces
                )
            
            elif item["type"] == "header_footer_table_cell":
                update_header_footer_table_cell_with_enhanced_preservation(
                    item, translated_text, header_footer_trees, namespaces
                )

        # Save all modified files back to temp directory
        with open(document_xml_path, "wb") as f:
            f.write(etree.tostring(document_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
        
        if numbering_tree is not None:
            with open(numbering_xml_path, "wb") as f:
                f.write(etree.tostring(numbering_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
        
        for hf_file, hf_tree in header_footer_trees.items():
            hf_path = os.path.join(temp_dir, hf_file.replace('/', os.sep))
            with open(hf_path, "wb") as f:
                f.write(etree.tostring(hf_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

        # Create result file
        result_folder = "result"
        os.makedirs(result_folder, exist_ok=True)
        result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.docx")

        # Create new DOCX file with all original files preserved
        with ZipFile(result_path, 'w', ZIP_DEFLATED) as new_doc:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir).replace(os.sep, '/')
                    new_doc.write(file_path, arcname)

        app_logger.info(f"Translated Word document saved to: {result_path}")
        return result_path
        
    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

def update_smartart_with_translations(temp_dir, original_data, translations, namespaces):
    """Update SmartArt diagrams with translated content"""
    # Get SmartArt items
    smartart_items = [item for item in original_data if item['type'] == 'smartart']
    
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
        drawing_path = f"word/diagrams/drawing{diagram_index}.xml"
        data_path = f"word/diagrams/data{diagram_index}.xml"
        
        # Process drawing file
        try:
            drawing_file_path = os.path.join(temp_dir, drawing_path.replace('/', os.sep))
            if os.path.exists(drawing_file_path):
                with open(drawing_file_path, 'rb') as f:
                    drawing_xml = f.read()
                drawing_tree = etree.fromstring(drawing_xml)
                
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
                                distribute_smartart_text_to_runs(paragraph, translated_text, item, namespaces)
                                app_logger.info(f"Updated SmartArt drawing text for diagram {diagram_index}, shape {item['shape_index']}")
                
                # Save modified drawing
                with open(drawing_file_path, "wb") as f:
                    f.write(etree.tostring(drawing_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified SmartArt drawing file: {drawing_path}")
                                                        
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {drawing_path}: {e}")
            continue
        
        # Process corresponding data file
        try:
            data_file_path = os.path.join(temp_dir, data_path.replace('/', os.sep))
            if os.path.exists(data_file_path):
                with open(data_file_path, 'rb') as f:
                    data_xml = f.read()
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
                                point_run_info = process_smartart_text_runs(point_text_runs, namespaces)
                                # If the original text matches, update this paragraph
                                if point_run_info['merged_text'].strip() == original_text.strip():
                                    distribute_smartart_text_to_runs(point_paragraph, translated_text, item, namespaces)
                                    app_logger.info(f"Updated SmartArt data text for diagram {diagram_index}: '{original_text}' -> '{translated_text[:50]}...'")
                                    break
                
                # Save modified data
                with open(data_file_path, "wb") as f:
                    f.write(etree.tostring(data_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified SmartArt data file: {data_path}")
                                                     
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {data_path}: {e}")
            continue

def distribute_smartart_text_to_runs(paragraph, translated_text, item, namespaces):
    """Distribute translated text across SmartArt runs, preserving spacing and structure"""
    text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
    
    if not text_runs:
        return
    
    original_run_texts = item.get('run_texts', [])
    original_run_lengths = item.get('run_lengths', [])
    
    # If we don't have the original structure, fallback to simple distribution
    if not original_run_texts or len(original_run_texts) != len(text_runs):
        app_logger.warning(f"Mismatch in SmartArt run structure, using simple distribution")
        simple_smartart_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Use intelligent distribution based on original structure
    intelligent_smartart_text_distribution(text_runs, translated_text, original_run_texts, original_run_lengths, namespaces)

def simple_smartart_text_distribution(text_runs, translated_text, namespaces):
    """Simple fallback distribution method for SmartArt"""
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

def intelligent_smartart_text_distribution(text_runs, translated_text, original_run_texts, 
                                         original_run_lengths, namespaces):
    """Intelligent text distribution for SmartArt that preserves spacing and structure"""
    
    # Calculate total length excluding empty runs
    meaningful_runs = [(i, length) for i, length in enumerate(original_run_lengths) if length > 0]
    total_meaningful_length = sum(length for _, length in meaningful_runs)
    
    if total_meaningful_length == 0:
        simple_smartart_text_distribution(text_runs, translated_text, namespaces)
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

def update_sdt_paragraph_with_enhanced_preservation(item, translated_text, all_sdt_elements, namespaces):
    """Update SDT paragraph with enhanced format preservation"""
    try:
        sdt_index = item.get("sdt_index")
        paragraph_index = item.get("paragraph_index")
        
        if sdt_index is None or sdt_index >= len(all_sdt_elements):
            app_logger.error(f"Invalid SDT index: {sdt_index}")
            return
        
        sdt = all_sdt_elements[sdt_index]
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        
        if not sdt_content:
            app_logger.error(f"No SDT content found for index: {sdt_index}")
            return
        
        paragraphs = sdt_content[0].xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        
        if paragraph_index >= len(paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in SDT")
            return
        
        paragraph = paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Updated SDT paragraph {sdt_index}.{paragraph_index}")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating SDT paragraph: {e}")

def update_sdt_table_cell_with_enhanced_preservation(item, translated_text, all_sdt_elements, namespaces):
    """Update SDT table cell with enhanced format preservation"""
    try:
        sdt_index = item.get("sdt_index")
        table_index = item.get("table_index")
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        if sdt_index is None or sdt_index >= len(all_sdt_elements):
            app_logger.error(f"Invalid SDT index: {sdt_index}")
            return
        
        sdt = all_sdt_elements[sdt_index]
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        
        if not sdt_content:
            app_logger.error(f"No SDT content found for index: {sdt_index}")
            return
        
        # Handle nested table indices
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            update_sdt_nested_table_cell_with_enhanced_preservation(
                item, translated_text, sdt_content[0], namespaces
            )
            return
        
        tables = sdt_content[0].xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        
        if table_index >= len(tables):
            app_logger.error(f"Table index {table_index} out of bounds in SDT")
            return
        
        table = tables[table_index]
        rows = table.xpath('./w:tr', namespaces=namespaces)
        
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds in SDT table")
            return
        
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds in SDT table")
            return
        
        cell = cells[col_idx]
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in SDT table cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Updated SDT table cell {sdt_index}.{table_index}.{row_idx}.{col_idx}.{paragraph_index}")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating SDT table cell: {e}")

def update_sdt_nested_table_cell_with_enhanced_preservation(item, translated_text, sdt_content, namespaces):
    """Update nested table cell within SDT with enhanced format preservation"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        nested_path = "_nested_".join(parts[1:]).split("_")
        
        tables = sdt_content.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        if parent_table_index >= len(tables):
            app_logger.error(f"Invalid parent table index: {parent_table_index}")
            return
        
        parent_table = tables[parent_table_index]
        
        # Navigate to nested table
        current_table = parent_table
        for i in range(0, len(nested_path), 3):  # row, col, nested_index
            if i + 2 >= len(nested_path):
                break
                
            parent_row_idx = safe_convert_to_int(nested_path[i])
            parent_col_idx = safe_convert_to_int(nested_path[i + 1])
            nested_table_idx = safe_convert_to_int(nested_path[i + 2])
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds")
                return
            
            current_table = nested_tables[nested_table_idx]
        
        # Now update the cell in the nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Nested table final row index {row_idx} out of bounds")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Nested table final col index {col_idx} out of bounds")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in nested cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating SDT nested table cell: {e}")

def update_paragraph_with_enhanced_preservation(item, translated_text, all_main_elements, namespaces):
    """Update paragraph with enhanced format preservation"""
    try:
        element_index = item.get("element_index")
        if element_index is None or element_index >= len(all_main_elements):
            app_logger.error(f"Invalid element index: {element_index}")
            return
            
        paragraph = all_main_elements[element_index]
        
        if paragraph.tag.split('}')[-1] != 'p':
            app_logger.error(f"Element at index {element_index} is not a paragraph")
            return
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(paragraph, translated_text, namespaces, toc_structure)
        else:
            numbering_info_item = item.get("numbering_info")
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                paragraph, translated_text, namespaces, numbering_info_item, field_info, original_structure
            )
            
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating paragraph with index {item.get('element_index')}: {e}")

def update_table_cell_with_enhanced_preservation(item, translated_text, all_main_elements, namespaces):
    """Update table cell with enhanced format preservation"""
    try:
        table_index = item.get("table_index")
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            # Handle nested table
            update_nested_table_cell_with_enhanced_preservation(
                item, translated_text, all_main_elements, namespaces
            )
            return
        
        if table_index is None or table_index >= len(all_main_elements):
            app_logger.error(f"Invalid table index: {table_index}")
            return
        
        table = all_main_elements[table_index]
        
        if table.tag.split('}')[-1] != 'tbl':
            app_logger.error(f"Element at index {table_index} is not a table")
            return
        
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
            
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating table cell: {e}")

def update_nested_table_cell_with_enhanced_preservation(item, translated_text, all_main_elements, namespaces):
    """Update nested table cell with enhanced format preservation"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        nested_path = "_nested_".join(parts[1:]).split("_")
        
        if parent_table_index >= len(all_main_elements):
            app_logger.error(f"Invalid parent table index: {parent_table_index}")
            return
        
        parent_table = all_main_elements[parent_table_index]
        
        # Navigate to nested table
        current_table = parent_table
        for i in range(0, len(nested_path), 3):  # row, col, nested_index
            if i + 2 >= len(nested_path):
                break
                
            parent_row_idx = safe_convert_to_int(nested_path[i])
            parent_col_idx = safe_convert_to_int(nested_path[i + 1])
            nested_table_idx = safe_convert_to_int(nested_path[i + 2])
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds")
                return
            
            current_table = nested_tables[nested_table_idx]
        
        # Now update the cell in the nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Nested table final row index {row_idx} out of bounds")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Nested table final col index {col_idx} out of bounds")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in nested cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating nested table cell: {e}")

def update_textbox_with_enhanced_preservation(item, translated_text, all_wps_textboxes, all_vml_textboxes, namespaces):
    """Update textbox with enhanced format preservation"""
    try:
        textbox_index = item.get("textbox_index")
        textbox_format = item.get("textbox_format", "wps")
        
        if textbox_format == "wps":
            if textbox_index is None or textbox_index >= len(all_wps_textboxes):
                app_logger.error(f"Invalid WPS textbox index: {textbox_index}")
                return
            textbox = all_wps_textboxes[textbox_index]
        else:  # vml
            if textbox_index is None or textbox_index >= len(all_vml_textboxes):
                app_logger.error(f"Invalid VML textbox index: {textbox_index}")
                return
            textbox = all_vml_textboxes[textbox_index]
        
        field_info = item.get("field_info")
        update_textbox_content_with_enhanced_preservation(textbox, translated_text, namespaces, field_info)
        app_logger.info(f"Updated textbox {textbox_index} with translated text: '{translated_text[:50]}...'")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating textbox: {e}")

def update_header_footer_paragraph_with_enhanced_preservation(item, translated_text, header_footer_trees, namespaces):
    """Update header/footer paragraph with enhanced format preservation"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        p_idx = item.get("paragraph_index")
        
        paragraphs = hf_tree.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if p_idx >= len(paragraphs):
            app_logger.error(f"Paragraph index {p_idx} out of bounds in {hf_file}")
            return
        
        paragraph = paragraphs[p_idx]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(paragraph, translated_text, namespaces, toc_structure)
        else:
            numbering_info_item = item.get("numbering_info")
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                paragraph, translated_text, namespaces, numbering_info_item, field_info, original_structure
            )
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating header/footer paragraph: {e}")

def update_header_footer_textbox_with_enhanced_preservation(item, translated_text, header_footer_trees, namespaces):
    """Update header/footer textbox with enhanced format preservation"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        textbox_index = item.get("textbox_index")
        textbox_format = item.get("textbox_format", "wps")
        
        if textbox_format == "wps":
            hf_textboxes = hf_tree.xpath('.//wps:txbx', namespaces=namespaces)
        else:  # vml
            hf_textboxes = hf_tree.xpath('.//v:textbox', namespaces=namespaces)
        
        if textbox_index >= len(hf_textboxes):
            app_logger.error(f"Textbox index {textbox_index} out of bounds in {hf_file}")
            return
        
        textbox = hf_textboxes[textbox_index]
        field_info = item.get("field_info")
        update_textbox_content_with_enhanced_preservation(textbox, translated_text, namespaces, field_info)
        app_logger.info(f"Updated header/footer textbox {textbox_index} with translated text: '{translated_text[:50]}...'")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating header/footer textbox: {e}")

def update_header_footer_table_cell_with_enhanced_preservation(item, translated_text, header_footer_trees, namespaces):
    """Update header/footer table cell with enhanced format preservation"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        
        # Handle nested tables in header/footer
        table_index = item.get("table_index")
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            update_header_footer_nested_table_cell_with_enhanced_preservation(
                item, translated_text, hf_tree, namespaces
            )
            return
        
        tbl_idx = safe_convert_to_int(table_index) if isinstance(table_index, str) else table_index
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if tbl_idx >= len(tables):
            app_logger.error(f"Table index {tbl_idx} out of bounds in {hf_file}")
            return
        
        table = tables[tbl_idx]
        rows = table.xpath('./w:tr', namespaces=namespaces)
        
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds in table in {hf_file}")
            return
        
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds in table in {hf_file}")
            return
        
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in header/footer cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating header/footer table cell: {e}")

def update_header_footer_nested_table_cell_with_enhanced_preservation(item, translated_text, hf_tree, namespaces):
    """Update header/footer nested table cell with enhanced format preservation"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        nested_path = "_nested_".join(parts[1:]).split("_")
        
        tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if parent_table_index >= len(tables):
            app_logger.error(f"Invalid parent table index: {parent_table_index}")
            return
        
        parent_table = tables[parent_table_index]
        
        # Navigate to nested table
        current_table = parent_table
        for i in range(0, len(nested_path), 3):  # row, col, nested_index
            if i + 2 >= len(nested_path):
                break
                
            parent_row_idx = safe_convert_to_int(nested_path[i])
            parent_col_idx = safe_convert_to_int(nested_path[i + 1])
            nested_table_idx = safe_convert_to_int(nested_path[i + 2])
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds")
                return
            
            current_table = nested_tables[nested_table_idx]
        
        # Now update the cell in the nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Nested table final row index {row_idx} out of bounds")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Nested table final col index {col_idx} out of bounds")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in nested cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_complete_structure(target_paragraph, translated_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_enhanced_preservation(
                target_paragraph, translated_text, namespaces, None, field_info, original_structure
            )
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating header/footer nested table cell: {e}")

def restore_paragraph_properties(paragraph, original_pPr_xml, namespaces):
    """Restore original paragraph properties from XML"""
    if not original_pPr_xml:
        return
    
    try:
        # Remove existing pPr if any
        existing_pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
        for pPr in existing_pPr:
            paragraph.remove(pPr)
        
        # Parse and insert the original pPr
        original_pPr = etree.fromstring(original_pPr_xml)
        paragraph.insert(0, original_pPr)
        
    except Exception as e:
        app_logger.error(f"Error restoring paragraph properties: {e}")

def update_paragraph_text_with_enhanced_preservation(paragraph, new_text, namespaces, numbering_info=None, field_info=None, original_structure=None):
    """Update paragraph text with enhanced format preservation using original structure"""
    
    # Find all runs that are direct children of the paragraph
    all_runs = paragraph.xpath('./w:r', namespaces=namespaces)
    
    text_runs = []
    drawing_runs = []
    preserved_runs = []
    
    for run in all_runs:
        # Identify runs containing drawings (textboxes) - keep these
        if run.xpath('.//w:drawing | .//w:pict | .//mc:AlternateContent', namespaces=namespaces):
            drawing_runs.append(run)
            preserved_runs.append(run)
            continue
        
        # If we have field_info, we will regenerate all fields, so treat field runs as text runs to be removed
        if field_info and run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces):
            text_runs.append(run)
            continue
        
        # Identify field runs - keep these only if no field_info to regenerate
        if not field_info and run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces):
            preserved_runs.append(run)
            continue
        
        # Identify numbering runs if this is a numbered paragraph - keep these
        if numbering_info and numbering_info.get('has_numbering') and is_numbering_run(run, namespaces, numbering_info):
            preserved_runs.append(run)
            continue
        
        # This is a text run that needs to be replaced
        text_runs.append(run)
    
    # Get formatting from the first text run if available, or use original structure
    formatting = None
    if original_structure and original_structure.get('runs_info'):
        # Find the first text run with formatting
        for run_info in original_structure['runs_info']:
            if run_info.get('has_text') and run_info.get('rPr_xml'):
                try:
                    formatting = etree.fromstring(run_info['rPr_xml'])
                    break
                except:
                    pass
    
    if formatting is None and text_runs:
        # Fallback to first text run formatting
        first_run = text_runs[0]
        rPr_elements = first_run.xpath('./w:rPr', namespaces=namespaces)
        formatting = rPr_elements[0] if rPr_elements else None
    
    # Remove only the text runs, keep everything else
    for run in text_runs:
        paragraph.remove(run)
    
    # Add new text content with proper structure
    if field_info:
        update_paragraph_content_with_fields_enhanced(
            paragraph, new_text, namespaces, field_info, formatting, original_structure
        )
    else:
        add_text_with_enhanced_formatting(paragraph, new_text, namespaces, formatting)

def update_paragraph_content_with_fields_enhanced(paragraph, new_text, namespaces, field_info, formatting=None, original_structure=None):
    """Update paragraph content while preserving field variables and line breaks with enhanced formatting"""
    
    # Parse the new text to find field placeholders
    field_placeholders = re.findall(r'\{\{[^}]+\}\}', new_text)
    
    if not field_placeholders:
        # No field placeholders, just add text with line breaks
        add_text_with_enhanced_formatting(paragraph, new_text, namespaces, formatting)
        return
    
    # Create a mapping of field placeholders to field info with position tracking
    field_mapping = {}
    field_counters = {}
    
    for field in field_info:
        display_text = field.get('display_text', '')
        if display_text in field_placeholders:
            if display_text not in field_counters:
                field_counters[display_text] = 0
            
            key = f"{display_text}_{field_counters[display_text]}"
            field_mapping[key] = field
            field_counters[display_text] += 1
    
    # For repeated field placeholders that exceed available field info, reuse the first field info
    for placeholder in set(field_placeholders):
        placeholder_count = field_placeholders.count(placeholder)
        available_count = field_counters.get(placeholder, 0)
        
        if placeholder_count > available_count:
            # Find the first field info for this placeholder
            base_field_info = None
            for field in field_info:
                if field.get('display_text', '') == placeholder:
                    base_field_info = field
                    break
            
            if base_field_info:
                # Add mapping for additional occurrences
                for i in range(available_count, placeholder_count):
                    key = f"{placeholder}_{i}"
                    field_mapping[key] = base_field_info
    
    # Split text by both field placeholders and line breaks
    lines = new_text.split('\n')
    
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            # Add line break before each line except the first
            br_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
            if formatting is not None:
                cloned_rPr = etree.fromstring(etree.tostring(formatting))
                br_run.insert(0, cloned_rPr)
            etree.SubElement(br_run, f"{{{namespaces['w']}}}br")
        
        # Process field placeholders within this line
        if any(placeholder in line for placeholder in field_placeholders):
            # Split line by field placeholders and rebuild with proper field elements
            parts = re.split(r'(\{\{[^}]+\}\})', line)
            
            current_run = None
            field_usage = {}
            
            for part in parts:
                if part in field_placeholders:
                    # Track field usage to handle multiple occurrences
                    if part not in field_usage:
                        field_usage[part] = 0
                    
                    key = f"{part}_{field_usage[part]}"
                    field_usage[part] += 1
                    
                    if key in field_mapping:
                        field = field_mapping[key]
                        field_type = field.get('type')
                        
                        if field_type == 'simple_field':
                            # Create simple field using original XML
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            
                            original_field_xml = field.get('original_field_xml')
                            if original_field_xml:
                                try:
                                    field_element = etree.fromstring(original_field_xml)
                                    current_run.append(field_element)
                                except:
                                    # Fallback to manual creation
                                    fld_simple = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldSimple")
                                    fld_simple.set(f'{{{namespaces["w"]}}}instr', field.get('instruction', ''))
                            else:
                                # Manual creation
                                fld_simple = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldSimple")
                                fld_simple.set(f'{{{namespaces["w"]}}}instr', field.get('instruction', ''))
                        
                        elif field_type == 'field_begin':
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'begin')
                            
                        elif field_type == 'field_instruction':
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            
                            instr_text = etree.SubElement(current_run, f"{{{namespaces['w']}}}instrText")
                            instr_text.text = field.get('instruction', '')
                            
                        elif field_type == 'field_end':
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'end')
                            
                        elif field_type == 'field_separate':
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'separate')
                        
                        # Apply formatting if available
                        if formatting is not None and current_run is not None:
                            existing_rPr = current_run.xpath('./w:rPr', namespaces=namespaces)
                            for rPr in existing_rPr:
                                current_run.remove(rPr)
                            cloned_rPr = etree.fromstring(etree.tostring(formatting))
                            current_run.insert(0, cloned_rPr)
                        
                        current_run = None  # Force new run for next content
                    
                elif part.strip() or part == '':
                    # This is regular text (including empty strings to preserve structure)
                    if current_run is None:
                        current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                    
                    text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                    text_node.text = part
                    
                    # Apply formatting if available
                    if formatting is not None:
                        existing_rPr = current_run.xpath('./w:rPr', namespaces=namespaces)
                        for rPr in existing_rPr:
                            current_run.remove(rPr)
                        cloned_rPr = etree.fromstring(etree.tostring(formatting))
                        current_run.insert(0, cloned_rPr)
        else:
            # No field placeholders in this line, just add as regular text
            if line or line_idx == 0:  # Always add first line, add others only if not empty
                text_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                if formatting is not None:
                    cloned_rPr = etree.fromstring(etree.tostring(formatting))
                    text_run.insert(0, cloned_rPr)
                text_node = etree.SubElement(text_run, f"{{{namespaces['w']}}}t")
                text_node.text = line

def add_text_with_enhanced_formatting(paragraph, text, namespaces, formatting=None):
    """Add text to paragraph while preserving line breaks and enhanced formatting"""
    # Split text by line breaks but preserve the structure
    parts = text.split('\n')
    
    for i, part in enumerate(parts):
        if i > 0:
            # Add line break before each part except the first
            br_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
            if formatting is not None:
                cloned_rPr = etree.fromstring(etree.tostring(formatting))
                br_run.insert(0, cloned_rPr)
            etree.SubElement(br_run, f"{{{namespaces['w']}}}br")
        
        # Add text part (including empty strings to preserve structure)
        if part or i == 0:  # Always add first part, add others only if not empty
            text_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
            if formatting is not None:
                cloned_rPr = etree.fromstring(etree.tostring(formatting))
                text_run.insert(0, cloned_rPr)
            text_node = etree.SubElement(text_run, f"{{{namespaces['w']}}}t")
            text_node.text = part

def update_textbox_content_with_enhanced_preservation(textbox, new_text, namespaces, field_info=None):
    """Update textbox content with enhanced format preservation"""
    textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    if not textbox_content:
        app_logger.error("No textbox content found")
        return
    
    # Get original formatting from existing paragraphs before clearing
    original_formatting = None
    existing_paragraphs = textbox_content[0].xpath('./w:p', namespaces=namespaces)
    if existing_paragraphs:
        first_p = existing_paragraphs[0]
        first_runs = first_p.xpath('./w:r', namespaces=namespaces)
        if first_runs:
            rPr_elements = first_runs[0].xpath('./w:rPr', namespaces=namespaces)
            if rPr_elements:
                original_formatting = rPr_elements[0]
    
    # Clear all existing paragraphs
    for child in list(textbox_content[0]):
        textbox_content[0].remove(child)
    
    # Handle multi-line text
    if "\n" in new_text:
        text_lines = new_text.split("\n")
        for line_idx, line in enumerate(text_lines):
            # Create new paragraph
            new_p = etree.SubElement(textbox_content[0], f"{{{namespaces['w']}}}p")
            
            # Process line with field variables
            if field_info:
                update_paragraph_content_with_fields_enhanced(
                    new_p, line, namespaces, field_info, original_formatting, None
                )
            else:
                add_text_with_enhanced_formatting(new_p, line, namespaces, original_formatting)
    else:
        # Single line text - create one paragraph
        new_p = etree.SubElement(textbox_content[0], f"{{{namespaces['w']}}}p")
        
        # Process text with field variables
        if field_info:
            update_paragraph_content_with_fields_enhanced(
                new_p, new_text, namespaces, field_info, original_formatting, None
            )
        else:
            add_text_with_enhanced_formatting(new_p, new_text, namespaces, original_formatting)

def update_toc_paragraph_with_complete_structure(paragraph, translated_title, namespaces, toc_structure):
    """Update TOC paragraph using complete structure information for exact preservation"""
    if not toc_structure or not toc_structure.get('run_details'):
        app_logger.warning("No TOC structure available, falling back to simple replacement")
        update_toc_paragraph_fallback(paragraph, translated_title, namespaces)
        return
    
    run_details = toc_structure['run_details']
    total_runs = toc_structure.get('total_runs', 0)
    
    # Get all current runs in the paragraph
    current_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    if len(current_runs) != total_runs:
        app_logger.warning(f"Run count mismatch: expected {total_runs}, found {len(current_runs)}")
        update_toc_paragraph_fallback(paragraph, translated_title, namespaces)
        return
    
    # Update only the title runs with the translated text
    title_run_indices = [detail['index'] for detail in run_details if detail['type'] == 'title']
    
    if not title_run_indices:
        app_logger.warning("No title runs found in TOC structure")
        update_toc_paragraph_fallback(paragraph, translated_title, namespaces)
        return
    
    try:
        # Clear text content from title runs
        for run_idx in title_run_indices:
            if run_idx < len(current_runs):
                run = current_runs[run_idx]
                # Remove all text nodes
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
        
        # Add the translated text to the first title run
        first_title_run_idx = title_run_indices[0]
        if first_title_run_idx < len(current_runs):
            first_title_run = current_runs[first_title_run_idx]
            
            # Create new text node with translated text
            new_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
            new_text_node.text = translated_title
            
            app_logger.info(f"Successfully updated TOC paragraph with translated title: '{translated_title}'")
    
    except Exception as e:
        app_logger.error(f"Error updating TOC with complete structure: {e}")
        update_toc_paragraph_fallback(paragraph, translated_title, namespaces)

def update_toc_paragraph_fallback(paragraph, translated_text, namespaces):
    """Fallback method for updating TOC paragraph - safer processing"""
    try:
        # Check if paragraph is in a hyperlink
        hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
        
        if hyperlinks:
            # Process hyperlink-based TOC
            hyperlink = hyperlinks[0]
            hyperlink_runs = hyperlink.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            title_runs = []
            non_title_elements = []
            
            # Identify title runs and preserve non-title elements
            for run in hyperlink_runs:
                run_text = ""
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    run_text += text_node.text or ""
                
                # Check if this run contains tabs, dots, or page numbers
                if (run.xpath('.//w:tab', namespaces=namespaces) or
                    is_dot_leader(run_text) or
                    is_likely_page_number(run_text) or
                    run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)):
                    # This is a non-title element, preserve it
                    non_title_elements.append({
                        'element': run,
                        'position': hyperlink_runs.index(run)
                    })
                else:
                    # This is likely a title run
                    if run_text.strip() and not re.match(r'^\d+\.?$', run_text.strip()):
                        title_runs.append(run)
            
            # Clear text from title runs only
            for run in title_runs:
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
            
            # Add translated text to the first title run if available
            if title_runs:
                first_title_run = title_runs[0]
                new_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                new_text_node.text = translated_text
            else:
                # Create a new run for the title if no title runs found
                new_run = etree.Element(f"{{{namespaces['w']}}}r")
                new_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                new_text_node.text = translated_text
                hyperlink.insert(0, new_run)
        
        else:
            # Process non-hyperlink TOC paragraph
            all_runs = paragraph.xpath('./w:r', namespaces=namespaces)
            
            title_runs = []
            formatting = None
            
            # Identify runs that contain title text (not tabs, dots, or page numbers)
            for run in all_runs:
                run_text = ""
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    run_text += text_node.text or ""
                
                # Skip runs with tabs, dots, page numbers, or fields
                if (run.xpath('.//w:tab', namespaces=namespaces) or
                    is_dot_leader(run_text) or
                    is_likely_page_number(run_text) or
                    run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)):
                    continue
                
                # This is likely a title run
                if run_text.strip() and not re.match(r'^\d+\.?$', run_text.strip()):
                    title_runs.append(run)
                    # Get formatting from the first title run
                    if formatting is None:
                        rPr_elements = run.xpath('./w:rPr', namespaces=namespaces)
                        if rPr_elements:
                            formatting = rPr_elements[0]
            
            # Clear text from title runs
            for run in title_runs:
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
            
            # Add translated text
            if title_runs:
                first_title_run = title_runs[0]
                new_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                new_text_node.text = translated_text
            else:
                # Create new run for title if none found
                new_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                if formatting is not None:
                    cloned_rPr = etree.fromstring(etree.tostring(formatting))
                    new_run.insert(0, cloned_rPr)
                new_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                new_text_node.text = translated_text
        
        app_logger.info(f"Updated TOC paragraph (fallback method) with: '{translated_text}'")
    
    except Exception as e:
        app_logger.error(f"Error in TOC fallback update: {e}")
        # Last resort: simple text replacement
        update_paragraph_text_with_enhanced_preservation(paragraph, translated_text, namespaces, None, None, None)

def update_numbering_xml_with_translations(numbering_tree, original_data, translations, namespaces):
    """Update numbering.xml with translated content, preserving variable placeholders"""
    for item in original_data:
        if item["type"] == "numbering_level_text":
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if translated_text:
                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                
                # Find the corresponding lvlText element using xpath
                element_xpath = item.get("element_xpath")
                if element_xpath:
                    try:
                        lvl_text_nodes = numbering_tree.xpath(element_xpath, namespaces=namespaces)
                        for lvl_text_node in lvl_text_nodes:
                            # Validate that this is the correct node by checking original value
                            current_val = lvl_text_node.get(f'{{{namespaces["w"]}}}val', '')
                            original_val = item.get("original_lvl_text", "")
                            
                            if current_val == original_val:
                                # Extract and preserve variable placeholders from translated text
                                final_translated_text = extract_and_preserve_variables(translated_text, original_val)
                                
                                # Update the lvlText value
                                lvl_text_node.set(f'{{{namespaces["w"]}}}val', final_translated_text)
                                app_logger.info(f"Updated numbering level text: '{original_val}' -> '{final_translated_text}'")
                                break
                    except Exception as e:
                        app_logger.error(f"Error updating numbering level text: {e}")
        
        elif item["type"] == "numbering_text_node":
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if translated_text:
                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                
                # Find the text node using the original text value
                original_text = item.get("original_text", "")
                text_nodes = numbering_tree.xpath('.//w:t', namespaces=namespaces)
                
                for text_node in text_nodes:
                    if text_node.text and text_node.text.strip() == original_text:
                        text_node.text = translated_text
                        app_logger.info(f"Updated numbering text node: '{original_text}' -> '{translated_text}'")
                        break

def extract_and_preserve_variables(translated_text, original_text):
    """Extract variable placeholders from translated text and ensure they're preserved correctly"""
    # Find all variables in the original text
    original_variables = re.findall(r'%\d+', original_text)
    
    # Find all variables in the translated text
    translated_variables = re.findall(r'%\d+', translated_text)
    
    # If all variables are preserved in translation, return as is
    if set(original_variables) == set(translated_variables):
        return translated_text
    
    # If some variables are missing, try to restore them
    if original_variables:
        # Check if the translation removed variables
        if not translated_variables:
            # Variables were removed, try to restore them by finding the best position
            app_logger.warning(f"Variables {original_variables} were removed from translation, attempting to restore")
            
            # For now, just append the first variable to the end
            if original_variables:
                return translated_text + original_variables[0]
        else:
            # Some variables preserved, ensure all are present
            missing_vars = set(original_variables) - set(translated_variables)
            if missing_vars:
                app_logger.warning(f"Missing variables {missing_vars} in translation")
                # Add missing variables at the end
                for var in missing_vars:
                    translated_text += var
    
    return translated_text

def update_json_structure_after_translation(original_json_path, translated_json_path):
    """Update JSON structure after translation"""
    with open(original_json_path, "r", encoding="utf-8") as orig_file:
        original_data = json.load(orig_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as trans_file:
        translated_data = json.load(trans_file)
    
    translations_by_id = {}
    for item in translated_data:
        if "translated" in item:
            item_id = str(item.get("id", item.get("count_src")))
            if item_id:
                translations_by_id[item_id] = item["translated"]
    
    restructured_data = []
    for item in original_data:
        item_id = str(item.get("id", item.get("count_src")))
        if item_id in translations_by_id:
            new_item = {
                "id": item.get("id"),
                "count_src": item.get("count_src"),
                "type": item["type"],
                "translated": translations_by_id[item_id]
            }
            
            # Preserve important metadata including TOC structure, SmartArt, and all format info
            preserve_keys = [
                "is_heading", "has_numbering", "numbering_info", "is_toc", "toc_info", "toc_structure", 
                "textbox_type", "textbox_format", "textbox_index", "positioning_info", "paragraph_context",
                "field_info", "original_pPr", "original_structure", "table_props", "row_props", "cell_props",
                "paragraph_index", "nesting_level", "sdt_index", "is_toc_sdt", "sdt_props",
                "diagram_index", "shape_index", "tx_body_index", "model_id", "run_texts", "run_styles", 
                "run_lengths", "drawing_path", "original_text", "xpath"
            ]
            
            for key in preserve_keys:
                if key in item:
                    new_item[key] = item[key]
            
            restructured_data.append(new_item)
    
    with open(translated_json_path, "w", encoding="utf-8") as outfile:
        json.dump(restructured_data, outfile, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Updated translation JSON structure: {translated_json_path}")
    return translated_json_path
