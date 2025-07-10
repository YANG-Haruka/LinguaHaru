import json
import os
from lxml import etree
from zipfile import ZipFile
from .skip_pipeline import should_translate
from config.log_config import app_logger
from typing import Dict, List, Any, Optional

def extract_ppt_content_to_json(file_path: str) -> str:
    """
    Extract text content from PowerPoint, processing each text element with proper positioning.
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
    
    # Complete namespace definitions
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'
    }

    try:
        with ZipFile(file_path, 'r') as pptx:
            for slide_index, slide_path in enumerate(slides, start=1):
                try:
                    slide_xml = pptx.read(slide_path)
                    slide_tree = etree.fromstring(slide_xml)
                    
                    # Extract text from text boxes with better structure tracking
                    count = _extract_text_boxes(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from tables
                    count = _extract_tables(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from shapes (excluding text boxes)
                    count = _extract_shapes(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from charts
                    count = _extract_charts(slide_tree, slide_index, namespaces, content_data, count)
                    
                    # Extract text from notes
                    notes_path = slide_path.replace('slides/slide', 'notesSlides/notesSlide')
                    if notes_path in pptx.namelist():
                        count = _extract_notes(pptx, notes_path, slide_index, namespaces, content_data, count)
                        
                except Exception as e:
                    app_logger.error(f"Failed to process slide {slide_index}: {e}")
                    continue

    except Exception as e:
        app_logger.error(f"Failed to process PPTX content: {e}")
        raise

    # Save content to JSON with better error handling
    try:
        filename = os.path.splitext(os.path.basename(file_path))[0]
        temp_folder = os.path.join("temp", filename)
        os.makedirs(temp_folder, exist_ok=True)
        json_path = os.path.join(temp_folder, "src.json")
        
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(content_data, json_file, ensure_ascii=False, indent=4)
        
        app_logger.info(f"Extracted {len(content_data)} text elements from PowerPoint")
        return json_path
        
    except Exception as e:
        app_logger.error(f"Failed to save JSON file: {e}")
        raise

def _extract_text_boxes(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from text boxes with proper run tracking."""
    text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
    
    for text_box_index, text_box in enumerate(text_boxes, start=1):
        paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
        
        for p_index, paragraph in enumerate(paragraphs):
            text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
            
            for run_index, text_run in enumerate(text_runs):
                text_node = text_run.xpath('./a:t', namespaces=namespaces)
                if not text_node or not text_node[0].text:
                    continue
                    
                node_text = text_node[0].text
                
                # Extract complete style information
                style_info = _extract_run_style(text_run, namespaces)
                
                if should_translate(node_text):
                    count += 1
                    content_data.append({
                        "count_src": count,
                        "slide_index": slide_index,
                        "text_box_index": text_box_index,
                        "paragraph_index": p_index,
                        "run_index": run_index,
                        "type": "text_run",
                        "value": node_text.replace("\n", "␊").replace("\r", "␍"),
                        "style": style_info,
                        "xpath": f".//p:txBody[{text_box_index}]//a:p[{p_index + 1}]//a:r[{run_index + 1}]/a:t"
                    })
    
    return count

def _extract_tables(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from tables."""
    tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
    
    for table_index, table in enumerate(tables, start=1):
        rows = table.xpath('.//a:tr', namespaces=namespaces)
        
        for row_index, row in enumerate(rows):
            cells = row.xpath('.//a:tc', namespaces=namespaces)
            
            for cell_index, cell in enumerate(cells):
                # Process text runs in cell
                text_runs = cell.xpath('.//a:r', namespaces=namespaces)
                
                for run_index, text_run in enumerate(text_runs):
                    text_node = text_run.xpath('./a:t', namespaces=namespaces)
                    if not text_node or not text_node[0].text:
                        continue
                        
                    cell_text = text_node[0].text
                    style_info = _extract_run_style(text_run, namespaces)
                    
                    if should_translate(cell_text):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "table_index": table_index,
                            "row_index": row_index,
                            "cell_index": cell_index,
                            "run_index": run_index,
                            "type": "table_cell",
                            "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                            "style": style_info,
                            "xpath": f".//a:tbl[{table_index}]//a:tr[{row_index + 1}]//a:tc[{cell_index + 1}]//a:r[{run_index + 1}]/a:t"
                        })
    
    return count

def _extract_shapes(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from shapes (excluding text boxes)."""
    shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
    
    # Count non-textbox shapes to maintain proper indexing
    non_textbox_shapes = []
    for shape in shapes:
        if not shape.xpath('.//p:txBody', namespaces=namespaces):
            non_textbox_shapes.append(shape)
    
    for shape_index, shape in enumerate(non_textbox_shapes, start=1):
        text_runs = shape.xpath('.//a:r', namespaces=namespaces)
        
        for run_index, text_run in enumerate(text_runs):
            text_node = text_run.xpath('./a:t', namespaces=namespaces)
            if not text_node or not text_node[0].text:
                continue
                
            shape_text = text_node[0].text
            style_info = _extract_run_style(text_run, namespaces)
            
            if should_translate(shape_text):
                count += 1
                content_data.append({
                    "count_src": count,
                    "slide_index": slide_index,
                    "shape_index": shape_index,
                    "run_index": run_index,
                    "type": "shape",
                    "value": shape_text.replace("\n", "␊").replace("\r", "␍"),
                    "style": style_info,
                    "xpath": f".//p:sp[not(.//p:txBody)][{shape_index}]//a:r[{run_index + 1}]/a:t"
                })
    
    return count

def _extract_charts(slide_tree, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from charts."""
    charts = slide_tree.xpath('.//c:chart', namespaces=namespaces)
    
    for chart_index, chart in enumerate(charts, start=1):
        text_runs = chart.xpath('.//a:r', namespaces=namespaces)
        
        for run_index, text_run in enumerate(text_runs):
            text_node = text_run.xpath('./a:t', namespaces=namespaces)
            if not text_node or not text_node[0].text:
                continue
                
            chart_text = text_node[0].text
            style_info = _extract_run_style(text_run, namespaces)
            
            if should_translate(chart_text):
                count += 1
                content_data.append({
                    "count_src": count,
                    "slide_index": slide_index,
                    "chart_index": chart_index,
                    "run_index": run_index,
                    "type": "chart",
                    "value": chart_text.replace("\n", "␊").replace("\r", "␍"),
                    "style": style_info,
                    "xpath": f".//c:chart[{chart_index}]//a:r[{run_index + 1}]/a:t"
                })
    
    return count

def _extract_notes(pptx, notes_path: str, slide_index: int, namespaces: Dict, content_data: List, count: int) -> int:
    """Extract text from slide notes."""
    try:
        notes_xml = pptx.read(notes_path)
        notes_tree = etree.fromstring(notes_xml)
        
        text_runs = notes_tree.xpath('.//a:r', namespaces=namespaces)
        
        for run_index, text_run in enumerate(text_runs):
            text_node = text_run.xpath('./a:t', namespaces=namespaces)
            if not text_node or not text_node[0].text:
                continue
                
            notes_text = text_node[0].text
            style_info = _extract_run_style(text_run, namespaces)
            
            if should_translate(notes_text):
                count += 1
                content_data.append({
                    "count_src": count,
                    "slide_index": slide_index,
                    "run_index": run_index,
                    "type": "notes",
                    "value": notes_text.replace("\n", "␊").replace("\r", "␍"),
                    "style": style_info,
                    "xpath": f".//a:r[{run_index + 1}]/a:t"
                })
                
    except Exception as e:
        app_logger.error(f"Failed to extract notes for slide {slide_index}: {e}")
    
    return count

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

def write_translated_content_to_ppt(file_path: str, original_json_path: str, translated_json_path: str) -> str:
    """
    Write translated content back to the PowerPoint file while preserving format and structure.
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
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{filename}_translated.pptx")
    
    # Remove existing file if it exists
    if os.path.exists(result_path):
        os.remove(result_path)

    # Create temporary directory for modified files
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)

    # Define namespaces
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'
    }

    try:
        with ZipFile(file_path, 'r') as pptx:
            slides = [name for name in pptx.namelist() 
                     if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
            slides.sort()
            
            notes_slides = [name for name in pptx.namelist() 
                          if name.startswith('ppt/notesSlides/notesSlide') and name.endswith('.xml')]
            notes_slides.sort()

            # Process each slide
            for slide_index, slide_path in enumerate(slides, start=1):
                try:
                    slide_xml = pptx.read(slide_path)
                    slide_tree = etree.fromstring(slide_xml)
                    
                    # Get items for this slide
                    slide_items = [item for item in original_data if item['slide_index'] == slide_index]
                    
                    # Apply translations to slide
                    _apply_translations_to_slide(slide_tree, slide_items, translations, namespaces)
                    
                    # Save modified slide
                    modified_slide_path = os.path.join(temp_folder, slide_path)
                    os.makedirs(os.path.dirname(modified_slide_path), exist_ok=True)
                    
                    with open(modified_slide_path, "wb") as modified_slide:
                        modified_slide.write(etree.tostring(slide_tree, xml_declaration=True, 
                                                          encoding="UTF-8", standalone="yes"))
                        
                except Exception as e:
                    app_logger.error(f"Failed to process slide {slide_index}: {e}")
                    continue

            # Process notes slides
            for slide_index, notes_path in enumerate(notes_slides, start=1):
                try:
                    notes_xml = pptx.read(notes_path)
                    notes_tree = etree.fromstring(notes_xml)
                    
                    # Get notes items for this slide
                    notes_items = [item for item in original_data 
                                 if item['slide_index'] == slide_index and item['type'] == 'notes']
                    
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

            # Create final PowerPoint file
            _create_final_pptx(file_path, result_path, temp_folder, slides, notes_slides)
            
    except Exception as e:
        app_logger.error(f"Failed to write translated content: {e}")
        raise

    app_logger.info(f"Translated PowerPoint saved to: {result_path}")
    return result_path

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
            if item['type'] == 'text_run':
                _apply_text_run_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'table_cell':
                _apply_table_cell_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'shape':
                _apply_shape_translation(slide_tree, item, translated_text, namespaces)
            elif item['type'] == 'chart':
                _apply_chart_translation(slide_tree, item, translated_text, namespaces)
                
        except Exception as e:
            app_logger.error(f"Failed to apply translation for count {count}: {e}")

def _apply_text_run_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a text run."""
    text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
    
    if item['text_box_index'] <= len(text_boxes):
        text_box = text_boxes[item['text_box_index'] - 1]
        paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
        
        if item['paragraph_index'] < len(paragraphs):
            paragraph = paragraphs[item['paragraph_index']]
            text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
            
            if item['run_index'] < len(text_runs):
                text_run = text_runs[item['run_index']]
                text_node = text_run.xpath('./a:t', namespaces=namespaces)
                
                if text_node:
                    text_node[0].text = translated_text
                else:
                    app_logger.warning(f"Text node not found for text run translation")

def _apply_table_cell_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a table cell."""
    tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
    
    if item['table_index'] <= len(tables):
        table = tables[item['table_index'] - 1]
        rows = table.xpath('.//a:tr', namespaces=namespaces)
        
        if item['row_index'] < len(rows):
            row = rows[item['row_index']]
            cells = row.xpath('.//a:tc', namespaces=namespaces)
            
            if item['cell_index'] < len(cells):
                cell = cells[item['cell_index']]
                text_runs = cell.xpath('.//a:r', namespaces=namespaces)
                
                if item['run_index'] < len(text_runs):
                    text_run = text_runs[item['run_index']]
                    text_node = text_run.xpath('./a:t', namespaces=namespaces)
                    
                    if text_node:
                        text_node[0].text = translated_text
                    else:
                        app_logger.warning(f"Text node not found for table cell translation")

def _apply_shape_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a shape."""
    shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
    
    # Filter out shapes that are text boxes to maintain proper indexing
    non_textbox_shapes = [shape for shape in shapes 
                         if not shape.xpath('.//p:txBody', namespaces=namespaces)]
    
    if item['shape_index'] <= len(non_textbox_shapes):
        shape = non_textbox_shapes[item['shape_index'] - 1]
        text_runs = shape.xpath('.//a:r', namespaces=namespaces)
        
        if item['run_index'] < len(text_runs):
            text_run = text_runs[item['run_index']]
            text_node = text_run.xpath('./a:t', namespaces=namespaces)
            
            if text_node:
                text_node[0].text = translated_text
            else:
                app_logger.warning(f"Text node not found for shape translation")

def _apply_chart_translation(slide_tree, item: Dict, translated_text: str, namespaces: Dict):
    """Apply translation to a chart."""
    charts = slide_tree.xpath('.//c:chart', namespaces=namespaces)
    
    if item['chart_index'] <= len(charts):
        chart = charts[item['chart_index'] - 1]
        text_runs = chart.xpath('.//a:r', namespaces=namespaces)
        
        if item['run_index'] < len(text_runs):
            text_run = text_runs[item['run_index']]
            text_node = text_run.xpath('./a:t', namespaces=namespaces)
            
            if text_node:
                text_node[0].text = translated_text
            else:
                app_logger.warning(f"Text node not found for chart translation")

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
            text_runs = notes_tree.xpath('.//a:r', namespaces=namespaces)
            
            if item['run_index'] < len(text_runs):
                text_run = text_runs[item['run_index']]
                text_node = text_run.xpath('./a:t', namespaces=namespaces)
                
                if text_node:
                    text_node[0].text = translated_text
                else:
                    app_logger.warning(f"Text node not found for notes translation")
                    
        except Exception as e:
            app_logger.error(f"Failed to apply notes translation for count {count}: {e}")

def _create_final_pptx(original_path: str, result_path: str, temp_folder: str, 
                      slides: List[str], notes_slides: List[str]):
    """Create the final translated PowerPoint file."""
    with ZipFile(original_path, 'r') as original_pptx:
        with ZipFile(result_path, 'w') as new_pptx:
            # Copy all files except slides and notes that we've modified
            for item in original_pptx.infolist():
                if item.filename not in slides and item.filename not in notes_slides:
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
