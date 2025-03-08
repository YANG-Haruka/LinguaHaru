import json
import os
from lxml import etree
from zipfile import ZipFile
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_word_content_to_json(file_path):
    """
    Extract all text content from a Word document (DOCX) using XML parsing, including paragraphs and tables.
    """
    # Open the Word document as a ZIP archive
    with ZipFile(file_path, 'r') as docx:
        document_xml = docx.read('word/document.xml')

    # Parse the main document XML
    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)

    content_data = []
    count = 0

    # Get paragraph elements
    paragraphs = document_tree.xpath('.//w:p', namespaces=namespaces)
    for paragraph in paragraphs:
        paragraph_text = ""
        # Get all text runs in the paragraph
        runs = paragraph.xpath('.//w:r', namespaces=namespaces)
        for run in runs:
            # Get all text nodes in the run
            text_nodes = run.xpath('.//w:t', namespaces=namespaces)
            for text_node in text_nodes:
                # Preserve original text, don't use strip()
                text_value = text_node.text if text_node.text else ""
                paragraph_text += text_value
        
        # Process complete paragraph text
        if paragraph_text and should_translate(paragraph_text):
            count += 1
            # Replace line breaks for display
            paragraph_text = paragraph_text.replace("\n", "␊").replace("\r", "␍")
            content_data.append({
                "count": count,
                "type": "paragraph",
                "value": paragraph_text
            })

    # Process table content
    tables = document_tree.xpath('.//w:tbl', namespaces=namespaces)
    for table in tables:
        rows = table.xpath('.//w:tr', namespaces=namespaces)
        for row in rows:
            cells = row.xpath('.//w:tc', namespaces=namespaces)
            for cell in cells:
                cell_text = ""
                # Get all paragraphs in the cell
                cell_paragraphs = cell.xpath('.//w:p', namespaces=namespaces)
                for cell_paragraph in cell_paragraphs:
                    # Process all text runs in the cell paragraph
                    cell_runs = cell_paragraph.xpath('.//w:r', namespaces=namespaces)
                    for cell_run in cell_runs:
                        cell_text_nodes = cell_run.xpath('.//w:t', namespaces=namespaces)
                        for cell_text_node in cell_text_nodes:
                            cell_text += cell_text_node.text if cell_text_node.text else ""
                    cell_text += " "  # Add space between paragraphs
                
                if cell_text.strip() and should_translate(cell_text.strip()):
                    count += 1
                    cell_text = cell_text.replace("\n", "␊").replace("\r", "␍")
                    content_data.append({
                        "count": count,
                        "type": "table_cell",
                        "value": cell_text.strip()
                    })

    # Save content to JSON
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    return json_path


def write_translated_content_to_word(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to the Word document while preserving the format and structure.
    """
    # Load translated JSON
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    # Create a mapping of translations
    translations = {str(item["count"]): item["translated"] for item in translated_data}
    
    # Open the Word document as a ZIP archive
    with ZipFile(file_path, 'r') as docx:
        # Read main document XML as bytes
        document_xml = docx.read('word/document.xml')

    # Parse the main document XML
    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)

    # Process paragraphs
    count = 0
    paragraphs = document_tree.xpath('.//w:p', namespaces=namespaces)
    for paragraph in paragraphs:
        # Extract the original text to compare
        original_text = ""
        runs = paragraph.xpath('.//w:r', namespaces=namespaces)
        for run in runs:
            text_nodes = run.xpath('.//w:t', namespaces=namespaces)
            for text_node in text_nodes:
                original_text += text_node.text if text_node.text else ""
        
        # Skip empty paragraphs
        if not original_text or not should_translate(original_text):
            continue
            
        # This paragraph should be translated
        count += 1
        translated_text = translations.get(str(count))
        
        if translated_text:
            # Convert special characters back
            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
            
            # Clear existing text nodes
            for run in runs:
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.text = ""
            
            # Add translated text to the first text node if exists
            first_text_node = paragraph.xpath('.//w:t', namespaces=namespaces)
            if first_text_node:
                first_text_node[0].text = translated_text
            else:
                # If no text nodes, create one
                first_run = paragraph.xpath('.//w:r', namespaces=namespaces)
                if first_run:
                    # Create a new text node
                    new_text = etree.SubElement(first_run[0], f"{{{namespaces['w']}}}t")
                    new_text.text = translated_text
                else:
                    # Create a new run and text node
                    new_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                    new_text = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                    new_text.text = translated_text

    # Process tables
    tables = document_tree.xpath('.//w:tbl', namespaces=namespaces)
    for table in tables:
        rows = table.xpath('.//w:tr', namespaces=namespaces)
        for row in rows:
            cells = row.xpath('.//w:tc', namespaces=namespaces)
            for cell in cells:
                cell_paragraphs = cell.xpath('.//w:p', namespaces=namespaces)
                
                # Extract original cell text
                cell_text = ""
                for cell_para in cell_paragraphs:
                    cell_runs = cell_para.xpath('.//w:r', namespaces=namespaces)
                    for cell_run in cell_runs:
                        cell_text_nodes = cell_run.xpath('.//w:t', namespaces=namespaces)
                        for cell_text_node in cell_text_nodes:
                            cell_text += cell_text_node.text if cell_text_node.text else ""
                    cell_text += " "  # Add space between paragraphs
                
                cell_text = cell_text.strip()
                if not cell_text or not should_translate(cell_text):
                    continue
                    
                # This cell should be translated
                count += 1
                translated_cell = translations.get(str(count))
                
                if translated_cell:
                    # Convert special characters back
                    translated_cell = translated_cell.replace("␊", "\n").replace("␍", "\r")
                    
                    # Clear existing text in cell
                    for cell_para in cell_paragraphs:
                        cell_runs = cell_para.xpath('.//w:r', namespaces=namespaces)
                        for cell_run in cell_runs:
                            cell_text_nodes = cell_run.xpath('.//w:t', namespaces=namespaces)
                            for cell_text_node in cell_text_nodes:
                                cell_text_node.text = ""
                    
                    # Add translated text to first text node in cell
                    first_cell_text = cell.xpath('.//w:t', namespaces=namespaces)
                    if first_cell_text:
                        first_cell_text[0].text = translated_cell
                    else:
                        # If no text nodes in cell, create one
                        if cell_paragraphs:
                            first_cell_run = cell_paragraphs[0].xpath('.//w:r', namespaces=namespaces)
                            if first_cell_run:
                                new_text = etree.SubElement(first_cell_run[0], f"{{{namespaces['w']}}}t")
                                new_text.text = translated_cell
                            else:
                                new_run = etree.SubElement(cell_paragraphs[0], f"{{{namespaces['w']}}}r")
                                new_text = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                                new_text.text = translated_cell
                        else:
                            # Create new paragraph, run and text node
                            new_para = etree.SubElement(cell, f"{{{namespaces['w']}}}p")
                            new_run = etree.SubElement(new_para, f"{{{namespaces['w']}}}r")
                            new_text = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                            new_text.text = translated_cell

    # Save the modified XML back to the Word document
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    temp_word_folder = os.path.join(temp_folder, "word")
    os.makedirs(temp_word_folder, exist_ok=True)
    modified_doc_path = os.path.join(temp_word_folder, "document.xml")

    with open(modified_doc_path, "wb") as modified_doc:
        modified_doc.write(etree.tostring(document_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    # Create a new Word document with the modified content
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.docx")

    with ZipFile(file_path, 'r') as original_doc:
        with ZipFile(result_path, 'w') as new_doc:
            # Copy all files from the original Word document to the new document
            for item in original_doc.infolist():
                if item.filename != 'word/document.xml':
                    new_doc.writestr(item, original_doc.read(item.filename))
            # Write the modified document.xml
            new_doc.write(modified_doc_path, 'word/document.xml')

    app_logger.info(f"Translated Word document saved to: {result_path}")
    return result_path