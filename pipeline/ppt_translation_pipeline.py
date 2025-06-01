import json
import os
from lxml import etree
from zipfile import ZipFile
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_ppt_content_to_json(file_path):
    """
    Extract text content from PowerPoint, processing each text run with different styles separately.
    """
    with ZipFile(file_path, 'r') as pptx:
        slides = [name for name in pptx.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')]

    content_data = []
    count = 0
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'
    }

    with ZipFile(file_path, 'r') as pptx:
        for slide_index, slide_path in enumerate(slides, start=1):
            slide_xml = pptx.read(slide_path)
            slide_tree = etree.fromstring(slide_xml)

            # Extract text from text boxes
            text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
            
            for text_box_index, text_box in enumerate(text_boxes, start=1):
                # Get all paragraphs in the text box
                paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
                
                current_style = None
                current_segment = {
                    "slide_index": slide_index,
                    "text_box_index": text_box_index,
                    "paragraphs": [],
                    "style_segments": []
                }
                current_text = []
                
                for p_index, paragraph in enumerate(paragraphs):
                    # Get each text run in the paragraph
                    text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                    
                    for run_index, text_run in enumerate(text_runs):
                        # Extract the text content
                        text_node = text_run.xpath('./a:t', namespaces=namespaces)
                        node_text = text_node[0].text if text_node and text_node[0].text else ""
                        
                        # Extract style information
                        rpr = text_run.xpath('./a:rPr', namespaces=namespaces)
                        style_info = {}
                        
                        if rpr:
                            # Font size
                            sz = rpr[0].get('sz')
                            if sz:
                                style_info['font_size'] = sz
                            
                            # Bold
                            b = rpr[0].get('b')
                            if b:
                                style_info['bold'] = b
                            
                            # Italic
                            i = rpr[0].get('i')
                            if i:
                                style_info['italic'] = i
                            
                            # Font color
                            solid_fill = rpr[0].xpath('./a:solidFill/a:srgbClr', namespaces=namespaces)
                            if solid_fill:
                                style_info['color'] = solid_fill[0].get('val')
                        
                        # Check if style has changed
                        if style_info != current_style and current_text:
                            # Save previous segment
                            segment_text = "".join(current_text).replace("\n", "␊").replace("\r", "␍")
                            if should_translate(segment_text):
                                count += 1
                                content_data.append({
                                    "count_src": count,
                                    "slide_index": slide_index,
                                    "text_box_index": text_box_index,
                                    "p_index": p_index,
                                    "type": "style_segment",
                                    "value": segment_text,
                                    "style": current_style,
                                    "position": len(current_segment["style_segments"])
                                })
                                
                                current_segment["style_segments"].append({
                                    "position": len(current_segment["style_segments"]),
                                    "text": segment_text,
                                    "style": current_style
                                })
                            
                            current_text = []
                        
                        current_style = style_info
                        if node_text:
                            current_text.append(node_text)
                    
                    # Add paragraph break if needed
                    if text_runs and p_index < len(paragraphs) - 1:
                        current_text.append("\n")
                
                # Save the last segment
                if current_text:
                    segment_text = "".join(current_text).replace("\n", "␊").replace("\r", "␍")
                    if should_translate(segment_text):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "text_box_index": text_box_index,
                            "p_index": p_index if 'p_index' in locals() else 0,
                            "type": "style_segment",
                            "value": segment_text,
                            "style": current_style,
                            "position": len(current_segment["style_segments"])
                        })
                        
                        current_segment["style_segments"].append({
                            "position": len(current_segment["style_segments"]),
                            "text": segment_text,
                            "style": current_style
                        })

            # Extract text from tables
            tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
            
            for table_index, table in enumerate(tables, start=1):
                # Get all rows in the table
                rows = table.xpath('.//a:tr', namespaces=namespaces)
                
                for row_index, row in enumerate(rows):
                    # Get all cells in the row
                    cells = row.xpath('.//a:tc', namespaces=namespaces)
                    
                    for cell_index, cell in enumerate(cells):
                        # Get text from cell
                        cell_text_nodes = cell.xpath('.//a:t', namespaces=namespaces)
                        
                        for text_node_index, text_node in enumerate(cell_text_nodes):
                            cell_text = text_node.text if text_node.text else ""
                            
                            if should_translate(cell_text):
                                count += 1
                                content_data.append({
                                    "count_src": count,
                                    "slide_index": slide_index,
                                    "table_index": table_index,
                                    "row_index": row_index,
                                    "cell_index": cell_index,
                                    "text_node_index": text_node_index,
                                    "type": "table_cell",
                                    "value": cell_text.replace("\n", "␊").replace("\r", "␍")
                                })

            # Extract text from shapes (SmartArt, diagrams, etc.)
            shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
            
            for shape_index, shape in enumerate(shapes, start=1):
                # Check if it's not already processed as a text box
                if shape.xpath('.//p:txBody', namespaces=namespaces):
                    continue  # Already processed
                
                # Get text from shape
                shape_text_nodes = shape.xpath('.//a:t', namespaces=namespaces)
                
                for text_node_index, text_node in enumerate(shape_text_nodes):
                    shape_text = text_node.text if text_node.text else ""
                    
                    if should_translate(shape_text):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "shape_index": shape_index,
                            "text_node_index": text_node_index,
                            "type": "shape",
                            "value": shape_text.replace("\n", "␊").replace("\r", "␍")
                        })

            # Extract text from charts
            charts = slide_tree.xpath('.//a:chart', namespaces=namespaces)
            
            for chart_index, chart in enumerate(charts, start=1):
                # Get text from chart elements
                chart_text_nodes = chart.xpath('.//a:t', namespaces=namespaces)
                
                for text_node_index, text_node in enumerate(chart_text_nodes):
                    chart_text = text_node.text if text_node.text else ""
                    
                    if should_translate(chart_text):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "chart_index": chart_index,
                            "text_node_index": text_node_index,
                            "type": "chart",
                            "value": chart_text.replace("\n", "␊").replace("\r", "␍")
                        })

            # Extract text from notes
            notes_path = slide_path.replace('slides/slide', 'notesSlides/notesSlide')
            if notes_path in pptx.namelist():
                notes_xml = pptx.read(notes_path)
                notes_tree = etree.fromstring(notes_xml)
                
                notes_text_nodes = notes_tree.xpath('.//a:t', namespaces=namespaces)
                
                for text_node_index, text_node in enumerate(notes_text_nodes):
                    notes_text = text_node.text if text_node.text else ""
                    
                    if should_translate(notes_text):
                        count += 1
                        content_data.append({
                            "count_src": count,
                            "slide_index": slide_index,
                            "text_node_index": text_node_index,
                            "type": "notes",
                            "value": notes_text.replace("\n", "␊").replace("\r", "␍")
                        })

    # Save content to JSON
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    return json_path

def write_translated_content_to_ppt(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to the PowerPoint file while preserving the format and structure.
    """
    # Load original and translated JSON
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    # Create a mapping of translations
    translations = {str(item["count_src"]): item["translated"] for item in translated_data}

    # Open the PowerPoint file as a ZIP archive
    with ZipFile(file_path, 'r') as pptx:
        slides = [name for name in pptx.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
        notes_slides = [name for name in pptx.namelist() if name.startswith('ppt/notesSlides/notesSlide') and name.endswith('.xml')]

    # Create temporary directory
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)

    # Define namespaces
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'
    }

    # Replace text in each slide
    with ZipFile(file_path, 'r') as pptx:
        for slide_index, slide_path in enumerate(slides, start=1):
            slide_xml = pptx.read(slide_path)
            slide_tree = etree.fromstring(slide_xml)
            
            # Process style segments for text boxes
            slide_segments = [item for item in original_data if item['slide_index'] == slide_index and item['type'] == 'style_segment']
            
            if slide_segments:
                text_boxes = slide_tree.xpath('.//p:txBody', namespaces=namespaces)
                
                # Group segments by text box
                text_box_segments = {}
                for item in slide_segments:
                    tb_index = item['text_box_index']
                    if tb_index not in text_box_segments:
                        text_box_segments[tb_index] = []
                    text_box_segments[tb_index].append(item)
                
                # Process each text box
                for text_box_index, text_box in enumerate(text_boxes, start=1):
                    if text_box_index not in text_box_segments:
                        continue
                        
                    # Create a map of paragraph runs for this text box
                    paragraph_runs = {}
                    paragraphs = text_box.xpath('.//a:p', namespaces=namespaces)
                    
                    for p_index, paragraph in enumerate(paragraphs, start=0):
                        if p_index not in paragraph_runs:
                            paragraph_runs[p_index] = []
                        
                        runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                        for run in runs:
                            # Get the style of this run
                            rpr = run.xpath('./a:rPr', namespaces=namespaces)
                            style_info = {}
                            
                            if rpr:
                                sz = rpr[0].get('sz')
                                if sz:
                                    style_info['font_size'] = sz
                                
                                b = rpr[0].get('b')
                                if b:
                                    style_info['bold'] = b
                                
                                i = rpr[0].get('i')
                                if i:
                                    style_info['italic'] = i
                                
                                solid_fill = rpr[0].xpath('./a:solidFill/a:srgbClr', namespaces=namespaces)
                                if solid_fill:
                                    style_info['color'] = solid_fill[0].get('val')
                            
                            paragraph_runs[p_index].append((run, style_info))
                    
                    # Process segments for this text box
                    for segment in text_box_segments[text_box_index]:
                        style = segment['style']
                        count = segment['count_src']
                        
                        # Get translation for this segment
                        translated_text = translations.get(str(count), None)
                        if not translated_text:
                            app_logger.warning(f"Missing translation for count {count}. Skipping.")
                            continue
                        
                        # Handle paragraph breaks in translations
                        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                        
                        # Find all runs with matching style
                        matching_runs_by_paragraph = {}
                        for p_idx, runs in paragraph_runs.items():
                            for run, run_style in runs:
                                if run_style == style:
                                    if p_idx not in matching_runs_by_paragraph:
                                        matching_runs_by_paragraph[p_idx] = []
                                    matching_runs_by_paragraph[p_idx].append(run)
                        
                        if not matching_runs_by_paragraph:
                            app_logger.warning(f"No matching runs found for segment {count} with style {style}")
                            continue
                        
                        # Get all paragraph indices and sort them
                        paragraph_indices = sorted(matching_runs_by_paragraph.keys())
                        
                        # Get the original formatted paragraph texts
                        original_paragraph_texts = []
                        for p_idx in paragraph_indices:
                            p_runs = matching_runs_by_paragraph[p_idx]
                            p_text = []
                            for run in p_runs:
                                text_node = run.xpath('./a:t', namespaces=namespaces)
                                if text_node and text_node[0].text:
                                    p_text.append(text_node[0].text)
                            if p_text:
                                original_paragraph_texts.append("".join(p_text))
                        
                        # Distribute translated text to paragraphs
                        paragraphs_to_write = []
                        if "\n" in translated_text:
                            paragraphs_to_write = translated_text.split("\n")
                        else:
                            if len(paragraph_indices) > 1:
                                total_original_length = sum(len(text) for text in original_paragraph_texts)
                                current_pos = 0
                                
                                for i, original_text in enumerate(original_paragraph_texts):
                                    if i == len(original_paragraph_texts) - 1:
                                        paragraphs_to_write.append(translated_text[current_pos:])
                                    else:
                                        ratio = len(original_text) / total_original_length
                                        chars_to_take = int(len(translated_text) * ratio)
                                        paragraphs_to_write.append(translated_text[current_pos:current_pos + chars_to_take])
                                        current_pos += chars_to_take
                            else:
                                paragraphs_to_write = [translated_text]
                        
                        # Write translated text
                        for i, p_idx in enumerate(paragraph_indices):
                            if i < len(paragraphs_to_write):
                                paragraph_text = paragraphs_to_write[i]
                                runs = matching_runs_by_paragraph[p_idx]
                                
                                # Clear all matching runs
                                for run in runs:
                                    text_node = run.xpath('./a:t', namespaces=namespaces)
                                    if text_node:
                                        text_node[0].text = ""
                                
                                # Set text to first run
                                if runs:
                                    text_node = runs[0].xpath('./a:t', namespaces=namespaces)
                                    if text_node:
                                        text_node[0].text = paragraph_text
                            else:
                                # Clear excess paragraphs
                                runs = matching_runs_by_paragraph[p_idx]
                                for run in runs:
                                    text_node = run.xpath('./a:t', namespaces=namespaces)
                                    if text_node:
                                        text_node[0].text = ""
            
            # Process table cells
            table_items = [item for item in original_data if item['slide_index'] == slide_index and item['type'] == 'table_cell']
            
            if table_items:
                tables = slide_tree.xpath('.//a:tbl', namespaces=namespaces)
                
                for item in table_items:
                    table_index = item['table_index'] - 1
                    if table_index < len(tables):
                        table = tables[table_index]
                        rows = table.xpath('.//a:tr', namespaces=namespaces)
                        
                        row_index = item['row_index']
                        if row_index < len(rows):
                            row = rows[row_index]
                            cells = row.xpath('.//a:tc', namespaces=namespaces)
                            
                            cell_index = item['cell_index']
                            if cell_index < len(cells):
                                cell = cells[cell_index]
                                text_nodes = cell.xpath('.//a:t', namespaces=namespaces)
                                
                                text_node_index = item['text_node_index']
                                if text_node_index < len(text_nodes):
                                    count = item['count_src']
                                    translated_text = translations.get(str(count), None)
                                    
                                    if translated_text:
                                        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                        text_nodes[text_node_index].text = translated_text
                                    else:
                                        app_logger.warning(f"Missing translation for table cell count {count}")
            
            # Process shapes
            shape_items = [item for item in original_data if item['slide_index'] == slide_index and item['type'] == 'shape']
            
            if shape_items:
                shapes = slide_tree.xpath('.//p:sp', namespaces=namespaces)
                
                for item in shape_items:
                    shape_index = item['shape_index'] - 1
                    if shape_index < len(shapes):
                        shape = shapes[shape_index]
                        text_nodes = shape.xpath('.//a:t', namespaces=namespaces)
                        
                        text_node_index = item['text_node_index']
                        if text_node_index < len(text_nodes):
                            count = item['count_src']
                            translated_text = translations.get(str(count), None)
                            
                            if translated_text:
                                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                text_nodes[text_node_index].text = translated_text
                            else:
                                app_logger.warning(f"Missing translation for shape count {count}")
            
            # Process charts
            chart_items = [item for item in original_data if item['slide_index'] == slide_index and item['type'] == 'chart']
            
            if chart_items:
                charts = slide_tree.xpath('.//a:chart', namespaces=namespaces)
                
                for item in chart_items:
                    chart_index = item['chart_index'] - 1
                    if chart_index < len(charts):
                        chart = charts[chart_index]
                        text_nodes = chart.xpath('.//a:t', namespaces=namespaces)
                        
                        text_node_index = item['text_node_index']
                        if text_node_index < len(text_nodes):
                            count = item['count_src']
                            translated_text = translations.get(str(count), None)
                            
                            if translated_text:
                                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                text_nodes[text_node_index].text = translated_text
                            else:
                                app_logger.warning(f"Missing translation for chart count {count}")
            
            # Save modified slide
            modified_slide_path = os.path.join(temp_folder, slide_path)
            os.makedirs(os.path.dirname(modified_slide_path), exist_ok=True)
            with open(modified_slide_path, "wb") as modified_slide:
                modified_slide.write(etree.tostring(slide_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
        
        # Process notes
        for slide_index, notes_path in enumerate(notes_slides, start=1):
            notes_xml = pptx.read(notes_path)
            notes_tree = etree.fromstring(notes_xml)
            
            # Process notes items
            notes_items = [item for item in original_data if item['slide_index'] == slide_index and item['type'] == 'notes']
            
            if notes_items:
                text_nodes = notes_tree.xpath('.//a:t', namespaces=namespaces)
                
                for item in notes_items:
                    text_node_index = item['text_node_index']
                    if text_node_index < len(text_nodes):
                        count = item['count_src']
                        translated_text = translations.get(str(count), None)
                        
                        if translated_text:
                            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                            text_nodes[text_node_index].text = translated_text
                        else:
                            app_logger.warning(f"Missing translation for notes count {count}")
                
                # Save modified notes
                modified_notes_path = os.path.join(temp_folder, notes_path)
                os.makedirs(os.path.dirname(modified_notes_path), exist_ok=True)
                with open(modified_notes_path, "wb") as modified_notes:
                    modified_notes.write(etree.tostring(notes_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    # Create a new PowerPoint file with modified content
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    
    # Define the output path
    result_path = os.path.join(result_folder, f"{filename}_translated.pptx")
    
    # Remove existing file if it exists
    if os.path.exists(result_path):
        os.remove(result_path)

    # Create a new PowerPoint file with modified content
    with ZipFile(file_path, 'r') as original_pptx:
        with ZipFile(result_path, 'w') as new_pptx:
            # Copy all files except slides and notes
            for item in original_pptx.infolist():
                if item.filename not in slides and item.filename not in notes_slides:
                    new_pptx.writestr(item, original_pptx.read(item.filename))
            
            # Add modified slides
            for slide in slides:
                modified_slide_path = os.path.join(temp_folder, slide)
                if os.path.exists(modified_slide_path):
                    new_pptx.write(modified_slide_path, slide)
                else:
                    app_logger.warning(f"Modified slide not found: {modified_slide_path}. Using original slide.")
                    new_pptx.writestr(slide, original_pptx.read(slide))
            
            # Add modified notes
            for notes in notes_slides:
                modified_notes_path = os.path.join(temp_folder, notes)
                if os.path.exists(modified_notes_path):
                    new_pptx.write(modified_notes_path, notes)
                else:
                    new_pptx.writestr(notes, original_pptx.read(notes))

    app_logger.info(f"Translated PowerPoint saved to: {result_path}")
    return result_path