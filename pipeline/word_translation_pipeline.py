import json
import os
from lxml import etree
from zipfile import ZipFile
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_word_content_to_json(file_path):
    with ZipFile(file_path, 'r') as docx:
        document_xml = docx.read('word/document.xml')

    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)

    content_data = []
    item_id = 0
    
    # Get all block elements for indexing
    block_elements = document_tree.xpath('.//*[self::w:p or self::w:tbl]', namespaces=namespaces)
    
    # Process all paragraphs and tables
    for element in block_elements:
        element_type = element.tag.split('}')[-1]
        element_index = block_elements.index(element)
        
        if element_type == 'p':
            is_heading = bool(element.xpath('.//w:pStyle[@w:val="Heading1" or @w:val="Heading2" or @w:val="Heading3"]', namespaces=namespaces))
            has_numbering = bool(element.xpath('.//w:numPr', namespaces=namespaces))
            
            paragraph_text = ""
            numbering_text = ""
            
            if has_numbering:
                runs = element.xpath('.//w:r', namespaces=namespaces)
                in_numbering = True
                
                for run in runs:
                    if in_numbering:
                        run_text = ""
                        text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                        for text_node in text_nodes:
                            run_text += text_node.text if text_node.text else ""
                        
                        if run_text.strip() and (run_text.strip().endswith('.') or run_text.strip().endswith(')') or run_text.strip().endswith(':')):
                            numbering_text += run_text
                            in_numbering = False
                        elif run_text.strip():
                            paragraph_text += run_text
                            in_numbering = False
                    else:
                        text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                        for text_node in text_nodes:
                            paragraph_text += text_node.text if text_node.text else ""
            else:
                runs = element.xpath('.//w:r', namespaces=namespaces)
                for run in runs:
                    text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                    for text_node in text_nodes:
                        paragraph_text += text_node.text if text_node.text else ""
            
            full_text = numbering_text + paragraph_text
            
            if full_text and should_translate(full_text):
                item_id += 1
                content_data.append({
                    "id": item_id,
                    "count": item_id,
                    "type": "paragraph",
                    "is_heading": is_heading,
                    "has_numbering": has_numbering,
                    "numbering_text": numbering_text,
                    "element_index": element_index,
                    "value": paragraph_text.replace("\n", "␊").replace("\r", "␍")
                })
        # Process for sheet in Word
        elif element_type == 'tbl':
            table_cells = []
            rows = element.xpath('.//w:tr', namespaces=namespaces)
            
            for row_idx, row in enumerate(rows):
                cells = row.xpath('.//w:tc', namespaces=namespaces)
                
                for cell_idx, cell in enumerate(cells):
                    cell_text = ""
                    
                    cell_paragraphs = cell.xpath('.//w:p', namespaces=namespaces)
                    for cell_para_idx, cell_paragraph in enumerate(cell_paragraphs):
                        para_text = ""
                        cell_runs = cell_paragraph.xpath('.//w:r', namespaces=namespaces)
                        
                        for cell_run in cell_runs:
                            cell_text_nodes = cell_run.xpath('.//w:t', namespaces=namespaces)
                            for cell_text_node in cell_text_nodes:
                                para_text += cell_text_node.text if cell_text_node.text else ""
                        
                        if para_text:
                            cell_text += para_text
                            if cell_para_idx < len(cell_paragraphs) - 1:
                                cell_text += "\n"
                    
                    cell_text = cell_text.strip()
                    if cell_text and should_translate(cell_text):
                        item_id += 1
                        table_cells.append({
                            "id": item_id,
                            "count": item_id,
                            "type": "table_cell",
                            "table_index": element_index,
                            "row": row_idx,
                            "col": cell_idx,
                            "value": cell_text.replace("\n", "␊").replace("\r", "␍")
                        })
            
            content_data.extend(table_cells)

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    app_logger.info(f"Extracted {len(content_data)} content items from document: {filename}")
    return json_path


def write_translated_content_to_word(file_path, original_json_path, translated_json_path):
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    translations = {}
    for item in translated_data:
        item_id = str(item.get("id", item.get("count")))
        if item_id and "translated" in item:
            translations[item_id] = item["translated"]
    
    with ZipFile(file_path, 'r') as docx:
        document_xml = docx.read('word/document.xml')

    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)
    
    block_elements = document_tree.xpath('.//*[self::w:p or self::w:tbl]', namespaces=namespaces)

    # Handle paragraphs and table cells
    for item in original_data:
        if item["type"] == "paragraph":
            item_id = str(item.get("id", item.get("count")))
            translated_text = translations.get(item_id)
            
            if not translated_text:
                app_logger.warning(f"No translation found for paragraph ID {item_id}")
                continue
                
            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
            
            try:
                element_index = item.get("element_index")
                if element_index is None or element_index >= len(block_elements):
                    app_logger.error(f"Invalid element index: {element_index}")
                    continue
                    
                paragraph = block_elements[element_index]
                
                if paragraph.tag.split('}')[-1] != 'p':
                    app_logger.error(f"Element at index {element_index} is not a paragraph")
                    continue
                
                if item.get("has_numbering", False):
                    update_paragraph_text(paragraph, translated_text, namespaces, item.get("numbering_text", ""))
                else:
                    update_paragraph_text(paragraph, translated_text, namespaces)
                    
            except (IndexError, TypeError) as e:
                app_logger.error(f"Error finding paragraph with index {item.get('element_index')}: {e}")
                
        elif item["type"] == "table_cell":
            item_id = str(item.get("id", item.get("count")))
            translated_text = translations.get(item_id)
            
            if not translated_text:
                app_logger.warning(f"No translation found for table cell ID {item_id}")
                continue
                
            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
            
            try:
                table_index = item.get("table_index")
                if table_index is None or table_index >= len(block_elements):
                    app_logger.error(f"Invalid table index: {table_index}")
                    continue
                
                table = block_elements[table_index]
                
                if table.tag.split('}')[-1] != 'tbl':
                    app_logger.error(f"Element at index {table_index} is not a table")
                    continue
                
                row_idx = item.get("row")
                col_idx = item.get("col")
                
                rows = table.xpath('.//w:tr', namespaces=namespaces)
                if row_idx >= len(rows):
                    app_logger.error(f"Row index {row_idx} out of bounds")
                    continue
                    
                row = rows[row_idx]
                cells = row.xpath('.//w:tc', namespaces=namespaces)
                
                if col_idx >= len(cells):
                    app_logger.error(f"Column index {col_idx} out of bounds")
                    continue
                    
                cell = cells[col_idx]
                
                cell_paragraphs = cell.xpath('.//w:p', namespaces=namespaces)
                
                if cell_paragraphs:
                    first_paragraph = cell_paragraphs[0]
                    
                    # Clear all text in cell paragraphs
                    for p in cell_paragraphs:
                        runs = p.xpath('.//w:r', namespaces=namespaces)
                        for run in runs:
                            text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                            for text_node in text_nodes:
                                text_node.text = ""
                    
                    if "\n" in translated_text:
                        paragraph_texts = translated_text.split("\n")
                        
                        for i, p_text in enumerate(paragraph_texts):
                            if i < len(cell_paragraphs):
                                update_paragraph_text(cell_paragraphs[i], p_text, namespaces)
                            else:
                                new_p = etree.SubElement(cell, f"{{{namespaces['w']}}}p")
                                new_run = etree.SubElement(new_p, f"{{{namespaces['w']}}}r")
                                new_text = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                                new_text.text = p_text
                    else:
                        update_paragraph_text(first_paragraph, translated_text, namespaces)
                else:
                    new_p = etree.SubElement(cell, f"{{{namespaces['w']}}}p")
                    new_run = etree.SubElement(new_p, f"{{{namespaces['w']}}}r")
                    new_text = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                    new_text.text = translated_text
                    
            except (IndexError, TypeError) as e:
                app_logger.error(f"Error finding table cell: {e}")

    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    temp_word_folder = os.path.join(temp_folder, "word")
    os.makedirs(temp_word_folder, exist_ok=True)
    modified_doc_path = os.path.join(temp_word_folder, "document.xml")

    with open(modified_doc_path, "wb") as modified_doc:
        modified_doc.write(etree.tostring(document_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.docx")

    with ZipFile(file_path, 'r') as original_doc:
        with ZipFile(result_path, 'w') as new_doc:
            for item in original_doc.infolist():
                if item.filename != 'word/document.xml':
                    new_doc.writestr(item, original_doc.read(item.filename))
            new_doc.write(modified_doc_path, 'word/document.xml')

    app_logger.info(f"Translated Word document saved to: {result_path}")
    return result_path


def update_paragraph_text(paragraph, new_text, namespaces, numbering_text=""):
    runs = paragraph.xpath('.//w:r', namespaces=namespaces)
    
    if not runs:
        new_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
        new_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
        new_text_node.text = numbering_text + new_text
        return
    
    # Get all text nodes
    text_nodes = []
    for run in runs:
        run_text_nodes = run.xpath('.//w:t', namespaces=namespaces)
        text_nodes.extend(run_text_nodes)
    
    # Clear all existing text
    for text_node in text_nodes:
        text_node.text = ""
    
    # Handle numbering text if present
    if numbering_text:
        if text_nodes:
            text_nodes[0].text = numbering_text
            if len(text_nodes) > 1:
                text_nodes[1].text = new_text
            else:
                # Add new text node if only one exists
                new_text_node = etree.SubElement(runs[-1], f"{{{namespaces['w']}}}t")
                new_text_node.text = new_text
        else:
            # No text nodes found, create new ones
            new_text_node = etree.SubElement(runs[0], f"{{{namespaces['w']}}}t")
            new_text_node.text = numbering_text + new_text
    else:
        # No numbering, just add the text to first node
        if text_nodes:
            text_nodes[0].text = new_text
        else:
            new_text_node = etree.SubElement(runs[0], f"{{{namespaces['w']}}}t")
            new_text_node.text = new_text


def update_json_structure_after_translation(original_json_path, translated_json_path):
    with open(original_json_path, "r", encoding="utf-8") as orig_file:
        original_data = json.load(orig_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as trans_file:
        translated_data = json.load(trans_file)
    
    translations_by_id = {}
    for item in translated_data:
        if "translated" in item:
            item_id = str(item.get("id", item.get("count")))
            if item_id:
                translations_by_id[item_id] = item["translated"]
    
    restructured_data = []
    for item in original_data:
        item_id = str(item.get("id", item.get("count")))
        if item_id in translations_by_id:
            restructured_data.append({
                "id": item.get("id"),
                "count": item.get("count"),
                "type": item["type"],
                "translated": translations_by_id[item_id]
            })
    
    with open(translated_json_path, "w", encoding="utf-8") as outfile:
        json.dump(restructured_data, outfile, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Updated translation JSON structure to match original: {translated_json_path}")
    return translated_json_path